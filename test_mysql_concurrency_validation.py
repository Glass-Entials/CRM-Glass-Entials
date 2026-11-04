"""
test_mysql_concurrency_validation.py
====================================
Validates MySQL 8.0.42 InnoDB genuine multi-threaded concurrency,
durable debit idempotency, and failure recovery semantics across all
scenarios specified in Phase 5.4.1.

Requires DATABASE_URL in environment. No credentials hardcoded.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta

from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from model import (
    db,
    Organization,
    Plan,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    SaaSBillingPeriodReservation,
    BillingPeriodReservationStatus,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
)
from services.billing.providers.phonepe_autopay_v2_provider import (
    PhonePeAutoPayV2Provider,
    _compute_billing_period_key,
)


class TestMySQLConcurrencyValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mysql_url = os.environ.get("DATABASE_URL")
        if not mysql_url:
            raise unittest.SkipTest(
                "DATABASE_URL environment variable is required to execute live MySQL concurrency tests."
            )

        cls.app = Flask(__name__)
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = mysql_url
        cls.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        cls.app.config["TESTING"] = True
        cls.app.config["PHONEPE_ENV"] = "UAT"
        cls.app.config["PHONEPE_MERCHANT_ID"] = "PGTESTPAYUAT"
        cls.app.config["PHONEPE_CLIENT_ID"] = "test_client_id"
        cls.app.config["PHONEPE_CLIENT_SECRET"] = "test_client_secret"
        cls.app.config["PHONEPE_CLIENT_VERSION"] = 1

        db.init_app(cls.app)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

        # Independent sessionmaker for genuine multi-threaded concurrency tests
        cls.engine = create_engine(mysql_url, pool_pre_ping=True)
        cls.SessionFactory = sessionmaker(bind=cls.engine, expire_on_commit=False)

        cls.provider = PhonePeAutoPayV2Provider()

        # Seed isolated test tenant and plan
        cls.org_id = 98765
        cls.sub_id = 87654

        cls._cleanup()

        org = Organization.query.get(cls.org_id)
        if not org:
            org = Organization(id=cls.org_id, name="MySQL Concurrency Tenant", unique_code="MYSQLCONC")
            db.session.add(org)

        plan = Plan.query.get(999)
        if not plan:
            plan = Plan(id=999, name="Test Plan", display_name="Test", monthly_price_paise=100000, yearly_price_paise=1000000)
            db.session.add(plan)
        db.session.commit()

        # Create base test subscription
        sub = SaaSSubscription(
            id=cls.sub_id,
            organization_id=cls.org_id,
            plan_id=999,
            payment_provider="phonepe_v2",
            provider_subscription_id=f"MSUB_MYSQL_{cls.sub_id}",
            status=SaaSSubscriptionStatus.ACTIVE,
            billing_cycle=SaaSBillingCycle.MONTHLY,
            amount_paise=118000,
            current_period_start=datetime(2026, 11, 1, 0, 0, 0),
            current_period_end=datetime(2026, 12, 1, 0, 0, 0),
        )
        db.session.add(sub)
        db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        sub = SaaSSubscription.query.get(cls.sub_id)
        if sub:
            db.session.delete(sub)
        org = Organization.query.get(cls.org_id)
        if org:
            db.session.delete(org)
        plan = Plan.query.get(999)
        if plan:
            db.session.delete(plan)
        db.session.commit()
        cls.ctx.pop()
        cls.engine.dispose()

    @classmethod
    def _cleanup(cls):
        db.session.query(SaaSBillingPeriodReservation).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSRedemptionAttempt).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSPreDebitNotification).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSMandate).filter_by(organization_id=cls.org_id).delete()
        db.session.commit()

    def setUp(self):
        self._cleanup()
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.claim_token = None
        sub.claim_expires_at = None
        sub.current_period_start = datetime(2026, 11, 1, 0, 0, 0)
        sub.current_period_end = datetime(2026, 12, 1, 0, 0, 0)
        db.session.commit()

    # ── Scenario 1: Genuine Concurrency - Simultaneous Competing Workers ──────

    def test_scenario_1_simultaneous_competing_workers_blocked_by_database_constraint(self):
        """
        Genuine concurrency test: Two synchronized threads running on independent
        SQLAlchemy sessions and distinct physical database connections simultaneously
        attempt to reserve the same billing period with different merchant order IDs.
        Asserts exactly one reservation is created, and the losing worker cannot debit.
        """
        bpk = f"{self.sub_id}_2026-11"
        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, order_id: str):
            session = self.SessionFactory()
            try:
                # Synchronize threads to release at the exact same instant
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id=order_id,
                    session=session,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1", "ORD_CONC_ALPHA"))
        t2 = threading.Thread(target=worker, args=("worker_2", "ORD_CONC_BETA"))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Exactly one worker succeeded and one failed
        self.assertTrue((w1_ok and not w2_ok) or (w2_ok and not w1_ok))

        # Losing worker is blocked
        losing_err = w2_err if w1_ok else w1_err
        self.assertIn("already reserved", losing_err)
        self.assertIn("Duplicate debit prevented", losing_err)

        # Direct database verification on MySQL 8 InnoDB
        verify_session = self.SessionFactory()
        try:
            records = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).all()
            self.assertEqual(len(records), 1)
            winning_order = "ORD_CONC_ALPHA" if w1_ok else "ORD_CONC_BETA"
            self.assertEqual(records[0].merchant_order_id, winning_order)
            self.assertEqual(records[0].status, BillingPeriodReservationStatus.RESERVED.value)
        finally:
            verify_session.close()

    # ── Scenario 1b: Genuine Concurrency - Simultaneous Retries with Same ID ───

    def test_scenario_1b_simultaneous_idempotent_retries_same_order_id(self):
        """
        Genuine concurrency test: Two synchronized threads simultaneously attempt
        to reserve the same billing period using the exact SAME merchant order ID.
        Both workers succeed idempotently, and exactly one reservation row exists in MySQL InnoDB.
        """
        bpk = f"{self.sub_id}_2026-11"
        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str):
            session = self.SessionFactory()
            try:
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id="ORD_IDEMPOTENT_SAME_ID",
                    session=session,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1",))
        t2 = threading.Thread(target=worker, args=("worker_2",))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Both workers succeed idempotently
        self.assertTrue(w1_ok)
        self.assertTrue(w2_ok)
        self.assertIsNone(w1_err)
        self.assertIsNone(w2_err)

        # Direct verification against MySQL InnoDB: exactly 1 reservation row exists
        verify_session = self.SessionFactory()
        try:
            records = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).all()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].merchant_order_id, "ORD_IDEMPOTENT_SAME_ID")
        finally:
            verify_session.close()

    # ── Scenario 2: Lease Expiry with Reservation Intact ───────────────────────

    def test_scenario_2_lease_expiry_reservation_blocks_competing_order(self):
        """
        Scenario 2: Lease expires on SaaSSubscription while Worker 1 is still processing.
        Even if Worker 2 acquires the subscription claim lease, the durable
        SaaSBillingPeriodReservation table blocks Worker 2 from initiating a competing order.
        """
        bpk = f"{self.sub_id}_2026-11"

        # Worker 1 acquires subscription claim lease and reserves billing period
        token1 = self.provider._acquire_subscription_claim(self.sub_id, self.org_id)
        self.assertIsNotNone(token1)

        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_WORKER_1_LEASE_EXPIRE"
        )
        self.assertTrue(ok1)

        # Simulate Worker 1 lease expiry in MySQL
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.claim_expires_at = datetime.utcnow() - timedelta(seconds=10)
        db.session.commit()

        # Worker 2 comes along and is able to acquire the subscription claim (since lease expired)
        token2 = self.provider._acquire_subscription_claim(self.sub_id, self.org_id)
        self.assertIsNotNone(token2)
        self.assertNotEqual(token1, token2)

        # But Worker 2 attempts to reserve with its own order ID:
        ok2, err2, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_WORKER_2_AFTER_LEASE"
        )
        # Durable reservation protects the period!
        self.assertFalse(ok2)
        self.assertIn("already reserved", err2)

    # ── Scenario 3: Duplicate Reservation After Successful Debit ───────────────

    def test_scenario_3_duplicate_reservation_after_success_blocked(self):
        """
        Scenario 3: Duplicate billing-period reservation after a successful debit.
        COMPLETED status in reservation table permanently blocks new reservations for period.
        """
        bpk = f"{self.sub_id}_2026-11"

        # Worker 1 reserves and completes debit
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_SUCCESSFUL_001"
        )
        self.assertTrue(ok1)
        res1.status = BillingPeriodReservationStatus.COMPLETED.value
        db.session.commit()

        # Later worker attempts another debit for the same period with a new order ID
        ok2, err2, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_DUPLICATE_AFTER_SUCCESS"
        )
        self.assertFalse(ok2)
        self.assertIn("successful debit already exists", err2)

    # ── Scenario 4: Duplicate Reservation After Ambiguous Response ─────────────

    def test_scenario_4_duplicate_reservation_after_ambiguous_blocked(self):
        """
        Scenario 4: Ambiguous gateway response leaves reservation in PENDING_RECONCILIATION.
        A competing or scheduled worker cannot start a replacement debit until resolved.
        """
        bpk = f"{self.sub_id}_2026-11"

        # Worker 1 reserves and encounters an ambiguous outcome
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_AMBIGUOUS_001"
        )
        self.assertTrue(ok1)
        res1.status = BillingPeriodReservationStatus.PENDING_RECONCILIATION.value
        db.session.commit()

        # Worker 2 attempts replacement debit for the period
        ok2, err2, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_REPLACEMENT_FORBIDDEN"
        )
        self.assertFalse(ok2)
        self.assertIn("already reserved by order ORD_AMBIGUOUS_001", err2)
        self.assertIn("PENDING_RECONCILIATION", err2)

    # ── Scenario 5: Legitimate Reservation for Next Billing Period ────────────

    def test_scenario_5_legitimate_renewal_next_billing_period_succeeds(self):
        """
        Scenario 5: When the subscription advances to the next billing period,
        a new billing_period_key is generated and can be cleanly reserved.
        """
        bpk_nov = f"{self.sub_id}_2026-11"
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk_nov, "ORD_NOV_PAID"
        )
        self.assertTrue(ok1)
        res1.status = BillingPeriodReservationStatus.COMPLETED.value
        db.session.commit()

        # Subscription advances to December 2026
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.current_period_start = datetime(2026, 12, 1, 0, 0, 0)
        sub.current_period_end = datetime(2027, 1, 1, 0, 0, 0)
        db.session.commit()

        bpk_dec = _compute_billing_period_key(sub.id, sub.current_period_start)
        self.assertEqual(bpk_dec, f"{self.sub_id}_2026-12")

        # Worker reserves December 2026
        ok2, err2, res2 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk_dec, "ORD_DEC_RENEWAL"
        )
        self.assertTrue(ok2)
        self.assertIsNone(err2)
        self.assertEqual(res2.billing_period_key, bpk_dec)
        self.assertEqual(res2.status, BillingPeriodReservationStatus.RESERVED.value)

        # Both records exist peacefully in MySQL InnoDB
        nov_rec = SaaSBillingPeriodReservation.query.filter_by(billing_period_key=bpk_nov).first()
        dec_rec = SaaSBillingPeriodReservation.query.filter_by(billing_period_key=bpk_dec).first()
        self.assertEqual(nov_rec.status, "COMPLETED")
        self.assertEqual(dec_rec.status, "RESERVED")

    # ── Scenario 6: Worker Crash and Recovery ──────────────────────────────────

    def test_scenario_6_worker_crash_and_recovery_same_order_id(self):
        """
        Scenario 6: Worker crashes mid-flight after reserving.
        Recovery worker re-tries with the SAME merchant_order_id.
        Durable reservation recognizes the same order ID and allows safe resumption.
        """
        bpk = f"{self.sub_id}_2026-11"

        # Worker crashes after reservation
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_CRASH_RECOVER_001"
        )
        self.assertTrue(ok1)

        # Recovery worker executes retry with the identical order ID
        ok_retry, err_retry, res_retry = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_CRASH_RECOVER_001"
        )
        self.assertTrue(ok_retry)
        self.assertIsNone(err_retry)
        self.assertEqual(res_retry.id, res1.id)
        self.assertEqual(res_retry.merchant_order_id, "ORD_CRASH_RECOVER_001")

    # ── Scenario 7: Claim Release Failure ─────────────────────────────────────

    def test_scenario_7_claim_release_failure_ttl_protection(self):
        """
        Scenario 7: Worker acquires lease claim, but fails to release it (or network fails).
        After CLAIM_LEASE_SECONDS elapses, another worker is able to acquire claim,
        while durable SaaSBillingPeriodReservation continues to protect financial integrity.
        """
        bpk = f"{self.sub_id}_2026-11"

        # Worker 1 acquires claim and reserves
        token1 = self.provider._acquire_subscription_claim(self.sub_id, self.org_id)
        self.assertIsNotNone(token1)
        ok1, _, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_UNRELEASED_CLAIM"
        )
        self.assertTrue(ok1)

        # Worker 2 immediately tries to acquire claim while lease is active
        token2_immediate = self.provider._acquire_subscription_claim(self.sub_id, self.org_id)
        self.assertIsNone(token2_immediate)  # Denied!

        # Simulate TTL passage in MySQL
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.claim_expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        # Now Worker 2 tries to acquire claim after TTL: succeeds
        token2_after_ttl = self.provider._acquire_subscription_claim(self.sub_id, self.org_id)
        self.assertIsNotNone(token2_after_ttl)
        self.assertNotEqual(token1, token2_after_ttl)

        # But duplicate order ID for the same billing period is still rejected by reservation!
        ok2, err2, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_COMPETING_AFTER_TTL"
        )
        self.assertFalse(ok2)
        self.assertIn("already reserved", err2)


if __name__ == "__main__":
    unittest.main()

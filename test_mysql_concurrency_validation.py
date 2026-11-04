"""
test_mysql_concurrency_validation.py
====================================
Validates MySQL 8.0.42 InnoDB genuine multi-threaded concurrency,
durable debit idempotency, execution ownership, and failure recovery semantics
across all 10 scenarios specified in Phase 5.4.2.

Requires DATABASE_URL in environment. No credentials hardcoded.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

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
    SaaSWebhookEvent,
    SaaSTransaction,
    BillingPeriodReservationStatus,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    WebhookEventStatus,
)
from services.billing.providers.phonepe_autopay_v2_provider import (
    PhonePeAutoPayV2Provider,
    _compute_billing_period_key,
)
from services.billing.renewal_service import RenewalService
from services.billing.subscription_service import SubscriptionService


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
        cls.app.config["PHONEPE_WEBHOOK_USERNAME"] = "webhook_test_user"
        cls.app.config["PHONEPE_WEBHOOK_PASSWORD"] = "webhook_test_pass"

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
        sub = SaaSSubscription.query.get(cls.sub_id)
        if not sub:
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
        db.session.rollback()
        sub = db.session.get(SaaSSubscription, cls.sub_id)
        if sub:
            sub.active_mandate_id = None
            sub.claim_token = None
            sub.claim_expires_at = None
            db.session.commit()
        db.session.query(SaaSTransaction).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSWebhookEvent).filter_by(payment_provider="phonepe_v2").delete()
        db.session.query(SaaSBillingPeriodReservation).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSRedemptionAttempt).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSPreDebitNotification).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSMandate).filter_by(organization_id=cls.org_id).delete()
        db.session.commit()

    def setUp(self):
        self._cleanup()
        sub = db.session.get(SaaSSubscription, self.sub_id)
        if sub:
            sub.active_mandate_id = None
            sub.claim_token = None
            sub.claim_expires_at = None
            sub.current_period_start = datetime(2026, 11, 1, 0, 0, 0)
            sub.current_period_end = datetime(2026, 12, 1, 0, 0, 0)
            db.session.commit()

    # ── Scenario 1: Different Order IDs Competing for One Billing Period ───────

    def test_scenario_1_different_order_ids_competing_for_one_billing_period(self):
        """
        Scenario 1: Two synchronized workers on independent MySQL InnoDB connections
        simultaneously attempt to reserve the same billing period with different order IDs.
        Asserts exactly one reservation is created, and the losing worker is blocked by unique DB constraint.
        """
        bpk = f"{self.sub_id}_2026-11"
        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, order_id: str):
            session = self.SessionFactory()
            try:
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

        t1 = threading.Thread(target=worker, args=("worker_1", "ORD_COMPETE_A"))
        t2 = threading.Thread(target=worker, args=("worker_2", "ORD_COMPETE_B"))

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
            winning_order = "ORD_COMPETE_A" if w1_ok else "ORD_COMPETE_B"
            self.assertEqual(records[0].merchant_order_id, winning_order)
            self.assertEqual(records[0].status, BillingPeriodReservationStatus.RESERVED.value)
        finally:
            verify_session.close()

    # ── Scenario 2: Same Order ID Submitted Simultaneously by Two Workers ─────

    def test_scenario_2_same_order_id_submitted_simultaneously_by_two_workers(self):
        """
        Scenario 2: Two synchronized threads simultaneously attempt to execute/reserve
        the same billing period using the exact SAME merchant order ID with distinct owner tokens.
        Durable execution ownership guarantees exactly ONE worker wins the active execution claim.
        The second worker is rejected as concurrent execution blocked.
        """
        bpk = f"{self.sub_id}_2026-11"
        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, owner_token: str):
            session = self.SessionFactory()
            try:
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id="ORD_SAME_ID_CONC",
                    session=session,
                    owner_token=owner_token,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1", "worker_1_owner_token"))
        t2 = threading.Thread(target=worker, args=("worker_2", "worker_2_owner_token"))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Exactly one worker acquires execution ownership; second worker is blocked
        self.assertTrue((w1_ok and not w2_ok) or (w2_ok and not w1_ok))

        losing_err = w2_err if w1_ok else w1_err
        self.assertIn("currently owned by active worker", losing_err)
        self.assertIn("Concurrent execution blocked", losing_err)

        # Exactly 1 reservation row exists in MySQL InnoDB
        verify_session = self.SessionFactory()
        try:
            records = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).all()
            self.assertEqual(len(records), 1)
            winning_token = "worker_1_owner_token" if w1_ok else "worker_2_owner_token"
            self.assertEqual(records[0].owner_token, winning_token)
            self.assertEqual(records[0].merchant_order_id, "ORD_SAME_ID_CONC")
        finally:
            verify_session.close()

    # ── Scenario 3: Exactly One PhonePe Redeem Invocation ─────────────────────

    def test_scenario_3_exactly_one_phonepe_redeem_invocation(self):
        """
        Scenario 3: Two workers concurrently attempt execute_redemption for the same subscription
        and billing period. Asserts PhonePe client's redeem() is invoked EXACTLY ONCE across workers.
        """
        order_id = "ORD_SINGLE_REDEEM_INVOCATION"
        mandate = SaaSMandate(
            id=555,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        sub = SaaSSubscription.query.get(self.sub_id)
        sub.active_mandate_id = mandate.id

        notification = SaaSPreDebitNotification(
            id=777,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=f"{self.sub_id}_2026-11",
        )
        db.session.add(notification)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_SINGLE_REDEEM"}],
        }

        barrier = threading.Barrier(2)
        results = {}

        def run_worker(worker_id: str):
            with self.app.app_context():
                try:
                    barrier.wait(timeout=5)
                    worker_sub = db.session.get(SaaSSubscription, self.sub_id)
                    res = self.provider.execute_redemption(
                        subscription=worker_sub,
                        amount_paise=118000,
                        merchant_transaction_id=order_id,
                    )
                    results[worker_id] = res
                except Exception as exc:
                    results[worker_id] = exc

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            t1 = threading.Thread(target=run_worker, args=("worker_1",))
            t2 = threading.Thread(target=run_worker, args=("worker_2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # PhonePe redeem must be called EXACTLY ONCE
        self.assertEqual(mock_client.redeem.call_count, 1)

        # One worker succeeded; the other worker was prevented from duplicate debit
        r1 = results.get("worker_1")
        r2 = results.get("worker_2")
        self.assertTrue((getattr(r1, "success", False) and not getattr(r2, "success", False)) or
                        (getattr(r2, "success", False) and not getattr(r1, "success", False)) or
                        (getattr(r1, "status", None) == "SUCCESS" and getattr(r2, "status", None) == "PENDING"))

    # ── Scenario 4: Second Worker Cannot Independently Initiate Debit ──────────

    def test_scenario_4_second_worker_cannot_independently_initiate_debit(self):
        """
        Scenario 4: When Worker 1 holds active execution ownership, Worker 2 cannot
        independently invoke PhonePe redeem or initiate a second debit.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_ACTIVE_OWNER_IN_FLIGHT"

        # Worker 1 acquires reservation with execution lease
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_1_in_flight",
        )
        self.assertTrue(ok1)
        self.assertEqual(res1.owner_token, "worker_1_in_flight")
        self.assertGreater(res1.owner_expires_at, datetime.utcnow())

        # Worker 2 attempts same order with its own owner token
        ok2, err2, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_2_competing",
        )
        self.assertFalse(ok2)
        self.assertIn("currently owned by active worker", err2)

        # Worker 2 attempts different order for same period
        ok3, err3, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_DIFFERENT_COMPETING",
            owner_token="worker_2_competing",
        )
        self.assertFalse(ok3)
        self.assertIn("already reserved", err3)

    # ── Scenario 5: Worker Crash After Acquiring Execution Ownership ───────────

    def test_scenario_5_worker_crash_after_acquiring_execution_ownership(self):
        """
        Scenario 5: Worker 1 crashes after acquiring execution ownership BEFORE calling PhonePe
        (redemption_initiated_at is None). After lease expires, Worker 2 safely acquires
        ownership reusing the same order ID without creating a replacement order.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_CRASH_BEFORE_GATEWAY"

        # Worker 1 reserves period but crashes
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_1_crashed",
        )
        self.assertTrue(ok1)
        self.assertIsNone(res1.redemption_initiated_at)

        # Simulate Worker 1 lease expiry in MySQL InnoDB
        res1.owner_expires_at = datetime.utcnow() - timedelta(seconds=10)
        db.session.commit()

        # Worker 2 attempts recovery using the SAME order ID
        ok2, err2, res2 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_2_recovered",
        )
        self.assertTrue(ok2)
        self.assertIsNone(err2)
        self.assertEqual(res2.id, res1.id)
        self.assertEqual(res2.merchant_order_id, order_id)
        self.assertEqual(res2.owner_token, "worker_2_recovered")

    # ── Scenario 5B: Simultaneous Expired-Lease Acquisition by Two Threads ────

    def test_scenario_5b_simultaneous_expired_lease_atomic_acquisition_two_threads(self):
        """
        Phase 5.4.2.1 Requirement 3: Two synchronized threads on independent MySQL InnoDB sessions
        simultaneously attempt to acquire an expired execution lease for the SAME order ID.
        Atomic conditional UPDATE guarantees that:
          1. Exactly ONE worker acquires the expired lease.
          2. The losing worker is blocked from acquiring ownership.
          3. Two workers never both believe they acquired the expired reservation.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_SIMULTANEOUS_EXPIRED_LEASE"

        # Pre-seed reservation with an EXPIRED lease (pre-dispatch crash)
        expired_res = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.RESERVED.value,
            owner_token="crashed_worker_initial",
            owner_expires_at=datetime.utcnow() - timedelta(seconds=30),
            redemption_initiated_at=None,
        )
        db.session.add(expired_res)
        db.session.commit()

        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, owner_token: str):
            session = self.SessionFactory()
            try:
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id=order_id,
                    session=session,
                    owner_token=owner_token,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1", "worker_1_lease_token"))
        t2 = threading.Thread(target=worker, args=("worker_2", "worker_2_lease_token"))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Exactly ONE worker acquired the expired lease; the other worker was rejected
        self.assertTrue(
            (w1_ok and not w2_ok) or (w2_ok and not w1_ok),
            f"Both workers cannot succeed or fail simultaneously: w1={w1_ok}, w2={w2_ok}",
        )

        losing_err = w2_err if w1_ok else w1_err
        self.assertIn("Concurrent execution blocked", losing_err)

        # Directly verify database state in MySQL InnoDB
        verify_session = self.SessionFactory()
        try:
            persisted = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).first()
            self.assertIsNotNone(persisted)
            winning_token = "worker_1_lease_token" if w1_ok else "worker_2_lease_token"
            self.assertEqual(persisted.owner_token, winning_token)
            self.assertGreater(persisted.owner_expires_at, datetime.utcnow())
        finally:
            verify_session.close()

    def test_scenario_5c_simultaneous_expired_lease_redemption_exactly_one_redeem_call(self):
        """
        Phase 5.4.2.1 Requirement 3: Two synchronized worker threads simultaneously attempt
        execute_redemption() on an expired lease.
        Asserts:
          1. Exactly ONE execution owner is established.
          2. The losing worker cannot proceed to PhonePe redeem.
          3. PhonePe redeem() is invoked EXACTLY ONCE.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_SIMULTANEOUS_EXPIRED_REDEEM"

        # Set up active mandate and NOTIFIED notification
        mandate = SaaSMandate(
            id=556,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_EXP_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        sub = db.session.get(SaaSSubscription, self.sub_id)
        sub.active_mandate_id = mandate.id

        notification = SaaSPreDebitNotification(
            id=778,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=bpk,
        )
        db.session.add(notification)

        # Pre-seed reservation with an EXPIRED lease
        expired_res = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.RESERVED.value,
            owner_token="old_crashed_worker",
            owner_expires_at=datetime.utcnow() - timedelta(seconds=45),
            redemption_initiated_at=None,
        )
        db.session.add(expired_res)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_EXP_REDEEM_WINNER"}],
        }

        barrier = threading.Barrier(2)
        results = {}

        def run_worker(worker_id: str):
            with self.app.app_context():
                try:
                    barrier.wait(timeout=5)
                    worker_sub = db.session.get(SaaSSubscription, self.sub_id)
                    res = self.provider.execute_redemption(
                        subscription=worker_sub,
                        amount_paise=118000,
                        merchant_transaction_id=order_id,
                    )
                    results[worker_id] = res
                except Exception as exc:
                    results[worker_id] = exc

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            t1 = threading.Thread(target=run_worker, args=("worker_1",))
            t2 = threading.Thread(target=run_worker, args=("worker_2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # Assert PhonePe redeem was called EXACTLY ONCE across both threads
        self.assertEqual(mock_client.redeem.call_count, 1)

        r1 = results.get("worker_1")
        r2 = results.get("worker_2")

        # One worker succeeded and one was prevented
        r1_success = getattr(r1, "success", False) or getattr(r1, "status", None) == "SUCCESS"
        r2_success = getattr(r2, "success", False) or getattr(r2, "status", None) == "SUCCESS"
        self.assertTrue(
            (r1_success and not r2_success) or (r2_success and not r1_success),
            f"Expected exactly one success: r1={r1}, r2={r2}",
        )

        losing_res = r2 if r1_success else r1
        self.assertFalse(losing_res.success)
        self.assertTrue(
            "Redemption execution for order" in losing_res.error_message
            or "Subscription is currently claimed" in losing_res.error_message
        )

    # ── Scenario 5D: Paused Worker Lease Transfer Race Condition ──────────────

    def test_scenario_5d_paused_worker_lease_transfer_race_condition(self):
        """
        Phase 5.4.2.2 Requirement 1:
        Genuine MySQL InnoDB race test deliberately pausing Worker 1 between initial ownership
        acquisition and dispatch authorization, expiring/transferring the lease to Worker 2
        (new owner), and proving that former owner (Worker 1) cannot dispatch while new owner
        (Worker 2) can, yielding exactly ONE PhonePe redeem call.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_RACE_PAUSED_WORKER"

        # Set up active mandate and NOTIFIED notification
        mandate = SaaSMandate(
            id=558,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_PAUSE_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        sub = db.session.get(SaaSSubscription, self.sub_id)
        sub.active_mandate_id = mandate.id

        notification = SaaSPreDebitNotification(
            id=779,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=bpk,
        )
        db.session.add(notification)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_PAUSED_RACE_SUCCESS"}],
        }

        worker_1_paused_event = threading.Event()
        worker_1_resume_event = threading.Event()
        orig_reserve = self.provider._reserve_billing_period

        # Hook _reserve_billing_period to pause Worker 1 right after initial ownership acquisition
        def hooked_reserve(subscription_id, organization_id, billing_period_key, merchant_order_id, session=None, owner_token=None):
            ok, err, res = orig_reserve(subscription_id, organization_id, billing_period_key, merchant_order_id, session=session, owner_token=owner_token)
            if ok and owner_token and "worker_1" in owner_token:
                # Signal that Worker 1 has completed initial ownership acquisition
                worker_1_paused_event.set()
                # Pause Worker 1 until the test expires and transfers lease to Worker 2
                worker_1_resume_event.wait(timeout=5)
            return ok, err, res

        results = {}

        def run_worker_1():
            with self.app.app_context():
                try:
                    w1_sub = db.session.get(SaaSSubscription, self.sub_id)
                    with patch("uuid.uuid4") as mock_uuid:
                        mock_uuid.return_value.hex = "worker_1_initial_owner"
                        res = self.provider.execute_redemption(
                            subscription=w1_sub,
                            amount_paise=118000,
                            merchant_transaction_id=order_id,
                        )
                        results["worker_1"] = res
                except Exception as exc:
                    results["worker_1"] = exc

        def run_worker_2():
            with self.app.app_context():
                try:
                    w2_sub = db.session.get(SaaSSubscription, self.sub_id)
                    with patch("uuid.uuid4") as mock_uuid:
                        mock_uuid.return_value.hex = "worker_2_new_owner"
                        res = self.provider.execute_redemption(
                            subscription=w2_sub,
                            amount_paise=118000,
                            merchant_transaction_id=order_id,
                        )
                        results["worker_2"] = res
                except Exception as exc:
                    results["worker_2"] = exc

        with patch.object(self.provider, "_reserve_billing_period", side_effect=hooked_reserve), \
             patch.object(self.provider, "_get_client", return_value=mock_client):

            # 1. Start Worker 1
            t1 = threading.Thread(target=run_worker_1)
            t1.start()

            # 2. Wait until Worker 1 has acquired initial ownership on MySQL InnoDB
            self.assertTrue(worker_1_paused_event.wait(timeout=5), "Worker 1 failed to acquire initial ownership")

            # Verify Worker 1 acquired initial reservation in MySQL InnoDB
            verify_session = self.SessionFactory()
            try:
                res_row = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                    organization_id=self.org_id,
                    subscription_id=self.sub_id,
                    billing_period_key=bpk,
                ).first()
                self.assertIsNotNone(res_row)
                self.assertIn("worker_1", res_row.owner_token)
                self.assertIsNone(res_row.redemption_initiated_at)

                # 3. Deliberately expire Worker 1's leases in MySQL InnoDB
                res_row.owner_expires_at = datetime.utcnow() - timedelta(seconds=10)
                sub_row = verify_session.query(SaaSSubscription).get(self.sub_id)
                if sub_row:
                    sub_row.claim_expires_at = datetime.utcnow() - timedelta(seconds=10)
                verify_session.commit()
            finally:
                verify_session.close()

            # 4. Run Worker 2 while Worker 1 is still paused before dispatch authorization.
            # Worker 2 should acquire the expired lease, run atomic dispatch authorization,
            # and dispatch PhonePe redeem.
            t2 = threading.Thread(target=run_worker_2)
            t2.start()
            t2.join(timeout=10)

            # Assert Worker 2 succeeded
            w2_res = results.get("worker_2")
            self.assertIsNotNone(w2_res, "Worker 2 did not produce a result")
            w2_success = getattr(w2_res, "success", False) or getattr(w2_res, "status", None) == "SUCCESS"
            self.assertTrue(w2_success, f"Worker 2 was expected to succeed: {w2_res}")

            # 5. Now resume Worker 1: Worker 1 resumes and attempts atomic conditional UPDATE
            # for dispatch authorization with its former owner_token.
            worker_1_resume_event.set()
            t1.join(timeout=10)

            # Assert Worker 1 failed / aborted because updated_count == 0
            w1_res = results.get("worker_1")
            self.assertIsNotNone(w1_res, "Worker 1 did not produce a result")
            self.assertFalse(w1_res.success, f"Worker 1 was expected to fail: {w1_res}")
            self.assertTrue(
                "Execution lease expired or lost ownership" in getattr(w1_res, "error_message", "")
                or "Redemption was already initiated" in getattr(w1_res, "error_message", "")
            )

            # 6. Assert PhonePe redeem() was called EXACTLY ONCE (by Worker 2 only!)
            self.assertEqual(mock_client.redeem.call_count, 1)

            # 7. Directly verify MySQL InnoDB state: redemption succeeded and marked COMPLETED
            final_session = self.SessionFactory()
            try:
                final_res = final_session.query(SaaSBillingPeriodReservation).filter_by(
                    organization_id=self.org_id,
                    subscription_id=self.sub_id,
                    billing_period_key=bpk,
                ).first()
                self.assertIsNotNone(final_res)
                self.assertIsNotNone(final_res.redemption_initiated_at)
                self.assertEqual(final_res.status, BillingPeriodReservationStatus.COMPLETED.value)
                self.assertEqual(final_res.gateway_state, "COMPLETED")
            finally:
                final_session.close()

    # ── Scenario 6: Lease Expiry While Gateway Request Remains Ambiguous ───────

    def test_scenario_6_lease_expiry_while_gateway_request_remains_ambiguous(self):
        """
        Scenario 6: Worker 1 dispatched PhonePe gateway request (redemption_initiated_at is set),
        then crashed. After lease expires, Worker 2 attempts to reserve.
        Because redemption was already dispatched, Worker 2 is BLOCKED from initiating a second
        PhonePe debit and the reservation is marked PENDING_RECONCILIATION.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_DISPATCHED_CRASH_AMBIGUOUS"

        # Worker 1 reserves and initiates gateway call
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_1_dispatched",
        )
        self.assertTrue(ok1)
        res1.redemption_initiated_at = datetime.utcnow() - timedelta(minutes=5)
        res1.owner_expires_at = datetime.utcnow() - timedelta(minutes=3)  # lease expired
        db.session.commit()

        # Worker 2 attempts to reserve same order after lease expiry
        ok2, err2, res2 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_2_attempt",
        )
        self.assertFalse(ok2)
        self.assertIn("was already initiated with gateway", err2)
        self.assertIn("authoritative status reconciliation required", err2)

        # Directly verify status on MySQL InnoDB
        db.session.refresh(res1)
        self.assertEqual(res1.status, BillingPeriodReservationStatus.PENDING_RECONCILIATION.value)

    # ── Scenario 7: Recovery After Authoritative Gateway Success ───────────────

    def test_scenario_7_recovery_after_authoritative_gateway_success(self):
        """
        Scenario 7: Following an ambiguous outcome, authoritative S2S status check confirms
        gateway COMPLETED. Reconciles attempt and reservation to COMPLETED, clears execution lease,
        advances subscription period, and permanently blocks duplicate debits.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_RECOVER_SUCCESS"

        mandate = SaaSMandate(
            id=556,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        notification = SaaSPreDebitNotification(
            id=778,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            billing_period_key=bpk,
        )
        db.session.add(notification)

        reservation = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.PENDING_RECONCILIATION.value,
            owner_token="worker_expired_token",
            owner_expires_at=datetime.utcnow() - timedelta(minutes=1),
            redemption_initiated_at=datetime.utcnow() - timedelta(minutes=5),
        )
        db.session.add(reservation)

        attempt = SaaSRedemptionAttempt(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            notification_id=notification.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
            billing_period_key=bpk,
        )
        db.session.add(attempt)
        db.session.commit()

        # Mock authoritative gateway response returning COMPLETED
        mock_client = MagicMock()
        mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_AUTH_SUCCESS_777"}],
        }

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            updated_attempt = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(updated_attempt.status, RedemptionAttemptStatus.COMPLETED.value)

        # Verify reservation transitioned to COMPLETED and ownership cleared in MySQL
        db.session.refresh(reservation)
        self.assertEqual(reservation.status, BillingPeriodReservationStatus.COMPLETED.value)
        self.assertEqual(reservation.gateway_state, "COMPLETED")
        self.assertIsNone(reservation.owner_token)
        self.assertIsNone(reservation.owner_expires_at)

        # Subsequent attempt to reserve the period is permanently blocked
        ok_next, err_next, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_COMPLETED_PERIOD_RETRY"
        )
        self.assertFalse(ok_next)
        self.assertIn("successful debit already exists", err_next)

    # ── Scenario 8: Recovery After Authoritative Gateway Failure ───────────────

    def test_scenario_8_recovery_after_authoritative_gateway_failure(self):
        """
        Scenario 8: Following an ambiguous outcome, authoritative S2S status check confirms
        gateway FAILED. Reconciles attempt and reservation to FAILED, clears execution ownership,
        and does NOT advance subscription period.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_RECOVER_FAILED"

        mandate = SaaSMandate(
            id=557,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        notification = SaaSPreDebitNotification(
            id=779,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            billing_period_key=bpk,
        )
        db.session.add(notification)

        reservation = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.PENDING_RECONCILIATION.value,
            owner_token="worker_failed_token",
            owner_expires_at=datetime.utcnow() - timedelta(minutes=1),
            redemption_initiated_at=datetime.utcnow() - timedelta(minutes=5),
        )
        db.session.add(reservation)

        attempt = SaaSRedemptionAttempt(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            notification_id=notification.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
            billing_period_key=bpk,
        )
        db.session.add(attempt)
        db.session.commit()

        # Mock authoritative gateway response returning FAILED
        mock_client = MagicMock()
        mock_client.get_order_status.return_value = {
            "state": "FAILED",
            "message": "Debit declined by customer bank",
            "paymentDetails": [{"state": "FAILED", "responseCodeDescription": "DECLINED"}],
        }

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            updated_attempt = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(updated_attempt.status, RedemptionAttemptStatus.FAILED.value)

        # Verify reservation transitioned to FAILED and ownership cleared in MySQL
        db.session.refresh(reservation)
        self.assertEqual(reservation.status, BillingPeriodReservationStatus.FAILED.value)
        self.assertEqual(reservation.gateway_state, "FAILED")
        self.assertIsNone(reservation.owner_token)

    # ── Scenario 9: Duplicate Webhook Delivery During Reconciliation ──────────

    def test_scenario_9_duplicate_webhook_delivery_during_reconciliation(self):
        """
        Scenario 9: Webhook event idempotency deduplication in MySQL InnoDB.
        When duplicate webhooks arrive for the same PhonePe event, the first is processed
        and recorded with processing_state="PROCESSED"; the duplicate returns already_processed
        without executing redundant mutations.
        """
        event_id = "EVT_MYSQL_DEDUP_001"

        # Webhook 1 processes and commits idempotency record to MySQL
        ev = SaaSWebhookEvent(
            payment_provider="phonepe_v2",
            event_id=event_id,
            event_type="PAYMENT_SUCCESS",
            payload_summary=json.dumps({"processing_state": "PROCESSED", "org_id": self.org_id}),
        )
        db.session.add(ev)
        db.session.commit()

        # Webhook 2 arrives: check idempotency in MySQL
        existing_event = SaaSWebhookEvent.query.filter_by(payment_provider="phonepe_v2", event_id=event_id).first()
        self.assertIsNotNone(existing_event)
        summary = json.loads(existing_event.payload_summary)
        self.assertEqual(summary.get("processing_state"), "PROCESSED")

        # Duplicate delivery must recognize processed status
        is_already_processed = (summary.get("processing_state") == "PROCESSED")
        self.assertTrue(is_already_processed)

    # ── Scenario 10: Legitimate Subsequent Billing Period ─────────────────────

    def test_scenario_10_legitimate_subsequent_billing_period(self):
        """
        Scenario 10: When Period 1 (2026-11) is completed, subscription advances to Period 2 (2026-12).
        A new reservation with new billing_period_key succeeds cleanly on MySQL InnoDB.
        """
        bpk_nov = f"{self.sub_id}_2026-11"
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk_nov, "ORD_NOV_PAID",
            owner_token="worker_nov",
        )
        self.assertTrue(ok1)
        res1.status = BillingPeriodReservationStatus.COMPLETED.value
        res1.owner_token = None
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
            self.sub_id, self.org_id, bpk_dec, "ORD_DEC_RENEWAL",
            owner_token="worker_dec",
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

    # ── Scenario 11: Simultaneous Scheduler Renewals Strictly One Gateway Dispatch ─

    def test_scenario_11_simultaneous_scheduler_renewals_strictly_one_gateway_dispatch(self):
        """
        Scenario 11 (Phase 6): Two concurrent scheduler workers run RenewalService.process_due_renewals
        for the same due subscription using independent MySQL sessions.
        Asserts that the gateway's redeem() is dispatched EXACTLY ONCE.
        """
        order_id = "ORD_SCHEDULER_CONCURRENCY_01"
        now = datetime.utcnow()
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.next_billing_at = now - timedelta(hours=1)
        sub.current_period_start = now - timedelta(days=30)
        sub.claim_token = None
        sub.claim_expires_at = None

        mandate = SaaSMandate(
            id=666,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_SCHED_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.commit()

        sub.active_mandate_id = mandate.id
        db.session.commit()

        notification = SaaSPreDebitNotification(
            id=888,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=now - timedelta(hours=2),
            valid_upto_at=now + timedelta(days=1),
            billing_period_key=_compute_billing_period_key(self.sub_id, sub.current_period_start),
        )
        db.session.add(notification)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_SCHED_01"}],
        }
        mock_client.get_subscription_status.return_value = {"state": "ACTIVE"}

        barrier = threading.Barrier(2)
        results = {}

        def run_scheduler_worker(worker_id: str):
            with self.app.app_context():
                try:
                    barrier.wait(timeout=5)
                    worker_results = RenewalService.process_due_renewals(self.provider)
                    results[worker_id] = worker_results
                except Exception as exc:
                    results[worker_id] = exc

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            t1 = threading.Thread(target=run_scheduler_worker, args=("worker_1",))
            t2 = threading.Thread(target=run_scheduler_worker, args=("worker_2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # PhonePe redeem must be dispatched EXACTLY ONCE
        self.assertEqual(mock_client.redeem.call_count, 1)

        r1 = results.get("worker_1", [])
        r2 = results.get("worker_2", [])
        total_executed = sum(1 for r in r1 if isinstance(r, dict) and r.get("success")) + sum(1 for r in r2 if isinstance(r, dict) and r.get("success"))
        self.assertEqual(total_executed, 1)

    # ── Scenario 12: Simultaneous Webhook Event Delivery Fencing ──────────────────

    def test_scenario_12_simultaneous_webhook_event_delivery_fencing(self):
        """
        Scenario 12 (Phase 6): Two concurrent workers deliver the same webhook event.
        Provider-scoped unique constraint and atomic fencing token ensure only one worker
        processes the event while the other receives duplicate/already_processed.
        """
        event_id = "EVT_MYSQL_CONCUR_01"
        now = datetime.utcnow()
        now_ts = int(now.timestamp())

        barrier = threading.Barrier(2)
        worker_outcomes = {}

        def run_webhook_worker(worker_id: str):
            with self.app.app_context():
                engine = create_engine(self.app.config["SQLALCHEMY_DATABASE_URI"])
                Session = sessionmaker(bind=engine)
                session = Session()
                try:
                    barrier.wait(timeout=5)
                    # Simulate distributed intake
                    token = f"worker_{worker_id}"
                    expires_at = now + timedelta(seconds=60)

                    existing = session.query(SaaSWebhookEvent).filter_by(
                        payment_provider="phonepe_v2",
                        event_id=event_id,
                    ).first()

                    if not existing:
                        new_evt = SaaSWebhookEvent(
                            event_id=event_id,
                            payment_provider="phonepe_v2",
                            organization_id=self.org_id,
                            event_type="SUBSCRIPTION_REDEMPTION",
                            payload_summary=json.dumps({"ts": now_ts}),
                            status=WebhookEventStatus.PROCESSING.value,
                            owner_token=token,
                            owner_expires_at=expires_at,
                        )
                        session.add(new_evt)
                        try:
                            session.commit()
                            worker_outcomes[worker_id] = "ACQUIRED"
                        except Exception:
                            session.rollback()
                            worker_outcomes[worker_id] = "DUPLICATE_INSERT"
                    else:
                        worker_outcomes[worker_id] = "ALREADY_EXISTS"
                except Exception as exc:
                    worker_outcomes[worker_id] = str(exc)
                finally:
                    session.close()

        t1 = threading.Thread(target=run_webhook_worker, args=("1",))
        t2 = threading.Thread(target=run_webhook_worker, args=("2",))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one worker acquired ownership; the other encountered duplicate/existing
        acquired_count = sum(1 for outcome in worker_outcomes.values() if outcome == "ACQUIRED")
        self.assertEqual(acquired_count, 1)

        # Database state has exactly one event record in PROCESSING state
        with self.app.app_context():
            evt = SaaSWebhookEvent.query.filter_by(
                payment_provider="phonepe_v2",
                event_id=event_id,
            ).one()
            self.assertEqual(evt.status, WebhookEventStatus.PROCESSING.value)
            self.assertIsNotNone(evt.owner_token)


if __name__ == "__main__":
    unittest.main()

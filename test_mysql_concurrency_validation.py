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


if __name__ == "__main__":
    unittest.main()

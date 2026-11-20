import unittest
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from flask_login import LoginManager

from model import (
    db,
    Plan,
    Organization,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSMandate,
    SaaSPreDebitNotification,
    PreDebitNotificationStatus,
    SaaSBillingPeriodReservation,
    BillingPeriodReservationStatus,
)
from services.billing.providers.phonepe_autopay_v2_provider import (
    PhonePeAutoPayV2Provider,
)
from services.billing.providers.phonepe_autopay_client import (
    PhonePeResponseError,
    PhonePeNetworkError,
)
from services.billing.renewal_service import RenewalService
from services.billing.subscription_service import SubscriptionService


class PhonePeV2RenewalReconciliationTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = Flask("phonepe_v2_reconciliation_test")
        cls.app.config.update(
            SECRET_KEY="test-production-secret-key-reconciliation",
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            BILLING_ENABLED=True,
            RENEWAL_CRON_SECRET="test-cron-secret-12345",
        )
        db.init_app(cls.app)
        login_manager = LoginManager()
        login_manager.init_app(cls.app)
        login_manager.user_loader(lambda uid: None)
        from routes.saas import saas_bp
        cls.app.register_blueprint(saas_bp)

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        # Seed default test Plan and Org
        self.plan = Plan(
            name="Pro Plan",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )
        db.session.add(self.plan)
        db.session.flush()

        self.org = Organization(
            name="Alpha Corp",
            unique_code="ALPHA01",
            plan_id=self.plan.id,
        )
        db.session.add(self.org)
        db.session.flush()

        self.sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe_v2",
            amount_paise=100000,
            status=SaaSSubscriptionStatus.ACTIVE,
            provider_subscription_id="MSUB_ALPHA_001",
            current_period_start=datetime.utcnow() - timedelta(days=28),
            current_period_end=datetime.utcnow() + timedelta(days=2),
            next_billing_at=datetime.utcnow() + timedelta(days=2),
        )
        db.session.add(self.sub)
        db.session.flush()

        self.mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id="MSUB_ALPHA_001",
            max_amount_paise=500000,
            frequency="MONTHLY",
            mandate_status="ACTIVE",
            auto_debit=False,
        )
        db.session.add(self.mandate)
        db.session.flush()

        self.sub.active_mandate_id = self.mandate.id
        db.session.commit()

        self.mock_client = MagicMock()
        self.provider = PhonePeAutoPayV2Provider(autopay_client=self.mock_client)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    # ── Test 1: Stuck INITIATED notification reconciliation ─────────────────
    def test_stuck_initiated_notification_reconciles_to_notified(self):
        """1. Stuck INITIATED notification is reconciled to NOTIFIED upon authoritative confirmation."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_STUCK_INIT_01",
            amount_paise=100000,
            status=PreDebitNotificationStatus.INITIATED.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.return_value = {
            "state": "NOTIFIED",
            "validAfter": "2026-11-20T10:00:00Z",
            "validUpto": "2026-11-22T10:00:00Z",
        }

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["merchant_order_id"], "ORD_ALPHA_STUCK_INIT_01")
        self.assertEqual(reconciled[0]["current_status"], "NOTIFIED")
        self.assertTrue(reconciled[0]["success"])

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertIsNotNone(notification.notified_at)
        self.assertIsNotNone(notification.valid_after_at)

    # ── Test 2: NOTIFICATION_IN_PROGRESS reconciliation to NOTIFIED ─────────
    def test_notification_in_progress_reconciles_to_notified(self):
        """2. NOTIFICATION_IN_PROGRESS notification transitions to NOTIFIED with notified_at set."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_IN_PROGRESS_02",
            amount_paise=100000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            created_at=datetime.utcnow() - timedelta(minutes=15),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.return_value = {
            "state": "NOTIFIED",
            "validAfter": "2026-11-20T10:00:00Z",
            "validUpto": "2026-11-22T10:00:00Z",
        }

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["current_status"], "NOTIFIED")

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertIsNotNone(notification.notified_at)

    # ── Test 3: Unknown PhonePe state remains unresolved ─────────────────────
    def test_unknown_phonepe_state_remains_unresolved(self):
        """3. Unknown or unexpected state from PhonePe retains NOTIFICATION_IN_PROGRESS."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_UNKNOWN_STATE_03",
            amount_paise=100000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.return_value = {
            "state": "PENDING_MANUAL_REVIEW",
        }

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["current_status"], PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value)
        self.assertFalse(reconciled[0]["success"])

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value)
        self.assertIsNone(notification.notified_at)

    # ── Test 4: COMPLETED/SUCCESS does not become NOTIFIED ───────────────────
    def test_completed_or_success_state_does_not_become_notified(self):
        """4. Payment completion state (COMPLETED/SUCCESS) must NEVER mark notification delivery."""
        for state in ("COMPLETED", "SUCCESS"):
            notification = SaaSPreDebitNotification(
                organization_id=self.org.id,
                subscription_id=self.sub.id,
                mandate_id=self.mandate.id,
                merchant_order_id=f"ORD_ALPHA_COMPLETION_{state}",
                amount_paise=100000,
                status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
                created_at=datetime.utcnow() - timedelta(minutes=10),
            )
            db.session.add(notification)
            db.session.commit()

            self.mock_client.get_order_status.return_value = {
                "state": state,
            }

            reconciled = RenewalService.reconcile_unresolved_notifications(
                self.provider,
                min_age_minutes=2,
            )

            db.session.refresh(notification)
            self.assertNotEqual(notification.status, PreDebitNotificationStatus.NOTIFIED.value)
            self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value)
            self.assertIsNone(notification.notified_at)

    # ── Test 5: Definitive FAILED status recovery ────────────────────────────
    def test_failed_notification_recovery_authoritative_notified(self):
        """5a. Notification previously marked FAILED is recovered to NOTIFIED if PhonePe confirms NOTIFIED."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_RECOVER_FAILED_05A",
            amount_paise=100000,
            status=PreDebitNotificationStatus.FAILED.value,
            error_message="Previous local failure",
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.return_value = {
            "state": "NOTIFIED",
            "validAfter": "2026-11-20T10:00:00Z",
            "validUpto": "2026-11-22T10:00:00Z",
        }

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["action"], "recovered_authoritative_notified")
        self.assertTrue(reconciled[0]["success"])

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertIsNone(notification.error_message)
        self.assertIsNotNone(notification.notified_at)

    def test_failed_notification_recovery_guarded_retry_on_404(self):
        """5b. Order never reached PhonePe (404 NOT_FOUND): safe guarded retry reuses same order ID."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_RECOVER_RETRY_05B",
            amount_paise=100000,
            status=PreDebitNotificationStatus.FAILED.value,
            error_message="Initial connection aborted",
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        # PhonePe reports 404 (order not found) on status query
        self.mock_client.get_order_status.side_effect = PhonePeResponseError(
            "Order not found", status_code=404, error_code="ORDER_NOT_FOUND"
        )
        # PhonePe live mandate check confirms ACTIVE
        self.mock_client.check_subscription_status = MagicMock(return_value=MagicMock(status="ACTIVE"))
        # PhonePe notify_redemption succeeds on retry
        self.mock_client.notify_redemption.return_value = {
            "orderId": "PP_ORD_RETRY_01",
            "validAfter": "2026-11-20T10:00:00Z",
            "validUpto": "2026-11-22T10:00:00Z",
        }

        with patch.object(self.provider, "check_subscription_status", return_value=MagicMock(status="ACTIVE")):
            reconciled = RenewalService.reconcile_unresolved_notifications(
                self.provider,
                min_age_minutes=2,
            )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["action"], "guarded_retry")
        self.assertTrue(reconciled[0]["success"])

        # Verified: exactly one record exists in DB (no duplicate order ID created)
        pdn_count = SaaSPreDebitNotification.query.filter_by(
            merchant_order_id="ORD_ALPHA_RECOVER_RETRY_05B"
        ).count()
        self.assertEqual(pdn_count, 1)

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value)

    def test_failed_notification_terminal_on_gateway_no_retry(self):
        """5c. Order definitively FAILED on PhonePe: stays FAILED, no blind retry."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_TERMINAL_FAILED_05C",
            amount_paise=100000,
            status=PreDebitNotificationStatus.FAILED.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.side_effect = None
        self.mock_client.get_order_status.return_value = {
            "state": "FAILED",
            "message": "Mandate suspended by issuing bank",
        }

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["action"], "definitive_failure_no_retry")
        self.assertFalse(reconciled[0]["success"])
        self.mock_client.notify_redemption.assert_not_called()

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.FAILED.value)
        self.assertIn("Mandate suspended", notification.error_message)

    # ── Test 6: Ambiguous timeout never triggers blind retry ─────────────────
    def test_ambiguous_network_timeout_never_triggers_blind_retry(self):
        """6. Ambiguous network timeout during status query leaves record unresolved without retrying."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_TIMEOUT_06",
            amount_paise=100000,
            status=PreDebitNotificationStatus.FAILED.value,
            error_message="Initial timeout",
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.side_effect = PhonePeNetworkError("Read timeout connecting to PhonePe")

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["action"], "unresolved_network_timeout")
        self.assertFalse(reconciled[0]["success"])
        self.mock_client.notify_redemption.assert_not_called()

        db.session.refresh(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.FAILED.value)

    # ── Test 7: Concurrent workers cannot duplicate notification/retry ──────
    def test_concurrent_worker_claim_blocks_duplicate_reconciliation(self):
        """7. Concurrent active lease on subscription blocks reconciliation/retry by another worker."""
        # Worker 1 acquires lease
        token = SubscriptionService.acquire_subscription_claim(
            subscription_id=self.sub.id,
            organization_id=self.org.id,
            lease_duration_seconds=300,
        )
        self.assertIsNotNone(token)

        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_CONCURRENT_07",
            amount_paise=100000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        # Worker 2 attempts reconciliation
        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        # Worker 2 skipped sub because active lease was held
        self.assertEqual(len(reconciled), 0)
        self.mock_client.get_order_status.assert_not_called()

        # Worker 1 releases lease
        SubscriptionService.release_subscription_claim(
            subscription_id=self.sub.id,
            organization_id=self.org.id,
            claim_token=token,
        )

        # Now worker 2 can process it
        self.mock_client.get_order_status.side_effect = None
        self.mock_client.get_order_status.return_value = {"state": "NOTIFIED"}

        reconciled_after = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )
        self.assertEqual(len(reconciled_after), 1)
        self.assertEqual(reconciled_after[0]["current_status"], "NOTIFIED")

    # ── Test 8: Organization isolation ──────────────────────────────────────
    def test_organization_isolation_enforced(self):
        """8. Relational mismatch or foreign organization notifications are strictly isolated."""
        other_org = Organization(name="Beta Corp", unique_code="BETA01", plan_id=self.plan.id)
        db.session.add(other_org)
        db.session.flush()

        other_sub = SaaSSubscription(
            organization_id=other_org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe_v2",
            amount_paise=100000,
            status=SaaSSubscriptionStatus.ACTIVE,
        )
        db.session.add(other_sub)
        db.session.flush()

        # Mismatched notification: belongs to other_org, but has self.sub.id
        mismatched_pdn = SaaSPreDebitNotification(
            organization_id=other_org.id,
            subscription_id=self.sub.id,  # mismatch!
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_MISMATCHED_ORG_08",
            amount_paise=100000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(mismatched_pdn)
        db.session.commit()

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        # Relational mismatch was rejected by join and integrity check
        matched_mismatched = [r for r in reconciled if r["merchant_order_id"] == "ORD_MISMATCHED_ORG_08"]
        self.assertEqual(len(matched_mismatched), 0)

    # ── Test 9: Razorpay subscriptions are untouched ────────────────────────
    def test_razorpay_subscriptions_are_untouched(self):
        """9. Subscriptions configured for Razorpay are strictly ignored by PhonePe V2 reconciliation."""
        rzp_org = Organization(name="Razorpay Corp", unique_code="RZP01", plan_id=self.plan.id)
        db.session.add(rzp_org)
        db.session.flush()

        rzp_sub = SaaSSubscription(
            organization_id=rzp_org.id,
            plan_id=self.plan.id,
            payment_provider="razorpay",
            amount_paise=100000,
            status=SaaSSubscriptionStatus.ACTIVE,
        )
        db.session.add(rzp_sub)
        db.session.flush()

        rzp_pdn = SaaSPreDebitNotification(
            organization_id=rzp_org.id,
            subscription_id=rzp_sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_RZP_09",
            amount_paise=100000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(rzp_pdn)
        db.session.commit()

        reconciled = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )

        # Razorpay record was completely excluded from query
        rzp_results = [r for r in reconciled if r["merchant_order_id"] == "ORD_RZP_09"]
        self.assertEqual(len(rzp_results), 0)

    # ── Test 10: Repeated scheduler invocation is idempotent ────────────────
    def test_repeated_scheduler_invocation_is_idempotent(self):
        """10. Repeated invocation of notification reconciliation is safe and idempotent."""
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=self.mandate.id,
            merchant_order_id="ORD_ALPHA_IDEMPOTENT_10",
            amount_paise=100000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            created_at=datetime.utcnow() - timedelta(minutes=10),
        )
        db.session.add(notification)
        db.session.commit()

        self.mock_client.get_order_status.side_effect = None
        self.mock_client.get_order_status.return_value = {
            "state": "NOTIFIED",
            "validAfter": "2026-11-20T10:00:00Z",
            "validUpto": "2026-11-22T10:00:00Z",
        }

        # Run 1: Reconciles to NOTIFIED
        run1 = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )
        self.assertEqual(len(run1), 1)
        self.assertEqual(run1[0]["current_status"], "NOTIFIED")

        # Run 2: Already NOTIFIED, candidate query excludes it
        run2 = RenewalService.reconcile_unresolved_notifications(
            self.provider,
            min_age_minutes=2,
        )
        self.assertEqual(len(run2), 0)

        # Run 3: Direct provider call on already NOTIFIED record is a no-op
        db.session.refresh(notification)
        notified_timestamp = notification.notified_at
        self.provider.reconcile_notification_status(notification)
        self.assertEqual(notification.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertEqual(notification.notified_at, notified_timestamp)

    # ── Test 11: Scheduler endpoint unauthorized without secret ─────────────
    def test_scheduler_endpoint_unauthorized_without_secret(self):
        """11. Scheduler endpoint /saas/api/reconcile-notifications requires valid RENEWAL_CRON_SECRET."""
        client = self.app.test_client()

        # No secret provided -> 403
        resp_no_auth = client.post("/saas/api/reconcile-notifications")
        self.assertEqual(resp_no_auth.status_code, 403)

        # Invalid secret provided -> 403
        resp_bad_auth = client.post(
            "/saas/api/reconcile-notifications",
            headers={"X-Renewal-Secret": "wrong-secret-value"},
        )
        self.assertEqual(resp_bad_auth.status_code, 403)

    # ── Test 12: Scheduler endpoint authorized success ──────────────────────
    def test_scheduler_endpoint_authorized_success(self):
        """12. Scheduler endpoint /saas/api/reconcile-notifications succeeds with valid secret."""
        client = self.app.test_client()

        with patch("services.billing.renewal_service.RenewalService.reconcile_unresolved_notifications", return_value=[{"action": "reconcile", "success": True}]):
            with patch("services.billing.billing_service.BillingService.get_provider", return_value=self.provider):
                resp = client.post(
                    "/saas/api/reconcile-notifications",
                    headers={"X-Renewal-Secret": "test-cron-secret-12345"},
                )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["reconciled_count"], 1)

    # ── Test 13: Scheduler endpoint closed when billing disabled ────────────
    def test_scheduler_endpoint_closed_when_billing_disabled(self):
        """13. Reconcile endpoint returns 503 Service Unavailable when billing is disabled."""
        client = self.app.test_client()

        orig_billing = self.app.config.get("BILLING_ENABLED")
        self.app.config["BILLING_ENABLED"] = False
        try:
            resp = client.post(
                "/saas/api/reconcile-notifications",
                headers={"X-Renewal-Secret": "test-cron-secret-12345"},
            )
            self.assertEqual(resp.status_code, 503)
        finally:
            self.app.config["BILLING_ENABLED"] = orig_billing

    # ── Test 14: Process-renewals supports action=reconcile_only ─────────────
    def test_process_renewals_reconcile_only_action(self):
        """14. /saas/api/process-renewals?action=reconcile_only runs reconciliation without executing renewals."""
        client = self.app.test_client()

        with patch("services.billing.renewal_service.RenewalService.reconcile_unresolved_notifications", return_value=[{"reconciled": True}]) as mock_recon:
            with patch("services.billing.renewal_service.RenewalService.process_upcoming_notifications") as mock_upcoming:
                with patch("services.billing.renewal_service.RenewalService.process_due_renewals") as mock_due:
                    with patch("services.billing.billing_service.BillingService.get_provider", return_value=self.provider):
                        resp = client.post(
                            "/saas/api/process-renewals?action=reconcile_only",
                            headers={"X-Renewal-Secret": "test-cron-secret-12345"},
                        )

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["action"], "reconcile_only")
        mock_recon.assert_called_once()
        mock_upcoming.assert_not_called()
        mock_due.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

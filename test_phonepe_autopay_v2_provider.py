"""
test_phonepe_autopay_v2_provider.py
===================================
Comprehensive unit tests for PhonePeAutoPayV2Provider and BillingService factory.
Verifies all abstract and public methods, RBI compliance rules, financial safety
guards, tenant isolation, and backward compatibility with PhonePe V3.
"""

from __future__ import annotations

import base64
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app import app
from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    SaaSSubscription,
    SaaSTransaction,
    MandateStatus,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSTransactionStatus,
    User,
)
from services.billing.billing_service import BillingService
from services.billing.providers.phonepe_autopay_v2_provider import (
    PhonePeAutoPayV2Provider,
    _parse_datetime,
)
from services.billing.providers.phonepe_provider import PhonePeProvider
from services.billing.providers.phonepe_autopay_client import (
    PhonePeAutoPayClient,
    PhonePeResponseError,
    PhonePeNetworkError,
)


class TestPhonePeAutoPayV2Provider(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app_context = self.app.app_context()
        self.app_context.push()

        BillingService.reset_provider_cache()

        # Create provider with mocked clients
        self.mock_oauth = MagicMock()
        self.mock_client = MagicMock()

        with patch("services.billing.providers.phonepe_autopay_v2_provider.PhonePeOAuthClient") as mock_oauth_cls, \
             patch("services.billing.providers.phonepe_autopay_v2_provider.PhonePeAutoPayClient") as mock_client_cls:
            mock_oauth_cls.from_config.return_value = self.mock_oauth
            mock_client_cls.from_oauth_client.return_value = self.mock_client
            self.provider = PhonePeAutoPayV2Provider(merchant_id="PGTESTPAYUAT")

        self.provider._client = self.mock_client
        self.provider._oauth = self.mock_oauth

        # Dummy models for testing
        self.dummy_plan = Plan(
            id=1,
            name="Pro",
            display_name="Professional Plan",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )
        self.dummy_user = User(id=42, username="testadmin", email="admin@example.com")
        self.dummy_org = Organization(id=7, name="Acme Glass", unique_code="ACME")
        self.dummy_sub = SaaSSubscription(
            id=99,
            organization_id=7,
            plan_id=1,
            payment_provider="phonepe_v2",
            status=SaaSSubscriptionStatus.ACTIVE,
            billing_cycle=SaaSBillingCycle.MONTHLY,
            active_mandate_id=10,
        )
        self.dummy_sub.organization = self.dummy_org

    def tearDown(self):
        BillingService.reset_provider_cache()
        self.app_context.pop()

    # ── Test 1: Provider Name ─────────────────────────────────────────────────
    def test_provider_name_is_phonepe_v2(self):
        """Test provider identifier is 'phonepe_v2'."""
        self.assertEqual(self.provider.name, "phonepe_v2")

    # ── Test 2: Factory Registration ─────────────────────────────────────────
    def test_factory_registration_phonepe_v2(self):
        """Test BillingService factory returns PhonePeAutoPayV2Provider for 'phonepe_v2'."""
        with patch("services.billing.providers.phonepe_autopay_v2_provider.PhonePeOAuthClient.from_config") as mock_oauth:
            mock_oauth.return_value = self.mock_oauth
            provider = BillingService.get_provider("phonepe_v2")
            self.assertIsInstance(provider, PhonePeAutoPayV2Provider)
            self.assertEqual(provider.name, "phonepe_v2")

            # Check cached singleton
            cached = BillingService.get_provider("phonepe_v2")
            self.assertIs(provider, cached)

    # ── Test 3: Create Mandate Success ────────────────────────────────────────
    @patch("model.db.session.commit")
    @patch("model.db.session.flush")
    @patch("model.db.session.add")
    def test_create_subscription_mandate_success(self, mock_add, mock_flush, mock_commit):
        """Test mandate setup calls client and creates SaaSMandate with active_mandate_id."""
        redirect_target = "https://mercury-t2.phonepe.com/transact/pg?token=v2token123"
        self.mock_client.setup_subscription.return_value = {
            "redirectUrl": redirect_target,
            "subscriptionId": "SUB_PHONEPE_123",
            "mandateReference": "UMN_PHONEPE_123",
        }

        res = self.provider.create_subscription_mandate(
            subscription=self.dummy_sub,
            plan=self.dummy_plan,
            billing_cycle="monthly",
            user=self.dummy_user,
            return_url="https://app.glassentials.com/saas/callback",
            callback_url="https://app.glassentials.com/saas/webhook",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.provider, "phonepe_v2")
        self.assertEqual(res.redirect_url, redirect_target)
        self.assertTrue(res.merchant_subscription_id.startswith("MSUB_V2_7_"))
        self.assertTrue(res.merchant_transaction_id.startswith("MT_V2_7_"))
        self.mock_client.setup_subscription.assert_called_once()
        mock_add.assert_called_once()
        mock_commit.assert_called_once()

    # ── Test 4: Create Mandate API Error ──────────────────────────────────────
    def test_create_subscription_mandate_api_error(self):
        """Test API error in setup_subscription returns failed MandateSetupResult."""
        self.mock_client.setup_subscription.side_effect = PhonePeResponseError(
            "Invalid maxAmount parameter", status_code=400, error_code="BAD_REQUEST"
        )

        res = self.provider.create_subscription_mandate(
            subscription=self.dummy_sub,
            plan=self.dummy_plan,
            billing_cycle="yearly",
            user=self.dummy_user,
            return_url="https://app.glassentials.com/saas/callback",
            callback_url="https://app.glassentials.com/saas/webhook",
        )

        self.assertFalse(res.success)
        self.assertIn("Invalid maxAmount parameter", res.error_message)
        self.assertEqual(res.provider, "phonepe_v2")

    # ── Test 5: Check Subscription Status ACTIVE ──────────────────────────────
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    def test_check_subscription_status_active(self, mock_commit, mock_mandate_query):
        """Test check_subscription_status maps ACTIVE to success=True and updates DB."""
        mock_mandate = MagicMock(mandate_status="INITIATED", auto_debit=False)
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.get_subscription_status.return_value = {
            "state": "ACTIVE",
            "autoDebit": True,
            "subscriptionId": "SUB_LIVE_99",
            "mandateReference": "UMN_LIVE_99",
            "maxAmount": 118000,
        }

        res = self.provider.check_subscription_status("MSUB_V2_7_123")

        self.assertTrue(res.success)
        self.assertEqual(res.status, "ACTIVE")
        self.assertEqual(res.provider_subscription_id, "SUB_LIVE_99")
        self.assertEqual(res.mandate_reference, "UMN_LIVE_99")
        self.assertEqual(mock_mandate.mandate_status, "ACTIVE")
        self.assertTrue(mock_mandate.auto_debit)
        mock_commit.assert_called_once()

    # ── Test 6: Check Subscription Status PAUSED ──────────────────────────────
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    def test_check_subscription_status_paused(self, mock_commit, mock_mandate_query):
        """Test check_subscription_status maps PAUSED to success=False."""
        mock_mandate = MagicMock(mandate_status="ACTIVE")
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.get_subscription_status.return_value = {
            "state": "PAUSED",
            "subscriptionId": "SUB_LIVE_99",
        }

        res = self.provider.check_subscription_status("MSUB_V2_7_123")

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PAUSED")
        self.assertEqual(mock_mandate.mandate_status, "PAUSED")

    # ── Test 7: Check Transaction Status COMPLETED ────────────────────────────
    def test_check_transaction_status_completed(self):
        """Test check_transaction_status confirms success when both root and paymentDetails are COMPLETED."""
        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "merchantSubscriptionId": "MSUB_V2_7_123",
            "subscriptionId": "SUB_123",
            "paymentDetails": [
                {
                    "state": "COMPLETED",
                    "transactionId": "TXN_COMPLETED_001",
                    "amount": 118000,
                }
            ],
        }

        res = self.provider.check_transaction_status("MT_V2_7_123")

        self.assertTrue(res.success)
        self.assertEqual(res.status, "COMPLETED")
        self.assertEqual(res.provider_transaction_id, "TXN_COMPLETED_001")
        self.assertEqual(res.amount_paise, 118000)

    # ── Test 8: Check Transaction Status PENDING ──────────────────────────────
    def test_check_transaction_status_pending(self):
        """Test check_transaction_status never marks success when in PENDING state."""
        self.mock_client.get_order_status.return_value = {
            "state": "PENDING",
            "merchantSubscriptionId": "MSUB_V2_7_123",
            "paymentDetails": [
                {
                    "state": "PENDING",
                    "transactionId": "TXN_PENDING_002",
                    "amount": 118000,
                }
            ],
        }

        res = self.provider.check_transaction_status("MT_V2_7_123")

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")

    # ── Test 9: NOT_FOUND is Ambiguous (Never FAILED) ─────────────────────────
    def test_check_transaction_status_not_found_is_ambiguous(self):
        """Rule 4: NOT_FOUND from gateway is ALWAYS treated as ambiguous (status=PENDING)."""
        self.mock_client.get_order_status.side_effect = PhonePeResponseError(
            "Order not found", status_code=404, error_code="NOT_FOUND"
        )

        res = self.provider.check_transaction_status("MT_V2_NOT_FOUND")

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")
        self.assertIn("Ambiguous", res.error_message)

    # ── Test 10: Notify Redemption Creates DB Record ──────────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    @patch("model.db.session.add")
    def test_notify_redemption_creates_db_record(
        self, mock_add, mock_commit, mock_mandate_query, mock_pdn_query
    ):
        """Test notify_redemption inserts SaaSPreDebitNotification record."""
        mock_pdn_query.filter_by.return_value.first.return_value = None
        mock_mandate = MagicMock(id=10, merchant_subscription_id="MSUB_V2_7_99", auto_debit=False)
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.notify_redemption.return_value = {
            "orderId": "PO_NOTIFY_001",
            "validAfter": 1700000000,
            "validUpto": 1700200000,
        }

        res = self.provider.notify_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="NOTIFY_ORD_001",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.status, PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value)
        mock_add.assert_called_once()
        created_pdn = mock_add.call_args[0][0]
        self.assertIsInstance(created_pdn, SaaSPreDebitNotification)
        self.assertEqual(created_pdn.organization_id, 7)
        self.assertEqual(created_pdn.merchant_order_id, "NOTIFY_ORD_001")
        self.assertFalse(created_pdn.auto_debit_snapshot)

    # ── Test 11: Notify Records NOTIFICATION_IN_PROGRESS ──────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    @patch("model.db.session.add")
    def test_notify_redemption_records_notification_in_progress(
        self, mock_add, mock_commit, mock_mandate_query, mock_pdn_query
    ):
        """Test notification record is updated to NOTIFICATION_IN_PROGRESS with order ID and window."""
        mock_pdn_query.filter_by.return_value.first.return_value = None
        mock_mandate = MagicMock(id=10, merchant_subscription_id="MSUB_V2_7_99", auto_debit=False)
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.notify_redemption.return_value = {
            "orderId": "PO_PHONEPE_555",
            "validAfter": "2026-11-04T12:00:00Z",
            "validUpto": "2026-11-06T12:00:00Z",
        }

        res = self.provider.notify_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="NOTIFY_ORD_002",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.status, "NOTIFICATION_IN_PROGRESS")
        created_pdn = mock_add.call_args[0][0]
        self.assertEqual(created_pdn.phonepe_order_id, "PO_PHONEPE_555")
        self.assertIsNotNone(created_pdn.valid_after_at)
        self.assertIsNotNone(created_pdn.valid_upto_at)

    # ── Test 12: Execute Blocked for Auto-Debit Mandate ───────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    def test_execute_redemption_blocked_for_auto_debit(self, mock_pdn_query):
        """Rule 2: auto_debit_snapshot=True MUST NEVER call redeem()."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=True,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_upto_at=datetime.utcnow() + timedelta(days=2),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_AUTODEBIT_01",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("Cannot call redeem() for auto-debit mandate", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 13: Execute Requires NOTIFIED Status ─────────────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    def test_execute_redemption_requires_notified_status(self, mock_pdn_query):
        """Rule 1: NOTIFIED status is mandatory prior to calling redeem()."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
            valid_upto_at=datetime.utcnow() + timedelta(days=2),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_NOT_YET_NOTIFIED",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("Notification not yet NOTIFIED", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 14: Execute Blocked for Expired Notification ─────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.db.session.commit")
    def test_execute_redemption_expired_notification(self, mock_commit, mock_pdn_query):
        """Test execute_redemption blocks and marks EXPIRED when valid_upto_at is in the past."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_upto_at=datetime.utcnow() - timedelta(minutes=5),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_EXPIRED",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("expired", res.error_message)
        self.assertEqual(mock_pdn.status, PreDebitNotificationStatus.EXPIRED.value)
        self.mock_client.redeem.assert_not_called()

    # ── Test 15: Execute Success with Payment Details ─────────────────────────
    @patch("model.SaaSRedemptionAttempt.query")
    @patch("model.SaaSMandate.query")
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.db.session.commit")
    @patch("model.db.session.add")
    def test_execute_redemption_success_with_payment_details(
        self, mock_add, mock_commit, mock_pdn_query, mock_mandate_query, mock_attempt_query
    ):
        """Test successful redeem() when both root state and paymentDetails state are COMPLETED."""
        mock_pdn = MagicMock(
            id=5,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        mock_attempt_filter = MagicMock()
        mock_attempt_query.filter_by.return_value.filter.return_value.first.return_value = None
        mock_attempt_query.filter_by.return_value.count.return_value = 0

        mock_mandate = MagicMock(id=10, merchant_subscription_id="MSUB_V2_7_99")
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [
                {
                    "state": "COMPLETED",
                    "transactionId": "TXN_REDEEM_999",
                    "amount": 118000,
                }
            ],
        }

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_REDEEM_01",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.provider_payment_id, "TXN_REDEEM_999")
        self.assertEqual(mock_pdn.status, PreDebitNotificationStatus.CONSUMED.value)
        mock_commit.assert_called()

    # ── Test 16: Execute PENDING Sets PENDING_RECONCILIATION ───────────────────
    @patch("model.SaaSRedemptionAttempt.query")
    @patch("model.SaaSMandate.query")
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.db.session.commit")
    @patch("model.db.session.add")
    def test_execute_redemption_pending_sets_pending_reconciliation(
        self, mock_add, mock_commit, mock_pdn_query, mock_mandate_query, mock_attempt_query
    ):
        """Test that PENDING from redeem() sets attempt to PENDING_RECONCILIATION."""
        mock_pdn = MagicMock(
            id=5,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn
        mock_attempt_query.filter_by.return_value.filter.return_value.first.return_value = None
        mock_attempt_query.filter_by.return_value.count.return_value = 0

        mock_mandate = MagicMock(id=10, merchant_subscription_id="MSUB_V2_7_99")
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.redeem.return_value = {
            "state": "PENDING",
            "paymentDetails": [{"state": "PENDING", "transactionId": "TXN_IN_FLIGHT"}],
        }

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_REDEEM_PENDING",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")
        created_attempt = mock_add.call_args[0][0]
        self.assertEqual(created_attempt.status, RedemptionAttemptStatus.PENDING_RECONCILIATION.value)

    # ── Test 17: Execute Idempotency Check ────────────────────────────────────
    @patch("model.SaaSRedemptionAttempt.query")
    @patch("model.SaaSPreDebitNotification.query")
    def test_execute_redemption_idempotency_check(self, mock_pdn_query, mock_attempt_query):
        """Rule 7: Never run a second debit while an existing attempt is PENDING or AMBIGUOUS."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        existing_attempt = MagicMock(
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
            amount_paise=118000,
        )
        mock_attempt_query.filter_by.return_value.filter.return_value.first.return_value = existing_attempt

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_ALREADY_IN_FLIGHT",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")
        self.assertIn("already exists", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 18: Cancel Subscription Success ──────────────────────────────────
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    def test_cancel_subscription_success(self, mock_commit, mock_mandate_query):
        """Test cancel_subscription cancels mandate on PhonePe and clears active_mandate_id."""
        mock_mandate = MagicMock(id=10, merchant_subscription_id="MSUB_V2_7_10", mandate_status="ACTIVE")
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.cancel_subscription.return_value = {}
        self.mock_client.get_subscription_status.return_value = {"state": "CANCELLED"}

        sub = SaaSSubscription(organization_id=7, active_mandate_id=10)
        success = self.provider.cancel_subscription(sub, reason="Customer request")

        self.assertTrue(success)
        self.assertEqual(mock_mandate.mandate_status, MandateStatus.CANCELLED.value)
        self.assertIsNone(sub.active_mandate_id)
        self.mock_client.cancel_subscription.assert_called_once_with("MSUB_V2_7_10")
        mock_commit.assert_called_once()

    # ── Test 19: Verify Webhook Returns Untrusted Hint ─────────────────────────
    def test_verify_webhook_returns_untrusted_hint(self):
        """Test that V2 webhook is parsed as an untrusted hint containing essential fields."""
        payload = {
            "eventId": "EVT_V2_999",
            "event": "SUBSCRIPTION_ACTIVE",
            "merchantId": "PGTESTPAYUAT",
            "payload": {
                "merchantSubscriptionId": "MSUB_V2_7_99",
                "subscriptionId": "SUB_PHONEPE_99",
                "state": "ACTIVE",
                "amount": 118000,
            },
        }
        raw_bytes = json.dumps(payload).encode("utf-8")

        res = self.provider.verify_webhook(raw_bytes, headers={})

        self.assertTrue(res.is_valid)
        self.assertEqual(res.event_id, "EVT_V2_999")
        self.assertEqual(res.event_type, "SUBSCRIPTION_ACTIVE")
        self.assertEqual(res.merchant_subscription_id, "MSUB_V2_7_99")
        self.assertEqual(res.provider_subscription_id, "SUB_PHONEPE_99")
        self.assertEqual(res.state, "ACTIVE")
        self.assertEqual(res.amount_paise, 118000)

    # ── Test 20: Reconcile Notification Status to NOTIFIED ────────────────────
    @patch("model.db.session.commit")
    def test_reconcile_notification_status_notified(self, mock_commit):
        """Test reconcile_notification_status transitions status to NOTIFIED upon COMPLETED order."""
        notification = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="NOTIFY_ORD_RECON",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
        )

        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "validAfter": "2026-11-04T00:00:00Z",
            "validUpto": "2026-11-06T00:00:00Z",
        }

        updated = self.provider.reconcile_notification_status(notification)

        self.assertEqual(updated.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertIsNotNone(updated.notified_at)
        self.assertIsNotNone(updated.valid_after_at)
        self.assertIsNotNone(updated.valid_upto_at)
        mock_commit.assert_called_once()

    # ── Test 21: Reconcile Redemption Status Completed Advances Subscription ──
    @patch("services.billing.transaction_service.TransactionService.record_transaction")
    @patch("model.db.session.commit")
    def test_reconcile_redemption_status_completed_advances_subscription(
        self, mock_commit, mock_record_tx
    ):
        """Rule 5 & 6: Authoritative COMPLETED debit confirmation advances subscription period."""
        sub = SaaSSubscription(
            id=99,
            organization_id=7,
            billing_cycle=SaaSBillingCycle.MONTHLY,
            status=SaaSSubscriptionStatus.ACTIVE,
            active_mandate_id=10,
        )
        sub.organization = self.dummy_org

        attempt = SaaSRedemptionAttempt(
            id=55,
            organization_id=7,
            subscription_id=99,
            notification_id=5,
            merchant_order_id="ORD_RECON_COMPLETED",
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
        )
        attempt.subscription = sub
        attempt.notification = MagicMock(status=PreDebitNotificationStatus.NOTIFIED.value)

        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [
                {
                    "state": "COMPLETED",
                    "transactionId": "TXN_RECON_CONFIRMED",
                    "amount": 118000,
                }
            ],
        }

        updated = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(updated.status, RedemptionAttemptStatus.COMPLETED.value)
        self.assertEqual(updated.phonepe_transaction_id, "TXN_RECON_CONFIRMED")
        self.assertEqual(updated.notification.status, PreDebitNotificationStatus.CONSUMED.value)
        self.assertEqual(sub.status, SaaSSubscriptionStatus.ACTIVE)
        self.assertIsNotNone(sub.next_billing_at)
        self.assertIsNotNone(sub.current_period_start)
        self.assertIsNotNone(sub.current_period_end)
        mock_record_tx.assert_called_once()
        mock_commit.assert_called()

    # ── Test 22: Tenant Isolation Query Filters ───────────────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    def test_tenant_isolation_query_filters(self, mock_pdn_query):
        """Test tenant isolation: database queries filter by organization_id."""
        mock_pdn_query.filter_by.return_value.first.return_value = None

        self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORD_ISOLATION_CHECK",
        )

        mock_pdn_query.filter_by.assert_called_once_with(
            merchant_order_id="ORD_ISOLATION_CHECK",
            organization_id=self.dummy_sub.organization_id,
        )

    # ── Test 23: Legacy PhonePe V3 Billing Unaffected ─────────────────────────
    def test_legacy_phonepe_v3_billing_unaffected(self):
        """Test that BillingService.get_provider('phonepe') still resolves PhonePeProvider."""
        provider = BillingService.get_provider("phonepe")
        self.assertIsInstance(provider, PhonePeProvider)
        self.assertEqual(provider.name, "phonepe")

    # ── Test 24: Verify Payment Routes to Transaction Check by Order ID ──────
    @patch.object(PhonePeAutoPayV2Provider, "check_transaction_status")
    def test_verify_payment_routes_by_order_id(self, mock_check_tx):
        """Test verify_payment routes to check_transaction_status when merchantOrderId is provided."""
        mock_check_tx.return_value = MagicMock(success=True, status="COMPLETED")
        payload = {"merchantOrderId": "MT_ORDER_ROUTE_01", "merchantSubscriptionId": "MSUB_01"}

        res = self.provider.verify_payment(payload)

        mock_check_tx.assert_called_once_with("MT_ORDER_ROUTE_01")
        self.assertTrue(res.success)

    # ── Test 25: Verify Payment Routes to Subscription Check by Sub ID ───────
    @patch.object(PhonePeAutoPayV2Provider, "check_subscription_status")
    def test_verify_payment_routes_by_sub_id(self, mock_check_sub):
        """Test verify_payment routes to check_subscription_status when only merchantSubscriptionId is provided."""
        mock_check_sub.return_value = MagicMock(success=True, status="ACTIVE")
        payload = {"merchantSubscriptionId": "MSUB_ONLY_02"}

        res = self.provider.verify_payment(payload)

        mock_check_sub.assert_called_once_with("MSUB_ONLY_02")
        self.assertTrue(res.success)

    # ── Test 26: Webhook Merchant ID Mismatch Rejected ────────────────────────
    def test_webhook_merchant_id_mismatch_rejected(self):
        """Test that webhook with mismatched merchantId returns is_valid=False."""
        payload = {
            "eventId": "EVT_ATTACK_01",
            "merchantId": "DIFFERENT_MERCHANT_ID",
            "payload": {"state": "ACTIVE"},
        }
        res = self.provider.verify_webhook(json.dumps(payload).encode("utf-8"), headers={})

        self.assertFalse(res.is_valid)
        self.assertIn("Merchant ID mismatch", res.error_message)

    # ── Test 27: Sync Mandate Status ──────────────────────────────────────────
    @patch("model.db.session.commit")
    def test_sync_mandate_status(self, mock_commit):
        """Test sync_mandate_status queries PhonePe and updates SaaSMandate fields."""
        mandate = SaaSMandate(
            id=10,
            organization_id=7,
            subscription_id=99,
            merchant_subscription_id="MSUB_SYNC_01",
            mandate_status="INITIATED",
            auto_debit=False,
            max_amount_paise=118000,
            frequency="MONTHLY",
        )

        self.mock_client.get_subscription_status.return_value = {
            "state": "ACTIVE",
            "autoDebit": True,
            "subscriptionId": "SUB_SYNC_PHONEPE",
            "mandateReference": "UMN_SYNC_PHONEPE",
        }

        synced = self.provider.sync_mandate_status(mandate)

        self.assertEqual(synced.mandate_status, "ACTIVE")
        self.assertTrue(synced.auto_debit)
        self.assertEqual(synced.provider_subscription_id, "SUB_SYNC_PHONEPE")
        self.assertEqual(synced.mandate_reference, "UMN_SYNC_PHONEPE")
        mock_commit.assert_called_once()

    # ── Test 28: Redeem NOT_FOUND Sets AMBIGUOUS_NOT_FOUND ────────────────────
    @patch("model.SaaSRedemptionAttempt.query")
    @patch("model.SaaSMandate.query")
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.db.session.commit")
    @patch("model.db.session.add")
    def test_execute_redemption_not_found_sets_ambiguous(
        self, mock_add, mock_commit, mock_pdn_query, mock_mandate_query, mock_attempt_query
    ):
        """Rule 4: NOT_FOUND during redeem() sets AMBIGUOUS_NOT_FOUND and returns PENDING."""
        mock_pdn = MagicMock(
            id=5,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn
        mock_attempt_query.filter_by.return_value.filter.return_value.first.return_value = None
        mock_attempt_query.filter_by.return_value.count.return_value = 0

        mock_mandate = MagicMock(id=10, merchant_subscription_id="MSUB_V2_7_99")
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate

        self.mock_client.redeem.side_effect = PhonePeResponseError(
            "Transaction order not found", status_code=404, error_code="NOT_FOUND"
        )

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_REDEEM_404",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")
        created_attempt = mock_add.call_args[0][0]
        self.assertEqual(created_attempt.status, RedemptionAttemptStatus.AMBIGUOUS_NOT_FOUND.value)

    # ── Test 29: Datetime Parser Helper ───────────────────────────────────────
    def test_parse_datetime_helper(self):
        """Test _parse_datetime with various inputs: float epoch, int epoch, millis, ISO strings."""
        now = datetime.utcnow()
        self.assertIsNone(_parse_datetime(None))
        self.assertIsNone(_parse_datetime(""))
        self.assertIsNone(_parse_datetime("invalid"))
        self.assertEqual(_parse_datetime(now), now)

        # Seconds epoch
        epoch_sec = 1700000000
        dt_sec = _parse_datetime(epoch_sec)
        self.assertIsInstance(dt_sec, datetime)

        # Milliseconds epoch
        epoch_ms = 1700000000000
        dt_ms = _parse_datetime(epoch_ms)
        self.assertIsInstance(dt_ms, datetime)

        # ISO String
        iso_str = "2026-11-03T10:00:00Z"
        dt_iso = _parse_datetime(iso_str)
        self.assertEqual(dt_iso.year, 2026)
        self.assertEqual(dt_iso.month, 11)


if __name__ == "__main__":
    unittest.main()

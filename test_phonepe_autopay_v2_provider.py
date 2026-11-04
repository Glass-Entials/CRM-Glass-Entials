"""
test_phonepe_autopay_v2_provider.py
===================================
Comprehensive unit tests for PhonePeAutoPayV2Provider and BillingService factory.
Verifies all abstract and public methods, RBI compliance rules, financial safety
guards, tenant isolation, and backward compatibility with PhonePe V3.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from flask import Flask
from sqlalchemy.pool import StaticPool
from services.billing.payment_provider import VerificationResult, WebhookResult, RedemptionResult

from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSBillingPeriodReservation,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    SaaSSubscription,
    SaaSTransaction,
    BillingPeriodReservationStatus,
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
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:?check_same_thread=False"
        cls.app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
            "poolclass": StaticPool,
            "connect_args": {"timeout": 30, "check_same_thread": False},
        }
        cls.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        cls.app.config["TESTING"] = True
        cls.app.config["PHONEPE_ENV"] = "UAT"
        cls.app.config["PHONEPE_MERCHANT_ID"] = "PGTESTPAYUAT"
        cls.app.config["PHONEPE_CLIENT_ID"] = "test_client_id"
        cls.app.config["PHONEPE_CLIENT_SECRET"] = "test_client_secret"
        cls.app.config["PHONEPE_CLIENT_VERSION"] = 1
        cls.app.config["PHONEPE_WEBHOOK_USERNAME"] = "test_wh_user"
        cls.app.config["PHONEPE_WEBHOOK_PASSWORD"] = "test_wh_password"
        db.init_app(cls.app)

        with cls.app.app_context():
            db.create_all()

    def setUp(self):
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

        # Clean DB for test run
        db.session.query(SaaSBillingPeriodReservation).delete()
        db.session.query(SaaSRedemptionAttempt).delete()
        db.session.query(SaaSPreDebitNotification).delete()
        db.session.query(SaaSMandate).delete()
        db.session.query(SaaSTransaction).delete()
        db.session.query(SaaSSubscription).delete()
        db.session.query(Organization).delete()
        db.session.query(Plan).delete()
        db.session.query(User).delete()
        db.session.commit()

        # Dummy models for testing seeded in SQLite DB
        self.dummy_plan = Plan(
            id=1,
            name="Pro",
            display_name="Professional Plan",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )
        self.dummy_user = User(
            id=42,
            username="testadmin",
            email="admin@example.com",
            password="hashed_test_password",
            organization_id=7,
        )
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
        db.session.add_all([self.dummy_plan, self.dummy_user, self.dummy_org, self.dummy_sub])
        db.session.commit()

    def tearDown(self):
        try:
            db.session.rollback()
        except Exception:
            pass
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
        """Test check_transaction_status confirms success when both root and paymentDetails are COMPLETED for explicit debit."""
        pdn = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="MT_V2_7_123",
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add(pdn)
        db.session.commit()

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

        res = self.provider.check_transaction_status("MT_V2_7_123", organization_id=7)

        self.assertTrue(res.success)
        self.assertEqual(res.status, "COMPLETED")
        self.assertEqual(res.provider_transaction_id, "TXN_COMPLETED_001")
        self.assertEqual(res.amount_paise, 118000)

    # ── Test 8: Check Transaction Status PENDING ──────────────────────────────
    def test_check_transaction_status_pending(self):
        """Test check_transaction_status never marks success when in PENDING state."""
        pdn = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="MT_V2_7_123",
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add(pdn)
        db.session.commit()

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

        res = self.provider.check_transaction_status("MT_V2_7_123", organization_id=7)

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
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
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
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
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
            valid_after_at=datetime.utcnow() - timedelta(days=1),
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
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
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
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
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
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        existing_attempt = MagicMock(
            merchant_order_id="ORDER_ALREADY_IN_FLIGHT",
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
        auth_token = hashlib.sha256(b"test_wh_user:test_wh_password").hexdigest()

        res = self.provider.verify_webhook(raw_bytes, headers={"Authorization": f"Basic {auth_token}"})

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
        """Test reconcile_notification_status transitions status to NOTIFIED upon authoritative NOTIFIED order."""
        notification = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="NOTIFY_ORD_RECON",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
        )

        self.mock_client.get_order_status.return_value = {
            "state": "NOTIFIED",
            "validAfter": "2026-11-04T00:00:00Z",
            "validUpto": "2026-11-06T00:00:00Z",
        }

        updated = self.provider.reconcile_notification_status(notification)

        self.assertEqual(updated.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertIsNotNone(updated.notified_at)
        self.assertIsNotNone(updated.valid_after_at)
        self.assertIsNotNone(updated.valid_upto_at)
        mock_commit.assert_called_once()
        self.mock_client.get_order_status.assert_called_with("NOTIFY_ORD_RECON", details=False)

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
        attempt.notification = MagicMock(
            id=5,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            organization_id=7,
            subscription_id=99,
            auto_debit_snapshot=False,
        )

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

        res = self.provider.verify_payment(payload, organization_id=7)

        mock_check_tx.assert_called_once_with("MT_ORDER_ROUTE_01", organization_id=7)
        self.assertTrue(res.success)

    # ── Test 25: Verify Payment Routes to Subscription Check by Sub ID ───────
    @patch.object(PhonePeAutoPayV2Provider, "check_subscription_status")
    def test_verify_payment_routes_by_sub_id(self, mock_check_sub):
        """Test verify_payment routes to check_subscription_status when only merchantSubscriptionId is provided."""
        mock_check_sub.return_value = MagicMock(success=True, status="ACTIVE")
        payload = {"merchantSubscriptionId": "MSUB_ONLY_02"}

        res = self.provider.verify_payment(payload, organization_id=7)

        mock_check_sub.assert_called_once_with("MSUB_ONLY_02", organization_id=7)
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
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
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


    # ── Test 30: Notification Reconcile Completed Order Does Not Mark Notified ──
    @patch("model.db.session.commit")
    def test_reconcile_notification_status_completed_order_does_not_mark_notified(self, mock_commit):
        """Financial Safety: Generic payment completion state 'COMPLETED' must NEVER mark notification delivery."""
        notification = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="NOTIFY_ORD_COMPLETED_TEST",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
        )

        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "validAfter": "2026-11-04T00:00:00Z",
            "validUpto": "2026-11-06T00:00:00Z",
        }

        updated = self.provider.reconcile_notification_status(notification)

        self.assertEqual(updated.status, PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value)
        self.assertIsNone(updated.notified_at)
        mock_commit.assert_called_once()
        self.mock_client.get_order_status.assert_called_with("NOTIFY_ORD_COMPLETED_TEST", details=False)

    # ── Test 31: Notification Delivery and Debit Outcome Remain Separate ───────
    @patch("services.billing.transaction_service.TransactionService.record_transaction")
    @patch("model.db.session.commit")
    def test_notification_delivery_and_debit_outcome_remain_separate(
        self, mock_commit, mock_record_tx
    ):
        """Financial Safety: Authoritative NOTIFIED delivery must never trigger payment debit or period extension."""
        notification = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="NOTIFY_DELIVERY_SEPARATION",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
        )

        self.mock_client.get_order_status.return_value = {
            "state": "NOTIFIED",
            "validAfter": "2026-11-04T00:00:00Z",
            "validUpto": "2026-11-06T00:00:00Z",
        }

        updated = self.provider.reconcile_notification_status(notification)

        self.assertEqual(updated.status, PreDebitNotificationStatus.NOTIFIED.value)
        self.assertIsNotNone(updated.notified_at)
        mock_record_tx.assert_not_called()
        self.mock_client.redeem.assert_not_called()

    # ── Test 32: Reconcile Auto-Debit COMPLETED Requires Manual Reconciliation ──
    @patch("services.billing.transaction_service.TransactionService.record_transaction")
    @patch("model.db.session.commit")
    def test_reconcile_redemption_auto_debit_requires_manual_reconciliation(
        self, mock_commit, mock_record_tx
    ):
        """Financial Safety: Gateway-managed auto-debit COMPLETED order cannot advance period without settlement proof."""
        sub = SaaSSubscription(
            id=99,
            organization_id=7,
            billing_cycle=SaaSBillingCycle.MONTHLY,
            status=SaaSSubscriptionStatus.ACTIVE,
            active_mandate_id=10,
            current_period_start=datetime(2026, 10, 1),
            current_period_end=datetime(2026, 11, 1),
        )
        sub.organization = self.dummy_org

        notification = MagicMock(
            id=5,
            organization_id=7,
            subscription_id=99,
            auto_debit_snapshot=True,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )

        attempt = SaaSRedemptionAttempt(
            id=55,
            organization_id=7,
            subscription_id=99,
            notification_id=5,
            merchant_order_id="ORD_AUTODEBIT_RECON",
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
        )
        attempt.subscription = sub
        attempt.notification = notification

        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [
                {
                    "state": "COMPLETED",
                    "transactionId": "TXN_AD_CONFIRMED",
                    "amount": 118000,
                }
            ],
        }

        updated = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(updated.status, RedemptionAttemptStatus.MANUAL_RECONCILIATION_REQUIRED.value)
        self.assertIn("settlement confirmation", updated.failure_reason)
        # Period start must NOT be advanced
        self.assertEqual(sub.current_period_start, datetime(2026, 10, 1))
        # Notification must NOT be marked CONSUMED
        self.assertNotEqual(notification.status, PreDebitNotificationStatus.CONSUMED.value)
        # No transaction should be recorded
        mock_record_tx.assert_not_called()
        mock_commit.assert_called()

    # ── Test 33: Execute Blocked Before valid_after_at ─────────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    def test_execute_redemption_blocked_before_valid_after_at(self, mock_pdn_query):
        """Financial Safety: Debit cannot be executed before valid_after_at (pre-debit window not open yet)."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() + timedelta(hours=5),
            valid_upto_at=datetime.utcnow() + timedelta(days=2),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_TOO_EARLY",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("window has not opened yet", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 34: Execute Blocked When Validity Timestamps Missing ──────────────
    @patch("model.SaaSPreDebitNotification.query")
    def test_execute_redemption_blocked_missing_timestamps(self, mock_pdn_query):
        """Financial Safety: Debit must be rejected when validity timestamps are missing or malformed."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=None,
            valid_upto_at=None,
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_NO_TIMESTAMPS",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("missing validity window timestamps", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 35: Execute Blocked When Prior Attempt Unresolved ────────────────
    @patch("model.SaaSRedemptionAttempt.query")
    @patch("model.SaaSPreDebitNotification.query")
    def test_execute_redemption_blocked_when_prior_attempt_unresolved(
        self, mock_pdn_query, mock_attempt_query
    ):
        """Financial Safety: Cross-cycle debit safety blocks new redemption while any prior attempt remains unresolved."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        unresolved_attempt = MagicMock(
            merchant_order_id="PRIOR_CYCLE_DEBIT_UNRESOLVED",
            status=RedemptionAttemptStatus.MANUAL_RECONCILIATION_REQUIRED.value,
        )
        mock_attempt_query.filter_by.return_value.filter.return_value.first.return_value = unresolved_attempt

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="NEW_CYCLE_DEBIT",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")
        self.assertIn("remains unresolved", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 36: Expired Notification Does Not Authorize Replacement Debit ────
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.db.session.commit")
    def test_expired_notification_does_not_authorize_replacement_debit(
        self, mock_commit, mock_pdn_query
    ):
        """Financial Safety: Expiry marks notification EXPIRED and rejects debit without authorizing replacement."""
        mock_pdn = MagicMock(
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(days=3),
            valid_upto_at=datetime.utcnow() - timedelta(minutes=15),
        )
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_EXPIRED_SAFETY",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertEqual(mock_pdn.status, PreDebitNotificationStatus.EXPIRED.value)
        self.assertIn("expired", res.error_message)
        self.mock_client.redeem.assert_not_called()

    # ── Test 37: Cancel Status Inquiry Failure Preserves Active Mandate ────────
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    def test_cancel_subscription_status_inquiry_failure_preserves_active_mandate(
        self, mock_commit, mock_mandate_query
    ):
        """Financial Safety: If post-cancel S2S status inquiry fails, mandate is CANCEL_IN_PROGRESS and active_mandate_id is preserved."""
        sub = MagicMock(organization_id=7, active_mandate_id=10)
        mandate = MagicMock(
            id=10,
            merchant_subscription_id="MSUB_V2_7_99",
            mandate_status=MandateStatus.ACTIVE.value,
        )
        mock_mandate_query.filter_by.return_value.first.return_value = mandate

        self.mock_client.cancel_subscription.return_value = {"success": True}
        self.mock_client.get_subscription_status.side_effect = Exception("S2S timeout during cancellation verification")

        res = self.provider.cancel_subscription(sub)

        self.assertFalse(res)
        self.assertEqual(mandate.mandate_status, MandateStatus.CANCEL_IN_PROGRESS.value)
        self.assertEqual(sub.active_mandate_id, 10)  # Must NOT be cleared!
        mock_commit.assert_called()

    # ── Test 38: Cancel In-Progress State Preserves Active Mandate ──────────────
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    def test_cancel_subscription_in_progress_state_preserves_active_mandate(
        self, mock_commit, mock_mandate_query
    ):
        """Financial Safety: Non-terminal cancel response keeps mandate in CANCEL_IN_PROGRESS and preserves active_mandate_id."""
        sub = MagicMock(organization_id=7, active_mandate_id=10)
        mandate = MagicMock(
            id=10,
            merchant_subscription_id="MSUB_V2_7_99",
            mandate_status=MandateStatus.ACTIVE.value,
        )
        mock_mandate_query.filter_by.return_value.first.return_value = mandate

        self.mock_client.cancel_subscription.return_value = {"success": True}
        self.mock_client.get_subscription_status.return_value = {"state": "CANCEL_IN_PROGRESS"}

        res = self.provider.cancel_subscription(sub)

        self.assertFalse(res)
        self.assertEqual(mandate.mandate_status, MandateStatus.CANCEL_IN_PROGRESS.value)
        self.assertEqual(sub.active_mandate_id, 10)  # Must NOT be cleared!
        mock_commit.assert_called()

    # ── Test 39: Cancel Authoritative Confirmation Clears Active Mandate ──────
    @patch("model.SaaSMandate.query")
    @patch("model.db.session.commit")
    def test_cancel_subscription_authoritative_confirmation_clears_active_mandate(
        self, mock_commit, mock_mandate_query
    ):
        """Authoritative S2S confirmation (CANCELLED) sets mandate status CANCELLED and clears active_mandate_id."""
        sub = MagicMock(organization_id=7, active_mandate_id=10)
        mandate = MagicMock(
            id=10,
            merchant_subscription_id="MSUB_V2_7_99",
            mandate_status=MandateStatus.ACTIVE.value,
        )
        mock_mandate_query.filter_by.return_value.first.return_value = mandate

        self.mock_client.cancel_subscription.return_value = {"success": True}
        self.mock_client.get_subscription_status.return_value = {"state": "CANCELLED"}

        res = self.provider.cancel_subscription(sub)

        self.assertTrue(res)
        self.assertEqual(mandate.mandate_status, MandateStatus.CANCELLED.value)
        self.assertIsNone(sub.active_mandate_id)
        mock_commit.assert_called()

    # ── Test 40: Check Subscription Status Tenant Isolation ───────────────────
    @patch("model.SaaSMandate.query")
    def test_check_subscription_status_with_org_id_isolation(self, mock_mandate_query):
        """Tenant isolation: check_subscription_status strictly filters by organization_id when provided."""
        mock_mandate = MagicMock(mandate_status=MandateStatus.ACTIVE.value)
        mock_mandate_query.filter_by.return_value.first.return_value = mock_mandate
        self.mock_client.get_subscription_status.return_value = {"state": "ACTIVE"}

        res = self.provider.check_subscription_status("MSUB_TEST_123", organization_id=7)

        self.assertEqual(res.status, "ACTIVE")
        mock_mandate_query.filter_by.assert_called_with(
            merchant_subscription_id="MSUB_TEST_123",
            organization_id=7,
        )

    # ── Test 41: Check Transaction Status Tenant Isolation ────────────────────
    @patch("model.SaaSPreDebitNotification.query")
    @patch("model.SaaSRedemptionAttempt.query")
    def test_check_transaction_status_with_org_id_isolation(self, mock_attempt_query, mock_pdn_query):
        """Tenant isolation: check_transaction_status strictly filters by organization_id when provided."""
        mock_pdn = MagicMock(auto_debit_snapshot=False, organization_id=7)
        mock_pdn_query.filter_by.return_value.first.return_value = mock_pdn
        mock_attempt = MagicMock(status=RedemptionAttemptStatus.COMPLETED.value)
        mock_attempt_query.filter_by.return_value.first.return_value = mock_attempt
        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_777"}],
        }

        res = self.provider.check_transaction_status("ORD_TEST_999", organization_id=7)

        self.assertEqual(res.status, "COMPLETED")
        mock_pdn_query.filter_by.assert_called_with(
            merchant_order_id="ORD_TEST_999",
            organization_id=7,
        )

    # ── Test 42: Auto-Debit Status COMPLETED Returns Manual Recon (Phase 5.2) ──
    def test_check_transaction_status_auto_debit_returns_manual_recon(self):
        """Phase 5.2: Auto-debit COMPLETED order in check_transaction_status returns MANUAL_RECONCILIATION_REQUIRED and success=False."""
        pdn = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="MT_AD_CHECK_001",
            amount_paise=118000,
            auto_debit_snapshot=True,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add(pdn)
        db.session.commit()

        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "merchantSubscriptionId": "MSUB_V2_7_99",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_AD_001", "amount": 118000}],
        }

        res = self.provider.check_transaction_status("MT_AD_CHECK_001", organization_id=7)

        self.assertFalse(res.success)
        self.assertEqual(res.status, "MANUAL_RECONCILIATION_REQUIRED")
        self.assertIn("settlement", res.error_message.lower())

    # ── Test 43: verify_payment Auto-Debit Blocked (Phase 5.2) ─────────────────
    def test_verify_payment_auto_debit_blocked(self):
        """Phase 5.2: verify_payment for auto-debit cannot bypass auto-debit restrictions and returns success=False."""
        pdn = SaaSPreDebitNotification(
            organization_id=7,
            subscription_id=99,
            mandate_id=10,
            merchant_order_id="MT_AD_VERIFY_001",
            amount_paise=118000,
            auto_debit_snapshot=True,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add(pdn)
        db.session.commit()

        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_AD_V001", "amount": 118000}],
        }

        payload = {"merchantOrderId": "MT_AD_VERIFY_001"}
        res = self.provider.verify_payment(payload, organization_id=7)

        self.assertFalse(res.success)
        self.assertEqual(res.status, "MANUAL_RECONCILIATION_REQUIRED")

    # ── Test 44: Reconcile Missing Notification Relationship Safety (Phase 5.2) ─
    @patch("services.billing.transaction_service.TransactionService.record_transaction")
    def test_reconcile_redemption_missing_notification_sets_manual_recon(self, mock_record_tx):
        """Phase 5.2: Missing notification relationship sets MANUAL_RECONCILIATION_REQUIRED and does not extend period."""
        attempt = MagicMock(
            id=77,
            organization_id=7,
            subscription_id=99,
            notification_id=99999,  # Non-existent notification
            merchant_order_id="ORD_MISSING_NOTIF",
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
            notification=None,
        )

        res = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(res.status, RedemptionAttemptStatus.MANUAL_RECONCILIATION_REQUIRED.value)
        self.assertIn("missing or unverified", res.failure_reason)
        mock_record_tx.assert_not_called()

    # ── Test 45: Missing Tenant Context Prevents Financial Mutation (Phase 5.2) ─
    def test_missing_tenant_context_prevents_financial_mutation(self):
        """Phase 5.2: organization_id=None with unresolvable context returns UNRESOLVED with zero DB mutations."""
        self.mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "merchantSubscriptionId": "",
            "transactionId": "TXN_UNKNOWN",
            "amount": 1000,
        }
        self.mock_client.get_subscription_status.return_value = {
            "state": "ACTIVE",
            "merchantSubscriptionId": "",
        }

        res_tx = self.provider.check_transaction_status("MT_UNRESOLVABLE_UNKNOWN", organization_id=None)
        self.assertFalse(res_tx.success)
        self.assertEqual(res_tx.status, "UNRESOLVED")

        res_sub = self.provider.check_subscription_status("MSUB_UNRESOLVABLE_UNKNOWN", organization_id=None)
        self.assertFalse(res_sub.success)
        self.assertEqual(res_sub.status, "UNRESOLVED")

        res_verify = self.provider.verify_payment({"merchantOrderId": "MT_UNRESOLVABLE_UNKNOWN"}, organization_id=None)
        self.assertFalse(res_verify.success)
        self.assertEqual(res_verify.status, "UNRESOLVED")

    # ── Test 46: Atomic Claim Blocks Concurrent Redemptions (Phase 5.2) ────────
    def test_atomic_claim_blocks_concurrent_redemptions_different_order_ids(self):
        """Phase 5.2: Different order IDs for the same subscription cannot execute concurrently when claim lease is active."""
        claim_token = self.provider._acquire_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id)
        self.assertIsNotNone(claim_token)

        try:
            res = self.provider.execute_redemption(
                subscription=self.dummy_sub,
                amount_paise=118000,
                merchant_transaction_id="ORD_CONCURRENT_BLOCKED",
            )
            self.assertFalse(res.success)
            self.assertEqual(res.status, "PENDING")
            self.assertIn("currently claimed by another concurrent process", res.error_message)
        finally:
            self.provider._release_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id, claim_token)

    # ── Test 47: Lock Acquisition Failure Surfaced Safely (Phase 5.2) ──────────
    @patch.object(PhonePeAutoPayV2Provider, "_acquire_subscription_claim")
    def test_lock_acquisition_failure_surfaced_safely(self, mock_acquire):
        """Phase 5.2: Concurrency lock acquisition failure is safely surfaced as FAILED without proceeding."""
        mock_acquire.side_effect = Exception("Lock acquisition timeout on primary key")

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORD_LOCK_ERROR",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("Concurrency lock acquisition error", res.error_message)

    # ── Test 48: Lease Ownership and Expiration (Phase 5.2) ────────────────────
    def test_lease_ownership_and_expiration(self):
        """Phase 5.2: Lease ownership and expiration: active lease blocks; expired lease allows new acquisition."""
        tok1 = self.provider._acquire_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id, lease_duration_seconds=300)
        self.assertIsNotNone(tok1)

        tok2 = self.provider._acquire_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id)
        self.assertIsNone(tok2)

        sub = SaaSSubscription.query.get(self.dummy_sub.id)
        sub.claim_expires_at = datetime.utcnow() - timedelta(seconds=10)
        db.session.commit()

        tok3 = self.provider._acquire_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id, lease_duration_seconds=300)
        self.assertIsNotNone(tok3)
        self.assertNotEqual(tok1, tok3)

        self.provider._release_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id, tok3)

    # ── Test 49: Real DB Atomic Claim Concurrency (Phase 5.2) ──────────────────
    def test_real_db_atomic_claim_concurrency(self):
        """Phase 5.2: Multi-threaded concurrency test verifying atomic claim isolation across threads."""
        import threading

        acquired_tokens = []
        errors = []
        barrier = threading.Barrier(5)
        # SQLite StaticPool shares a single C-level sqlite3 connection pointer.
        # A lock serializes DBAPI calls to avoid sqlite3 driver InterfaceError while verifying atomic conditional updates.
        db_lock = threading.Lock()

        def worker():
            try:
                with self.app.app_context():
                    barrier.wait(timeout=5)
                    with db_lock:
                        tok = self.provider._acquire_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id)
                        if tok:
                            acquired_tokens.append(tok)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(acquired_tokens), 1)

        winner_tok = acquired_tokens[0]
        released = self.provider._release_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id, winner_tok)
        self.assertTrue(released)

        new_tok = self.provider._acquire_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id)
        self.assertIsNotNone(new_tok)
        self.provider._release_subscription_claim(self.dummy_sub.id, self.dummy_sub.organization_id, new_tok)

    # ── Test 50: Billing Period Idempotency Blocks Duplicate PDN (Phase 5.3 & 5.4) ──
    def test_billing_period_idempotency_blocks_duplicate_pdn(self):
        """Phase 5.4: notify_redemption blocked if a durable reservation exists for the billing period under diff order."""
        self.dummy_sub.current_period_start = datetime(2026, 11, 1, 0, 0, 0)
        expected_bpk = f"{self.dummy_sub.id}_2026-11"

        # Durable reservation created by a prior order
        resv = SaaSBillingPeriodReservation(
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            billing_period_key=expected_bpk,
            merchant_order_id="ORDER_PREV_RESERVED",
            status=BillingPeriodReservationStatus.RESERVED.value,
        )
        db.session.add(resv)
        db.session.commit()

        res = self.provider.notify_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_NEW_ATTEMPT_DIFF_ID",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("already reserved", res.error_message)

    # ── Test 51: Billing Period Idempotency Blocks Duplicate Execute (Phase 5.3 & 5.4) ──
    def test_billing_period_idempotency_blocks_duplicate_execute_redemption(self):
        """Phase 5.4: execute_redemption blocked if a completed durable reservation exists for billing period."""
        self.dummy_sub.current_period_start = datetime(2026, 11, 1, 0, 0, 0)
        expected_bpk = f"{self.dummy_sub.id}_2026-11"

        pdn = SaaSPreDebitNotification(
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            mandate_id=self.dummy_sub.active_mandate_id,
            merchant_order_id="ORDER_NEW_REDEEM_SAME_PERIOD",
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=expected_bpk,
        )
        # Prior completed reservation under different order
        resv = SaaSBillingPeriodReservation(
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            billing_period_key=expected_bpk,
            merchant_order_id="ORDER_PAID_ALREADY",
            status=BillingPeriodReservationStatus.COMPLETED.value,
        )
        db.session.add_all([pdn, resv])
        db.session.commit()

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_NEW_REDEEM_SAME_PERIOD",
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "FAILED")
        self.assertIn("successful debit already exists for billing period", res.error_message)

    # ── Test 52: Period Advancement Allows Legitimate Renewal (Phase 5.3 & 5.4) ───────
    def test_billing_period_advancement_allows_new_debit(self):
        """Phase 5.4: Advancing to a new billing period creates a distinct durable reservation and allows debit."""
        # Previous period 2026-11 completed
        prev_resv = SaaSBillingPeriodReservation(
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            billing_period_key=f"{self.dummy_sub.id}_2026-11",
            merchant_order_id="ORDER_NOVEMBER_PAID",
            status=BillingPeriodReservationStatus.COMPLETED.value,
        )
        # Now subscription period advances to 2026-12
        self.dummy_sub.current_period_start = datetime(2026, 12, 1, 0, 0, 0)

        mandate = SaaSMandate(
            id=10,
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_V2_7_99",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
            auto_debit=False,
        )
        pdn = SaaSPreDebitNotification(
            id=15,
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            mandate_id=10,
            merchant_order_id="ORDER_DECEMBER_RENEWAL",
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=f"{self.dummy_sub.id}_2026-12",
        )
        db.session.add_all([prev_resv, mandate, pdn])
        db.session.commit()

        self.mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_NEW_PERIOD_001", "amount": 118000}],
        }

        res = self.provider.execute_redemption(
            subscription=self.dummy_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_DECEMBER_RENEWAL",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.provider_payment_id, "TXN_NEW_PERIOD_001")

        # Verify durable reservation was persisted and transitioned to COMPLETED
        resv_dec = SaaSBillingPeriodReservation.query.filter_by(
            organization_id=self.dummy_sub.organization_id,
            subscription_id=self.dummy_sub.id,
            billing_period_key=f"{self.dummy_sub.id}_2026-12",
        ).first()
        self.assertIsNotNone(resv_dec)
        self.assertEqual(resv_dec.status, BillingPeriodReservationStatus.COMPLETED.value)

    # ── Test 53: Tenant Resolution Ignores Client Payload (Phase 5.4) ───────────
    def test_tenant_resolution_ignores_client_supplied_org_id(self):
        """Phase 5.4: verify_payment strictly ignores forged client payload organizationId."""
        mandate = SaaSMandate(
            id=25,
            organization_id=7,
            subscription_id=self.dummy_sub.id,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_HONEST_ORG7",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.commit()

        with patch.object(self.provider, "check_subscription_status") as mock_check:
            mock_check.return_value = VerificationResult(success=True, status="ACTIVE", merchant_subscription_id="MSUB_HONEST_ORG7")

            # Client maliciously supplies organizationId = 99999
            res = self.provider.verify_payment(
                payload={"merchantSubscriptionId": "MSUB_HONEST_ORG7", "organizationId": 99999, "organization_id": 99999},
                organization_id=None,
            )

            mock_check.assert_called_once_with("MSUB_HONEST_ORG7", organization_id=7)
            self.assertTrue(res.success)

    # ── Test 54: Tenant Mismatch with Authenticated Session (Phase 5.4) ─────────
    def test_tenant_mismatch_with_authenticated_session_returns_unresolved(self):
        """Phase 5.4: verify_payment returns UNRESOLVED when authenticated org_id conflicts with internal record."""
        mandate = SaaSMandate(
            id=26,
            organization_id=7,
            subscription_id=self.dummy_sub.id,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_ORG7_MANDATE",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.commit()

        # Authenticated user is from org 99, but internal record is org 7
        res = self.provider.verify_payment(
            payload={"merchantSubscriptionId": "MSUB_ORG7_MANDATE"},
            organization_id=99,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "UNRESOLVED")
        self.assertIn("Tenant context mismatch", res.error_message)

    # ── Test 55: Ambiguous Tenant Between Order and Mandate (Phase 5.4) ─────────
    def test_ambiguous_tenant_between_order_and_mandate_returns_unresolved(self):
        """Phase 5.4: verify_payment returns UNRESOLVED when order and mandate belong to conflicting tenants."""
        org_other = Organization(id=8, name="Other Corp", unique_code="OTHER")
        sub_other = SaaSSubscription(id=108, organization_id=8, plan_id=1, payment_provider="phonepe_v2")
        db.session.add_all([org_other, sub_other])

        mandate = SaaSMandate(
            id=30,
            organization_id=7,
            subscription_id=self.dummy_sub.id,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_ORG7_RECORD",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        pdn_other = SaaSPreDebitNotification(
            id=40,
            organization_id=8,  # Different org!
            subscription_id=108,
            mandate_id=30,
            merchant_order_id="ORDER_ORG8_RECORD",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add_all([mandate, pdn_other])
        db.session.commit()

        res = self.provider.verify_payment(
            payload={"merchantOrderId": "ORDER_ORG8_RECORD", "merchantSubscriptionId": "MSUB_ORG7_RECORD"},
            organization_id=None,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "UNRESOLVED")
        self.assertIn("conflicting tenant", res.error_message.lower())

    # ── Test 56: Relational Mismatch Between PDN and Mandate (Phase 5.4) ────────
    def test_relational_mismatch_between_pdn_and_mandate_returns_unresolved(self):
        """Phase 5.4: verify_payment returns UNRESOLVED when PDN mandate_id does not match Mandate id."""
        mandate1 = SaaSMandate(
            id=35,
            organization_id=7,
            subscription_id=self.dummy_sub.id,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_MANDATE_35",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        mandate2 = SaaSMandate(
            id=36,
            organization_id=7,
            subscription_id=self.dummy_sub.id,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_MANDATE_36",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        pdn = SaaSPreDebitNotification(
            id=50,
            organization_id=7,
            subscription_id=self.dummy_sub.id,
            mandate_id=35,  # Points to mandate 35
            merchant_order_id="ORDER_LINKED_TO_35",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add_all([mandate1, mandate2, pdn])
        db.session.commit()

        # Verification payload mixes ORDER_LINKED_TO_35 with MSUB_MANDATE_36
        res = self.provider.verify_payment(
            payload={"merchantOrderId": "ORDER_LINKED_TO_35", "merchantSubscriptionId": "MSUB_MANDATE_36"},
            organization_id=7,
        )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "UNRESOLVED")
        self.assertIn("Relational mismatch", res.error_message)

    # ── Test 57: Webhook Authentication Verification Contract (Phase 5.4.1) ───
    def test_webhook_multi_mode_authentication_verification(self):
        """Phase 5.4.1: verify_webhook enforces documented PhonePe Basic Auth (SHA256/Base64) and rejects invalid/missing/malformed."""
        import base64
        payload_dict = {
            "merchantId": "PGTESTPAYUAT",
            "eventId": "EVT_TEST_123",
            "event": "SUBSCRIPTION_ACTIVE",
            "payload": {"merchantSubscriptionId": "MSUB_V2_TEST"},
        }
        payload_bytes = json.dumps(payload_dict).encode("utf-8")

        # 1. Merchant ID mismatch
        mismatch_payload = json.dumps({"merchantId": "WRONG_MERCHANT", "eventId": "EVT_99"}).encode("utf-8")
        res_mismatch = self.provider.verify_webhook(mismatch_payload, {"Authorization": "Basic test"})
        self.assertFalse(res_mismatch.is_valid)
        self.assertIn("Merchant ID mismatch", res_mismatch.error_message)

        with patch.dict(self.app.config, {"PHONEPE_WEBHOOK_USERNAME": "test_wh_user", "PHONEPE_WEBHOOK_PASSWORD": "test_wh_password"}):
            # 2. Missing Authorization header
            res_missing = self.provider.verify_webhook(payload_bytes, {})
            self.assertFalse(res_missing.is_valid)
            self.assertEqual(res_missing.error_message, "Missing Authorization header")

            # 3. Malformed Authorization header (non-Basic scheme)
            res_malformed_scheme = self.provider.verify_webhook(payload_bytes, {"Authorization": "Bearer sometoken"})
            self.assertFalse(res_malformed_scheme.is_valid)
            self.assertIn("Malformed Authorization header; expected Basic authentication", res_malformed_scheme.error_message)

            # 4. Malformed Authorization header (empty token)
            res_empty_token = self.provider.verify_webhook(payload_bytes, {"Authorization": "Basic  "})
            self.assertFalse(res_empty_token.is_valid)
            self.assertIn("empty Basic authentication token", res_empty_token.error_message)

            # 5. Invalid credentials (wrong digest)
            res_bad_auth = self.provider.verify_webhook(payload_bytes, {"Authorization": "Basic wrong_token_value"})
            self.assertFalse(res_bad_auth.is_valid)
            self.assertEqual(res_bad_auth.error_message, "Invalid webhook authorization credentials")

            # 6. Valid documented PhonePe SHA-256 hash
            correct_sha256 = hashlib.sha256(b"test_wh_user:test_wh_password").hexdigest()
            res_good_sha = self.provider.verify_webhook(payload_bytes, {"Authorization": f"Basic {correct_sha256}"})
            self.assertTrue(res_good_sha.is_valid)
            self.assertEqual(res_good_sha.event_id, "EVT_TEST_123")

            # 7. Valid standard RFC 7617 Base64
            correct_base64 = base64.b64encode(b"test_wh_user:test_wh_password").decode("utf-8")
            res_good_b64 = self.provider.verify_webhook(payload_bytes, {"Authorization": f"Basic {correct_base64}"})
            self.assertTrue(res_good_b64.is_valid)
            self.assertEqual(res_good_b64.event_id, "EVT_TEST_123")

            # 8. Arbitrary HMAC schemes rejected
            res_hmac = self.provider.verify_webhook(payload_bytes, {"Authorization": "HMAC-SHA256 random_hash"})
            self.assertFalse(res_hmac.is_valid)
            self.assertIn("Malformed Authorization header", res_hmac.error_message)

    def test_webhook_event_deduplication_composite_fallback_distinguishes_event_types(self):
        """Phase 5.4.2 Requirement 4: When PhonePe eventId is missing, distinct lifecycle event types for the same order produce different event IDs."""
        correct_sha256 = hashlib.sha256(b"test_wh_user:test_wh_password").hexdigest()
        headers = {"Authorization": f"Basic {correct_sha256}"}

        # Event 1: Pre-Debit Notification Success for order ORDER_SHARED_100
        payload_notif = {
            "merchantId": "PGTESTPAYUAT",
            "event": "NOTIFICATION_SUCCESS",
            "payload": {
                "merchantOrderId": "ORDER_SHARED_100",
                "merchantSubscriptionId": "MSUB_SHARED_200",
                "state": "NOTIFIED",
                "amount": 118000,
            }
        }
        res_notif = self.provider.verify_webhook(json.dumps(payload_notif).encode("utf-8"), headers)
        self.assertTrue(res_notif.is_valid)
        self.assertEqual(res_notif.event_id, "PGTESTPAYUAT:NOTIFICATION_SUCCESS:ORDER_SHARED_100:MSUB_SHARED_200:NOTIFIED")

        # Event 2: Payment Success for SAME order ORDER_SHARED_100
        payload_payment = {
            "merchantId": "PGTESTPAYUAT",
            "event": "PAYMENT_SUCCESS",
            "payload": {
                "merchantOrderId": "ORDER_SHARED_100",
                "merchantSubscriptionId": "MSUB_SHARED_200",
                "state": "COMPLETED",
                "amount": 118000,
            }
        }
        res_payment = self.provider.verify_webhook(json.dumps(payload_payment).encode("utf-8"), headers)
        self.assertTrue(res_payment.is_valid)
        self.assertEqual(res_payment.event_id, "PGTESTPAYUAT:PAYMENT_SUCCESS:ORDER_SHARED_100:MSUB_SHARED_200:COMPLETED")

        # Event 3: Payment Failed for SAME order ORDER_SHARED_100
        payload_failed = {
            "merchantId": "PGTESTPAYUAT",
            "event": "PAYMENT_FAILED",
            "payload": {
                "merchantOrderId": "ORDER_SHARED_100",
                "merchantSubscriptionId": "MSUB_SHARED_200",
                "state": "FAILED",
                "amount": 118000,
            }
        }
        res_failed = self.provider.verify_webhook(json.dumps(payload_failed).encode("utf-8"), headers)
        self.assertTrue(res_failed.is_valid)
        self.assertEqual(res_failed.event_id, "PGTESTPAYUAT:PAYMENT_FAILED:ORDER_SHARED_100:MSUB_SHARED_200:FAILED")

        # Verify that all 3 event IDs are distinct despite sharing the exact same merchantOrderId
        event_ids = {res_notif.event_id, res_payment.event_id, res_failed.event_id}
        self.assertEqual(len(event_ids), 3, "Lifecycle events sharing merchantOrderId must not collide or suppress each other")

        # Verify that an actual duplicate of Event 2 produces the identical event ID
        res_payment_duplicate = self.provider.verify_webhook(json.dumps(payload_payment).encode("utf-8"), headers)
        self.assertEqual(res_payment_duplicate.event_id, res_payment.event_id)

    def test_billing_period_reservation_execution_lease_crash_recovery(self):
        """Phase 5.4.2 Requirement 1: Worker crash recovery after lease expires allows new worker to acquire ownership reusing same order ID."""
        bpk = "sub_99_2026-11"
        order_id = "ORD_CRASH_RECOVER_01"

        # Worker 1 reserves period but crashes before initiating gateway call (redemption_initiated_at is None)
        success1, err1, res1 = self.provider._reserve_billing_period(
            subscription_id=99,
            organization_id=7,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            owner_token="worker_1_token",
        )
        self.assertTrue(success1)
        self.assertEqual(res1.owner_token, "worker_1_token")
        self.assertIsNone(res1.redemption_initiated_at)

        # Worker 2 attempts while Worker 1 lease is still active -> blocked
        success2, err2, res2 = self.provider._reserve_billing_period(
            subscription_id=99,
            organization_id=7,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            owner_token="worker_2_token",
        )
        self.assertFalse(success2)
        self.assertIn("currently owned by active worker", err2)

        # Simulate Worker 1 lease expiration
        res1.owner_expires_at = datetime.utcnow() - timedelta(seconds=10)
        db.session.commit()

        # Worker 2 attempts after Worker 1 lease expired -> safely acquires ownership reusing same order
        success3, err3, res3 = self.provider._reserve_billing_period(
            subscription_id=99,
            organization_id=7,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            owner_token="worker_2_token",
        )
        self.assertTrue(success3)
        self.assertIsNone(err3)
        self.assertEqual(res3.owner_token, "worker_2_token")
        self.assertEqual(res3.merchant_order_id, order_id)

    def test_billing_period_reservation_lease_expired_after_gateway_dispatch_blocks_second_debit(self):
        """Phase 5.4.2 Requirement 1: Expired lease cannot authorize a second debit if gateway was already initiated; outcome must remain PENDING_RECONCILIATION."""
        bpk = "sub_99_2026-12"
        order_id = "ORD_DISPATCHED_CRASH_02"

        # Worker 1 reserves period and dispatches to PhonePe (redemption_initiated_at is set)
        success1, err1, res1 = self.provider._reserve_billing_period(
            subscription_id=99,
            organization_id=7,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            owner_token="worker_1_token",
        )
        self.assertTrue(success1)
        res1.redemption_initiated_at = datetime.utcnow() - timedelta(minutes=5)
        res1.owner_expires_at = datetime.utcnow() - timedelta(minutes=3)  # lease expired
        db.session.commit()

        # Worker 2 attempts to reserve same order after lease expiry
        # Because redemption was already dispatched to gateway, Worker 2 must NOT be allowed to initiate a second debit
        success2, err2, res2 = self.provider._reserve_billing_period(
            subscription_id=99,
            organization_id=7,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            owner_token="worker_2_token",
        )
        self.assertFalse(success2)
        self.assertIn("was already initiated with gateway", err2)
        self.assertEqual(res2.status, BillingPeriodReservationStatus.PENDING_RECONCILIATION.value)


if __name__ == "__main__":
    unittest.main()

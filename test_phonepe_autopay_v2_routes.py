"""
test_phonepe_autopay_v2_routes.py
==================================
Integration tests for PhonePe V2 and V3 callback and webhook routing,
trusted tenant context isolation, and duplicate debit prevention.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from flask import Flask
from flask_login import LoginManager, login_user
from sqlalchemy.pool import StaticPool

from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSTransaction,
    SaaSTransactionStatus,
    SaaSWebhookEvent,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    User,
    UserRole,
)
from routes.saas import saas_bp
from services.billing.billing_service import BillingService
from services.billing.payment_provider import VerificationResult, WebhookResult, RedemptionResult


class TestPhonePeAutoPayV2Routes(unittest.TestCase):
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
        cls.app.config["SECRET_KEY"] = "test-secret-key-phase53"
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["BILLING_DEFAULT_PROVIDER"] = "phonepe"

        # Initialize LoginManager
        login_manager = LoginManager()
        login_manager.init_app(cls.app)

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        # Register blueprint
        cls.app.register_blueprint(saas_bp)

        # Register dummy routes needed by redirect() and url_for()
        @cls.app.route("/home")
        def home_page():
            return "home_page_ok"

        @cls.app.route("/test-login/<int:uid>")
        def test_login(uid):
            user = db.session.get(User, uid)
            if user:
                login_user(user)
                return "logged_in"
            return "user_not_found", 404

        db.init_app(cls.app)
        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

        BillingService.reset_provider_cache()

        # Clean DB tables
        db.session.query(SaaSWebhookEvent).delete()
        db.session.query(SaaSTransaction).delete()
        db.session.query(SaaSRedemptionAttempt).delete()
        db.session.query(SaaSPreDebitNotification).delete()
        db.session.query(SaaSMandate).delete()
        db.session.query(SaaSSubscription).delete()
        db.session.query(User).delete()
        db.session.query(Organization).delete()
        db.session.query(Plan).delete()
        db.session.commit()

        # Seed baseline entities
        self.plan = Plan(
            id=1,
            name="Pro",
            display_name="Professional Plan",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )

        # ── V3 Org, User, Subscription ──
        self.v3_org = Organization(id=10, name="Legacy V3 Org", unique_code="V3ORG")
        self.v3_user = User(
            id=101,
            username="v3admin",
            email="v3admin@example.com",
            password="hashed_test_password",
            organization_id=10,
            role=UserRole.ADMIN,
        )
        self.v3_sub = SaaSSubscription(
            id=1001,
            organization_id=10,
            plan_id=1,
            payment_provider="phonepe",
            provider_subscription_id="MSUB_V3_10_1001",
            status=SaaSSubscriptionStatus.PENDING,
            billing_cycle=SaaSBillingCycle.MONTHLY,
            amount_paise=118000,
        )

        # ── V2 Org, User, Subscription, Mandate, PDN ──
        self.v2_org = Organization(id=20, name="Modern V2 Org", unique_code="V2ORG")
        self.v2_user = User(
            id=201,
            username="v2admin",
            email="v2admin@example.com",
            password="hashed_test_password",
            organization_id=20,
            role=UserRole.ADMIN,
        )
        self.v2_sub = SaaSSubscription(
            id=2001,
            organization_id=20,
            plan_id=1,
            payment_provider="phonepe_v2",
            provider_subscription_id="MSUB_V2_20_2001",
            status=SaaSSubscriptionStatus.ACTIVE,
            billing_cycle=SaaSBillingCycle.MONTHLY,
            amount_paise=118000,
            active_mandate_id=201,
            current_period_start=datetime(2026, 11, 1, 0, 0, 0),
            current_period_end=datetime(2026, 12, 1, 0, 0, 0),
        )
        self.v2_mandate = SaaSMandate(
            id=201,
            organization_id=20,
            subscription_id=2001,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_subscription_id="PP_SUB_V2_999",
            mandate_reference="MN_V2_ABC123",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
            auto_debit=False,
        )
        self.v2_pdn = SaaSPreDebitNotification(
            id=301,
            organization_id=20,
            subscription_id=2001,
            mandate_id=201,
            merchant_order_id="ORDER_V2_PDN_301",
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            billing_period_key="2001_2026-11",
        )

        db.session.add_all([
            self.plan,
            self.v3_org, self.v3_user, self.v3_sub,
            self.v2_org, self.v2_user, self.v2_sub, self.v2_mandate, self.v2_pdn,
        ])
        db.session.commit()

    def tearDown(self):
        try:
            db.session.rollback()
        except Exception:
            pass
        BillingService.reset_provider_cache()
        self.app_context.pop()

    # ── Group A: PhonePe V3 Callback & Webhook Routing ─────────────────────────

    def test_v3_callback_no_mandate_uses_legacy_provider(self):
        """V3 callback without a SaaSMandate routes to the legacy 'phonepe' provider."""
        mock_provider = MagicMock()
        mock_provider.name = "phonepe"
        mock_provider.verify_payment.return_value = VerificationResult(
            success=False,
            status="FAILED",
            merchant_subscription_id="MSUB_V3_10_1001",
            error_message="Test payment cancelled",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_provider
            response = self.client.get(
                "/saas/phonepe-callback?merchantTransactionId=V3_TXN_001&merchantSubscriptionId=MSUB_V3_10_1001"
            )

            # Verification: BillingService was asked for "phonepe"
            mock_get_provider.assert_called_with("phonepe")
            self.assertEqual(response.status_code, 302)  # Redirects to checkout on failure

    def test_v3_webhook_no_mandate_uses_legacy_provider(self):
        """V3 webhook without a matching V2 mandate routes to the legacy 'phonepe' provider."""
        mock_v3_provider = MagicMock()
        mock_v3_provider.name = "phonepe"
        mock_v3_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_V3_001",
            event_type="PAYMENT_SUCCESS",
            merchant_subscription_id="MSUB_V3_10_1001",
            amount_paise=118000,
            state="COMPLETED",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v3_provider
            payload = json.dumps({"merchantSubscriptionId": "MSUB_V3_10_1001", "event": "PAYMENT_SUCCESS"})
            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
            )

            mock_get_provider.assert_called_with("phonepe")
            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertEqual(data.get("event_id"), "EVT_V3_001")

    # ── Group B: PhonePe V2 Callback & Webhook Routing ─────────────────────────

    def test_v2_callback_with_mandate_uses_v2_provider(self):
        """V2 callback matching a V2 SaaSMandate routes to 'phonepe_v2' provider."""
        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_subscription_id="PP_SUB_V2_999",
            provider_transaction_id="TXN_V2_RET_123",
            provider_payment_id="PAY_V2_456",
            amount_paise=118000,
        )

        # Log in V2 user to verify server-side org_id passing
        self.client.get(f"/test-login/{self.v2_user.id}")

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.get(
                "/saas/phonepe-callback?merchantSubscriptionId=MSUB_V2_20_MANDATE"
            )

            mock_get_provider.assert_called_with("phonepe_v2")
            mock_v2_provider.verify_payment.assert_called_once()
            # Verify organization_id passed was 20 (from server-side current_user.organization_id)
            call_kwargs = mock_v2_provider.verify_payment.call_args[1]
            self.assertEqual(call_kwargs.get("organization_id"), 20)
            self.assertEqual(response.status_code, 302)  # Redirects to home_page on success

    def test_v2_callback_with_pdn_order_id_uses_v2_provider(self):
        """V2 callback matching a SaaSPreDebitNotification merchant_order_id routes to 'phonepe_v2'."""
        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=False,
            status="PENDING",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            error_message="Order pending reconciliation",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.get(
                "/saas/phonepe-callback?merchantOrderId=ORDER_V2_PDN_301"
            )

            mock_get_provider.assert_called_with("phonepe_v2")

    def test_v2_webhook_with_mandate_acknowledged(self):
        """V2 webhook event matching a V2 mandate is acknowledged immediately without V3 processing."""
        payload = json.dumps({
            "merchantSubscriptionId": "MSUB_V2_20_MANDATE",
            "event": "SUBSCRIPTION_ACTIVE",
        })
        response = self.client.post(
            "/saas/phonepe-webhook",
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("status"), "v2_event_acknowledged")

    def test_v2_webhook_with_order_id_acknowledged(self):
        """V2 webhook event matching a V2 pre-debit order is acknowledged without V3 processing."""
        payload = json.dumps({
            "merchantOrderId": "ORDER_V2_PDN_301",
            "event": "PAYMENT_SUCCESS",
        })
        response = self.client.post(
            "/saas/phonepe-webhook",
            data=payload,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data.get("status"), "v2_event_acknowledged")

    # ── Group C: Trusted Tenant Context Security ───────────────────────────────

    def test_verify_payment_uses_server_side_org_id(self):
        """POST /saas/api/verify-payment ignores client-supplied organization_id and enforces authenticated session org.id."""
        self.client.get(f"/test-login/{self.v2_user.id}")

        mock_provider = MagicMock()
        mock_provider.name = "phonepe_v2"
        mock_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription"):
            mock_get_provider.return_value = mock_provider

            # Client deliberately sends forged organizationId
            forged_payload = {
                "organizationId": 99999,
                "organization_id": 99999,
                "merchantOrderId": "ORDER_FORGED_ORG",
            }

            response = self.client.post(
                "/saas/api/verify-payment",
                data=json.dumps(forged_payload),
                content_type="application/json",
            )

            self.assertEqual(response.status_code, 200)
            mock_provider.verify_payment.assert_called_once()
            call_kwargs = mock_provider.verify_payment.call_args[1]
            # Must strictly be authenticated user's organization.id (20), NOT 99999
            self.assertEqual(call_kwargs.get("organization_id"), 20)

    def test_callback_without_auth_passes_none_org_id(self):
        """Unauthenticated /phonepe-callback does not trust query params for organization_id."""
        mock_provider = MagicMock()
        mock_provider.name = "phonepe"
        mock_provider.verify_payment.return_value = VerificationResult(
            success=False,
            status="FAILED",
            merchant_subscription_id="",
            error_message="Verification failed",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_provider

            self.client.get(
                "/saas/phonepe-callback?merchantOrderId=ORDER_ANON&organizationId=99999&organization_id=99999"
            )

            mock_provider.verify_payment.assert_called_once()
            call_kwargs = mock_provider.verify_payment.call_args[1]
            # Since user is unauthenticated, org_id passed to provider must be None
            self.assertIsNone(call_kwargs.get("organization_id"))

    # ── Group D: Duplicate Debit Prevention across Requests ────────────────────

    def test_duplicate_debit_same_billing_period_blocked(self):
        """Two debit attempts with different order IDs in the same billing period are strictly blocked."""
        provider = BillingService.get_provider("phonepe_v2")

        # Record a COMPLETED redemption attempt for 2026-11
        prior_attempt = SaaSRedemptionAttempt(
            organization_id=20,
            subscription_id=2001,
            notification_id=301,
            merchant_order_id="ORDER_FIRST_SUCCESS_001",
            amount_paise=118000,
            status=RedemptionAttemptStatus.COMPLETED.value,
            billing_period_key="2001_2026-11",
        )
        db.session.add(prior_attempt)
        db.session.commit()

        # Attempt 1: New PDN for a second debit in the SAME period is blocked by notify_redemption
        notify_res = provider.notify_redemption(
            subscription=self.v2_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_SECOND_ATTEMPT_DIFF_ID",
        )
        self.assertFalse(notify_res.success)
        self.assertEqual(notify_res.status, "FAILED")
        self.assertIn("successful debit already exists for billing period", notify_res.error_message)

        # Attempt 2: Even if a notification was somehow persisted (or simulated),
        # execute_redemption itself enforces Safety Check 7 and blocks the debit.
        second_pdn = SaaSPreDebitNotification(
            organization_id=20,
            subscription_id=2001,
            mandate_id=201,
            merchant_order_id="ORDER_SECOND_ATTEMPT_DIFF_ID",
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key="2001_2026-11",
        )
        db.session.add(second_pdn)
        db.session.commit()

        exec_res = provider.execute_redemption(
            subscription=self.v2_sub,
            amount_paise=118000,
            merchant_transaction_id="ORDER_SECOND_ATTEMPT_DIFF_ID",
        )
        self.assertFalse(exec_res.success)
        self.assertEqual(exec_res.status, "FAILED")
        self.assertIn("successful debit already exists for billing period", exec_res.error_message)

    # ── Group E: Regression / V3 Billing Untouched ──────────────────────────────

    def test_v3_webhook_subscription_active_flow(self):
        """Preserves legacy V3 webhook activation logic completely."""
        mock_v3_provider = MagicMock()
        mock_v3_provider.name = "phonepe"
        mock_v3_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_V3_ACTIVE_99",
            event_type="SUBSCRIPTION_ACTIVE",
            merchant_subscription_id="MSUB_V3_10_1001",
            provider_subscription_id="PP_V3_SUB_777",
            amount_paise=118000,
            state="ACTIVE",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v3_provider
            payload = json.dumps({"merchantSubscriptionId": "MSUB_V3_10_1001"})
            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json().get("status"), "success")

            # Check that V3 subscription was activated
            sub = db.session.get(SaaSSubscription, 1001)
            self.assertEqual(sub.status, SaaSSubscriptionStatus.ACTIVE)
            self.assertEqual(sub.provider_subscription_id, "PP_V3_SUB_777")


if __name__ == "__main__":
    unittest.main()

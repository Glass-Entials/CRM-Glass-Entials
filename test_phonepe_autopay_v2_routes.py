"""
test_phonepe_autopay_v2_routes.py
==================================
Integration tests for PhonePe V2 and V3 callback and webhook routing,
trusted tenant context isolation, and duplicate debit prevention.
"""

from __future__ import annotations

import hashlib
import hmac
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
    SaaSBillingPeriodReservation,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    BillingPeriodReservationStatus,
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
        cls.app.config["PHONEPE_ENV"] = "UAT"
        cls.app.config["PHONEPE_MERCHANT_ID"] = "PGTESTPAYUAT"
        cls.app.config["PHONEPE_CLIENT_ID"] = "test_client_id"
        cls.app.config["PHONEPE_CLIENT_SECRET"] = "test_client_secret"
        cls.app.config["PHONEPE_CLIENT_VERSION"] = 1
        cls.app.config["PHONEPE_SALT_KEY"] = "test_salt_key_routes"
        cls.app.config["PHONEPE_SALT_INDEX"] = "1"
        cls.app.config["PHONEPE_WEBHOOK_USERNAME"] = "webhook_test_user"
        cls.app.config["PHONEPE_WEBHOOK_PASSWORD"] = "webhook_test_pass"

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
        db.session.query(SaaSBillingPeriodReservation).delete()
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

    def test_v2_webhook_invalid_authentication_rejected(self):
        """V2 webhook request with invalid or missing authentication is strictly rejected with HTTP 401."""
        payload = json.dumps({
            "merchantSubscriptionId": "MSUB_V2_20_MANDATE",
            "event": "SUBSCRIPTION_ACTIVE",
        })
        # Missing auth headers -> 401
        response = self.client.post(
            "/saas/phonepe-webhook",
            data=payload,
            content_type="application/json",
            headers={"Authorization": "Basic invalid_credentials"},
        )
        self.assertEqual(response.status_code, 401)
        data = response.get_json()
        self.assertIn("error", data)

    def test_v2_webhook_with_mandate_reconciled(self):
        """V2 webhook event matching a V2 mandate with valid auth triggers S2S reconciliation and returns HTTP 200."""
        payload = json.dumps({
            "merchantSubscriptionId": "MSUB_V2_20_MANDATE",
            "event": "SUBSCRIPTION_ACTIVE",
            "eventId": "EVT_V2_MANDATE_001",
        })
        # Compute valid Basic Auth token matching configured credentials
        auth_token = hashlib.sha256("webhook_test_user:webhook_test_pass".encode("utf-8")).hexdigest()
        headers = {
            "Authorization": f"Basic {auth_token}",
        }

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_V2_MANDATE_001",
            event_type="SUBSCRIPTION_ACTIVE",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            state="ACTIVE",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
                headers=headers,
            )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertTrue(data.get("reconciled"))
            self.assertEqual(data.get("event_id"), "EVT_V2_MANDATE_001")

            # Verify S2S mandate sync was triggered
            mock_v2_provider.sync_mandate_status.assert_called_once()
            called_mandate = mock_v2_provider.sync_mandate_status.call_args[0][0]
            self.assertEqual(called_mandate.id, 201)

            # Verify durable webhook event logged
            evt = SaaSWebhookEvent.query.filter_by(event_id="EVT_V2_MANDATE_001").first()
            self.assertIsNotNone(evt)
            self.assertEqual(evt.payment_provider, "phonepe_v2")

    def test_v2_webhook_with_order_id_reconciled(self):
        """V2 webhook event matching a V2 pre-debit order triggers S2S order reconciliation and returns HTTP 200."""
        payload = json.dumps({
            "merchantOrderId": "ORDER_V2_PDN_301",
            "event": "PAYMENT_SUCCESS",
            "eventId": "EVT_V2_ORDER_002",
        })
        auth_token = hashlib.sha256("webhook_test_user:webhook_test_pass".encode("utf-8")).hexdigest()
        headers = {
            "Authorization": f"Basic {auth_token}",
        }

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_V2_ORDER_002",
            event_type="PAYMENT_SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_payment_id="ORDER_V2_PDN_301",
            state="COMPLETED",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
                headers=headers,
            )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertTrue(data.get("reconciled"))

            # Verify S2S mandate sync was called
            mock_v2_provider.sync_mandate_status.assert_called_once()
            evt = SaaSWebhookEvent.query.filter_by(event_id="EVT_V2_ORDER_002").first()
            self.assertIsNotNone(evt)

    def test_v2_webhook_idempotent_duplicate_acknowledged(self):
        """Repeated delivery of an already processed V2 webhook event returns already_processed without re-reconciling."""
        prior_evt = SaaSWebhookEvent(
            payment_provider="phonepe_v2",
            event_id="EVT_V2_DUPLICATE_999",
            event_type="SUBSCRIPTION_ACTIVE",
            payload_summary="{}",
        )
        db.session.add(prior_evt)
        db.session.commit()

        auth_token = hashlib.sha256("webhook_test_user:webhook_test_pass".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        payload = json.dumps({
            "merchantSubscriptionId": "MSUB_V2_20_MANDATE",
            "eventId": "EVT_V2_DUPLICATE_999",
            "event": "SUBSCRIPTION_ACTIVE",
        })

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_V2_DUPLICATE_999",
            event_type="SUBSCRIPTION_ACTIVE",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v2_provider
            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
                headers=headers,
            )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data.get("status"), "already_processed")
            # Must NOT call sync_mandate_status on duplicate
            mock_v2_provider.sync_mandate_status.assert_not_called()

    def test_v2_auto_debit_webhook_reconciliation_invokes_provider_with_attempt_object(self):
        """Phase 5.4.1: V2 auto-debit webhook resolves/creates SaaSRedemptionAttempt and invokes provider with attempt object."""
        # Update self.v2_pdn to be an auto-debit notification
        self.v2_pdn.auto_debit_snapshot = True
        db.session.commit()

        auth_token = hashlib.sha256("webhook_test_user:webhook_test_pass".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        payload = json.dumps({
            "merchantOrderId": "ORDER_V2_PDN_301",
            "event": "PAYMENT_SUCCESS",
            "eventId": "EVT_AUTODEBIT_001",
        })

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_AUTODEBIT_001",
            event_type="PAYMENT_SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_payment_id="ORDER_V2_PDN_301",
            state="COMPLETED",
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
                headers=headers,
            )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(data.get("status"), "success")
            self.assertTrue(data.get("reconciled"))

            # Assert reconcile_redemption_status was called with SaaSRedemptionAttempt object!
            mock_v2_provider.reconcile_redemption_status.assert_called_once()
            call_arg = mock_v2_provider.reconcile_redemption_status.call_args[0][0]
            self.assertIsInstance(call_arg, SaaSRedemptionAttempt)
            self.assertEqual(call_arg.merchant_order_id, "ORDER_V2_PDN_301")
            self.assertEqual(call_arg.organization_id, 20)
            self.assertEqual(call_arg.notification_id, self.v2_pdn.id)

    def test_setup_order_reconciliation_uses_internal_order_id_not_transaction_or_event_id(self):
        """Phase 5.4.1: Initial setup order reconciliation strictly uses internal merchant_order_id, never transactionId or eventId."""
        # Set subscription to PENDING with specific provider_order_id
        self.v2_sub.status = SaaSSubscriptionStatus.PENDING
        self.v2_sub.provider_order_id = "MT_V2_INTERNAL_SETUP_555"
        self.v2_mandate.mandate_status = "ACTIVE"
        db.session.commit()

        auth_token = hashlib.sha256("webhook_test_user:webhook_test_pass".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        payload = json.dumps({
            "merchantSubscriptionId": "MSUB_V2_20_MANDATE",
            "transactionId": "TXN_GATEWAY_SUBSTITUTE_TRAP",
            "eventId": "EVT_SETUP_001",
            "event": "SUBSCRIPTION_ACTIVE",
        })

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_webhook.return_value = WebhookResult(
            is_valid=True,
            event_id="EVT_SETUP_001",
            event_type="SUBSCRIPTION_ACTIVE",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_payment_id="TXN_GATEWAY_SUBSTITUTE_TRAP",
            state="ACTIVE",
        )
        mock_v2_provider.check_transaction_status.return_value = VerificationResult(
            success=True,
            status="COMPLETED",
            merchant_subscription_id="MT_V2_INTERNAL_SETUP_555",
            provider_transaction_id="TXN_GATEWAY_SUBSTITUTE_TRAP",
            amount_paise=118000,
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.post(
                "/saas/phonepe-webhook",
                data=payload,
                content_type="application/json",
                headers=headers,
            )

            self.assertEqual(response.status_code, 200)

            # check_transaction_status MUST be called with "MT_V2_INTERNAL_SETUP_555", NOT transactionId or eventId!
            mock_v2_provider.check_transaction_status.assert_called_once()
            called_order_id = mock_v2_provider.check_transaction_status.call_args[0][0]
            self.assertEqual(called_order_id, "MT_V2_INTERNAL_SETUP_555")
            self.assertNotEqual(called_order_id, "TXN_GATEWAY_SUBSTITUTE_TRAP")
            self.assertNotEqual(called_order_id, "EVT_SETUP_001")
            mock_activate.assert_called_once()

    def test_unmatched_callback_cannot_activate_unrelated_subscription(self):
        """Phase 5.4.1: Unmatched callback with unknown order ID cannot activate an unrelated subscription in the tenant."""
        # Log in as Org 20 user
        self.client.get(f"/test-login/{self.v2_user.id}")

        # Ensure subscription is PENDING
        self.v2_sub.status = SaaSSubscriptionStatus.PENDING
        db.session.commit()

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe"
        # Simulate payment gateway returning success for unknown order
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="UNKNOWN_UNMATCHED_ORDER_888",
            provider_subscription_id="PP_SUB_UNKNOWN",
            amount_paise=118000,
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            # Client reaches callback with an unrelated / unmatched order
            response = self.client.get(
                "/saas/phonepe-callback?merchantOrderId=UNKNOWN_UNMATCHED_ORDER_888"
            )

            # Response must redirect to checkout (not activate subscription)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/saas/checkout", response.location)

            # activate_subscription MUST NOT be called!
            mock_activate.assert_not_called()

            # Subscription status MUST remain PENDING
            sub = db.session.get(SaaSSubscription, self.v2_sub.id)
            self.assertEqual(sub.status, SaaSSubscriptionStatus.PENDING)

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
            # Since user is unauthenticated and record not found, org_id passed to provider must be None
            self.assertIsNone(call_kwargs.get("organization_id"))

    def test_callback_tenant_mismatch_redirects_to_checkout(self):
        """Cross-tenant callback manipulation (logged-in user accessing another tenant's payment) redirects to checkout."""
        # Log in user belonging to Org 10
        self.client.get(f"/test-login/{self.v3_user.id}")

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider:
            mock_get_provider.return_value = mock_v2_provider

            # Callback parameter references Org 20 mandate
            response = self.client.get(
                "/saas/phonepe-callback?merchantSubscriptionId=MSUB_V2_20_MANDATE"
            )

            # Verification: Tenant mismatch redirects to checkout
            self.assertEqual(response.status_code, 302)
            self.assertIn("/saas/checkout", response.location)
            # verify_payment must NOT be called
            mock_v2_provider.verify_payment.assert_not_called()

    def test_callback_unauthenticated_resolves_tenant_from_record(self):
        """Unauthenticated callback securely resolves tenant context from internal financial record."""
        # Make sure no user is logged in
        self.client.get("/test-login/999999")  # unauthenticated

        self.v2_sub.provider_order_id = "ORDER_SETUP_V2_2001"
        db.session.commit()

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_subscription_id="PP_SUB_V2_999",
            provider_transaction_id="TXN_UNAUTH_RESOLVE",
            amount_paise=118000,
        )
        mock_v2_provider.check_transaction_status.return_value = VerificationResult(
            success=True,
            status="COMPLETED",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            amount_paise=118000,
            provider_transaction_id="TXN_UNAUTH_RESOLVE",
            provider_payment_id="PAY_UNAUTH_RESOLVE",
            raw_response={"currency": "INR", "paymentDetails": [{"currency": "INR", "state": "COMPLETED"}]},
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.get(
                "/saas/phonepe-callback?merchantSubscriptionId=MSUB_V2_20_MANDATE"
            )

            self.assertEqual(response.status_code, 302)
            self.assertIn("/home", response.location)
            mock_v2_provider.verify_payment.assert_called_once()
            call_kwargs = mock_v2_provider.verify_payment.call_args[1]
            # Resolved org_id must be 20 from SaaSMandate
            self.assertEqual(call_kwargs.get("organization_id"), 20)
            mock_activate.assert_called_once()

    def test_unmatched_callback_cannot_activate_unrelated_subscription(self):
        """Phase 5.4.1: Unmatched callback with unknown order ID cannot activate an unrelated subscription in the tenant."""
        # Log in as Org 20 user
        self.client.get(f"/test-login/{self.v2_user.id}")

        # Ensure subscription is PENDING
        self.v2_sub.status = SaaSSubscriptionStatus.PENDING
        db.session.commit()

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe"
        # Simulate payment gateway returning success for unknown order
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="UNKNOWN_UNMATCHED_ORDER_888",
            provider_subscription_id="PP_SUB_UNKNOWN",
            amount_paise=118000,
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            # Client reaches callback with an unrelated / unmatched order
            response = self.client.get(
                "/saas/phonepe-callback?merchantOrderId=UNKNOWN_UNMATCHED_ORDER_888"
            )

            # Response must redirect to checkout (not activate subscription)
            self.assertEqual(response.status_code, 302)
            self.assertIn("/saas/checkout", response.location)

            # activate_subscription MUST NOT be called!
            mock_activate.assert_not_called()

            # Subscription status MUST remain PENDING
            sub = db.session.get(SaaSSubscription, self.v2_sub.id)
            self.assertEqual(sub.status, SaaSSubscriptionStatus.PENDING)

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
            sub = db.session.get(SaaSSubscription, 1001)
            self.assertEqual(sub.status, SaaSSubscriptionStatus.ACTIVE)
            self.assertEqual(sub.provider_subscription_id, "PP_V3_SUB_777")

    # ── Group F: Phase 5.4.2 Financial Integrity & Callback Enforcement ─────────

    def test_callback_amount_mismatch_rejected(self):
        """Phase 5.4.2 Requirement 2: Browser callback with amount mismatch against plan is rejected and subscription not activated."""
        self.v2_sub.status = SaaSSubscriptionStatus.PENDING
        self.v2_sub.provider_order_id = "ORDER_SETUP_MISMATCH_AMOUNT"
        db.session.commit()

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_subscription_id="PP_SUB_V2_999",
            amount_paise=100000,
        )
        # S2S returns 100000 paise (underpayment: expected 118000 paise including GST)
        mock_v2_provider.check_transaction_status.return_value = VerificationResult(
            success=True,
            status="COMPLETED",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            amount_paise=100000,
            provider_transaction_id="TXN_UNDERPAY",
            raw_response={"currency": "INR", "paymentDetails": [{"currency": "INR", "state": "COMPLETED"}]},
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.get(
                "/saas/phonepe-callback?merchantSubscriptionId=MSUB_V2_20_MANDATE"
            )

            self.assertEqual(response.status_code, 302)
            self.assertIn("/saas/checkout", response.location)
            mock_activate.assert_not_called()
            sub = db.session.get(SaaSSubscription, self.v2_sub.id)
            self.assertEqual(sub.status, SaaSSubscriptionStatus.PENDING)

    def test_callback_currency_mismatch_rejected(self):
        """Phase 5.4.2 Requirement 2: Browser callback with currency other than INR is rejected and subscription not activated."""
        self.v2_sub.status = SaaSSubscriptionStatus.PENDING
        self.v2_sub.provider_order_id = "ORDER_SETUP_MISMATCH_CURRENCY"
        db.session.commit()

        mock_v2_provider = MagicMock()
        mock_v2_provider.name = "phonepe_v2"
        mock_v2_provider.verify_payment.return_value = VerificationResult(
            success=True,
            status="SUCCESS",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            provider_subscription_id="PP_SUB_V2_999",
            amount_paise=118000,
        )
        # S2S returns USD currency
        mock_v2_provider.check_transaction_status.return_value = VerificationResult(
            success=True,
            status="COMPLETED",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            amount_paise=118000,
            provider_transaction_id="TXN_USD",
            raw_response={"currency": "USD", "paymentDetails": [{"currency": "USD", "state": "COMPLETED"}]},
        )

        with patch("services.billing.billing_service.BillingService.get_provider") as mock_get_provider, \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:
            mock_get_provider.return_value = mock_v2_provider

            response = self.client.get(
                "/saas/phonepe-callback?merchantSubscriptionId=MSUB_V2_20_MANDATE"
            )

            self.assertEqual(response.status_code, 302)
            self.assertIn("/saas/checkout", response.location)
            mock_activate.assert_not_called()
            sub = db.session.get(SaaSSubscription, self.v2_sub.id)
            self.assertEqual(sub.status, SaaSSubscriptionStatus.PENDING)

    def test_webhook_ambiguous_mandate_returns_422(self):
        """Phase 5.4.2 Requirement 5: Ambiguous mandate across multiple organizations in webhook is rejected with HTTP 422."""
        # Create a second mandate in Org 10 with the same merchant_subscription_id to simulate cross-tenant collision
        duplicate_mandate = SaaSMandate(
            id=202,
            organization_id=10,
            subscription_id=1001,
            payment_provider="phonepe_v2",
            merchant_subscription_id="MSUB_V2_20_MANDATE",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(duplicate_mandate)
        db.session.commit()

        correct_sha = hashlib.sha256(b"webhook_test_user:webhook_test_pass").hexdigest()
        headers = {
            "Authorization": f"Basic {correct_sha}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({
            "merchantSubscriptionId": "MSUB_V2_20_MANDATE",
            "event": "SUBSCRIPTION_ACTIVE",
            "eventId": "EVT_AMBIGUOUS_MANDATE_001",
        })

        response = self.client.post("/saas/phonepe-webhook", data=payload, headers=headers)
        self.assertEqual(response.status_code, 422)
        data = response.get_json()
        self.assertIn("Ambiguous mandate reference", data.get("error"))

    def test_webhook_ambiguous_pdn_returns_422(self):
        """Phase 5.4.2 Requirement 5: Ambiguous PDN across multiple organizations in webhook is rejected with HTTP 422."""
        # Create a second PDN in Org 10 with the same merchant_order_id to simulate cross-tenant collision
        duplicate_pdn = SaaSPreDebitNotification(
            id=302,
            organization_id=10,
            subscription_id=1001,
            mandate_id=201,
            merchant_order_id="ORDER_V2_PDN_301",
            amount_paise=118000,
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add(duplicate_pdn)
        db.session.commit()

        correct_sha = hashlib.sha256(b"webhook_test_user:webhook_test_pass").hexdigest()
        headers = {
            "Authorization": f"Basic {correct_sha}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({
            "merchantOrderId": "ORDER_V2_PDN_301",
            "event": "REDEMPTION_COMPLETED",
            "eventId": "EVT_AMBIGUOUS_PDN_001",
        })

        response = self.client.post("/saas/phonepe-webhook", data=payload, headers=headers)
        self.assertEqual(response.status_code, 422)
        data = response.get_json()
        self.assertIn("Ambiguous notification reference", data.get("error"))

    def test_webhook_unresolved_relational_mismatch_retryable(self):
        """Phase 5.4.2 Requirement 3: Relational mismatch records UNRESOLVED state, returns HTTP 422 with retryable=True, and allows subsequent retries."""
        # Create an attempt with wrong organization_id (99 instead of 20)
        mismatched_attempt = SaaSRedemptionAttempt(
            id=501,
            organization_id=99,
            subscription_id=self.v2_sub.id,
            notification_id=self.v2_pdn.id,
            merchant_order_id=self.v2_pdn.merchant_order_id,
            amount_paise=118000,
            status=RedemptionAttemptStatus.INITIATED.value,
        )
        db.session.add(mismatched_attempt)
        db.session.commit()

        correct_sha = hashlib.sha256(b"webhook_test_user:webhook_test_pass").hexdigest()
        headers = {
            "Authorization": f"Basic {correct_sha}",
            "Content-Type": "application/json",
        }
        payload = json.dumps({
            "merchantOrderId": self.v2_pdn.merchant_order_id,
            "event": "REDEMPTION_COMPLETED",
            "eventId": "EVT_RELATIONAL_MISMATCH_001",
        })

        # 1. First webhook attempt encounters relational mismatch
        response1 = self.client.post("/saas/phonepe-webhook", data=payload, headers=headers)
        self.assertEqual(response1.status_code, 422)
        data1 = response1.get_json()
        self.assertEqual(data1.get("status"), "UNRESOLVED")
        self.assertTrue(data1.get("retryable"))

        # Verify idempotency record exists with processing_state="UNRESOLVED" (NOT PROCESSED)
        event_record = SaaSWebhookEvent.query.filter_by(event_id="EVT_RELATIONAL_MISMATCH_001").first()
        self.assertIsNotNone(event_record)
        summary = json.loads(event_record.payload_summary)
        self.assertEqual(summary.get("processing_state"), "UNRESOLVED")

        # 2. Fix the relational mismatch
        mismatched_attempt.organization_id = 20
        db.session.commit()

        # 3. Second webhook retry for the SAME eventId must NOT be dropped by deduplication
        with patch("services.billing.providers.phonepe_autopay_v2_provider.PhonePeAutoPayV2Provider.reconcile_redemption_status") as mock_recon:
            def side_effect(attempt):
                attempt.status = RedemptionAttemptStatus.COMPLETED.value
                return attempt
            mock_recon.side_effect = side_effect

            response2 = self.client.post("/saas/phonepe-webhook", data=payload, headers=headers)
            self.assertEqual(response2.status_code, 200)
            data2 = response2.get_json()
            self.assertEqual(data2.get("status"), "success")

            # Verify idempotency record is now updated to PROCESSED
            db.session.refresh(event_record)
            summary2 = json.loads(event_record.payload_summary)
            self.assertEqual(summary2.get("processing_state"), "PROCESSED")

        # 4. Third webhook delivery for identical event is now cleanly recognized as already_processed
        response3 = self.client.post("/saas/phonepe-webhook", data=payload, headers=headers)
        self.assertEqual(response3.status_code, 200)
        self.assertEqual(response3.get_json().get("status"), "already_processed")


if __name__ == "__main__":
    unittest.main()

"""
test_phase7_remediation.py
===========================
Comprehensive unit and integration test suite for Phase 7 remediation of local audit findings.

Covers:
- Fix 1: Provider consistency on subscription creation, checkout, and initiate_subscription.
- Fix 2: Production fail-closed configuration validation guard.
- Fix 3: Dedicated renewal secret requirement (no fallback to SECRET_KEY).
- Fix 4 & Fix 5: Dead code removal and V3 webhook security restriction (reject V2 mutation, disabled flag).
"""

from __future__ import annotations

import os
import unittest
import json
from unittest.mock import patch, MagicMock
from flask import Flask
from flask_login import LoginManager, login_user
from sqlalchemy.pool import StaticPool

from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSTransaction,
    SaaSWebhookEvent,
    User,
    UserRole,
)
from routes.saas import saas_bp
from config import Config, validate_production_config, is_production_mode
from services.billing.subscription_service import SubscriptionService
from services.billing.billing_service import BillingService
from services.billing.payment_provider import WebhookResult


class TestPhase7Remediation(unittest.TestCase):
    """Integration and unit test suite for Phase 7 remediations."""

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
        cls.app.config["SECRET_KEY"] = "test-secret-key-phase7"
        cls.app.config["WTF_CSRF_ENABLED"] = False
        cls.app.config["BILLING_DEFAULT_PROVIDER"] = "phonepe_v2"
        cls.app.config["PHONEPE_ENV"] = "UAT"
        cls.app.config["PHONEPE_MERCHANT_ID"] = "PGTESTPAYUAT"
        cls.app.config["PHONEPE_CLIENT_ID"] = "test_client_id"
        cls.app.config["PHONEPE_CLIENT_SECRET"] = "test_client_secret"
        cls.app.config["PHONEPE_CLIENT_VERSION"] = 1
        cls.app.config["PHONEPE_SALT_KEY"] = "test_salt_key_routes"
        cls.app.config["PHONEPE_SALT_INDEX"] = "1"
        cls.app.config["PHONEPE_WEBHOOK_USERNAME"] = "webhook_test_user"
        cls.app.config["PHONEPE_WEBHOOK_PASSWORD"] = "webhook_test_pass"
        cls.app.config["RENEWAL_CRON_SECRET"] = "test-cron-secret-789"
        cls.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = True

        login_manager = LoginManager()
        login_manager.init_app(cls.app)

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        cls.app.register_blueprint(saas_bp)

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
        db.session.query(SaaSSubscription).delete()
        db.session.query(User).delete()
        db.session.query(Organization).delete()
        db.session.query(Plan).delete()
        db.session.commit()

        # Seed test plan
        self.plan = Plan(
            id=1,
            name="Professional",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
            default_member_limit=10,
            features_json=json.dumps(["crm", "quotes"]),
        )
        # Seed test org
        self.org = Organization(
            id=10,
            name="Test Org Ltd",
            unique_code="TESTORG",
            status=OrganizationStatus.PENDING,
        )
        # Seed test admin user
        self.admin_user = User(
            id=100,
            username="admin_user_7",
            email="admin@testorg.com",
            password="dummy",
            role=UserRole.ADMIN,
            organization_id=10,
        )
        db.session.add_all([self.plan, self.org, self.admin_user])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        self.app_context.pop()

    # ─────────────────────────────────────────────────────────────────────────
    # Fix 1: Provider Consistency Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_get_or_create_subscription_persists_configured_v2_provider(self):
        """When BILLING_DEFAULT_PROVIDER is phonepe_v2, new subscriptions get phonepe_v2."""
        self.app.config["BILLING_DEFAULT_PROVIDER"] = "phonepe_v2"
        BillingService.reset_provider_cache()

        sub = SubscriptionService.get_or_create_subscription(self.org, self.plan)

        self.assertIsNotNone(sub.id)
        self.assertEqual(sub.payment_provider, "phonepe_v2")
        self.assertEqual(sub.status, SaaSSubscriptionStatus.PENDING)

        # Verify persisted in database
        queried = SaaSSubscription.query.filter_by(organization_id=self.org.id).first()
        self.assertIsNotNone(queried)
        self.assertEqual(queried.payment_provider, "phonepe_v2")

    def test_get_or_create_subscription_preserves_legacy_v3_provider(self):
        """An existing legacy V3 subscription preserves its payment_provider='phonepe'."""
        self.app.config["BILLING_DEFAULT_PROVIDER"] = "phonepe_v2"
        BillingService.reset_provider_cache()

        # Create pre-existing legacy V3 subscription
        legacy_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe",
        )
        db.session.add(legacy_sub)
        db.session.commit()

        # Calling get_or_create_subscription should return legacy_sub without altering provider
        sub = SubscriptionService.get_or_create_subscription(self.org, self.plan)

        self.assertEqual(sub.id, legacy_sub.id)
        self.assertEqual(sub.payment_provider, "phonepe")

        # Confirm persisted in DB
        queried = SaaSSubscription.query.filter_by(organization_id=self.org.id).first()
        self.assertEqual(queried.payment_provider, "phonepe")

    def test_initiate_subscription_sets_and_persists_provider(self):
        """initiate_subscription delegates mandate setup to the resolved provider and persists provider name."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe_v2",
        )
        db.session.add(sub)
        db.session.commit()

        mock_provider = MagicMock()
        mock_provider.name = "phonepe_v2"
        mock_setup = MagicMock()
        mock_setup.success = True
        mock_setup.provider_subscription_id = "OMS_TEST_777"
        mock_setup.merchant_subscription_id = "MSUB_TEST_777"
        mock_setup.merchant_transaction_id = "MTX_TEST_777"
        mock_provider.create_subscription_mandate.return_value = mock_setup

        res = SubscriptionService.initiate_subscription(
            org=self.org,
            plan=self.plan,
            billing_cycle="monthly",
            user=self.admin_user,
            provider=mock_provider,
            return_url="http://test/return",
            callback_url="http://test/callback",
        )

        self.assertTrue(res.success)
        self.assertEqual(sub.payment_provider, "phonepe_v2")
        self.assertEqual(sub.provider_subscription_id, "OMS_TEST_777")

    def test_checkout_and_create_subscription_use_persisted_provider(self):
        """Checkout and create-subscription API resolve provider from the subscription record."""
        # Create subscription with phonepe_v2
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe_v2",
        )
        db.session.add(sub)
        db.session.commit()

        self.client.get(f"/test-login/{self.admin_user.id}")

        mock_provider = MagicMock()
        mock_provider.name = "phonepe_v2"
        mock_setup = MagicMock()
        mock_setup.success = True
        mock_setup.redirect_url = "https://phonepe.com/checkout/test"
        mock_setup.provider_subscription_id = "OMS_CHECKOUT_123"
        mock_setup.merchant_subscription_id = "MSUB_CHECKOUT_123"
        mock_setup.merchant_transaction_id = "MTX_CHECKOUT_123"
        mock_provider.create_subscription_mandate.return_value = mock_setup

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_provider) as mock_get_prov:
            resp = self.client.post("/saas/api/create-subscription", json={"cycle": "monthly"})
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("provider"), "phonepe_v2")
            # Assert BillingService.get_provider was called with "phonepe_v2"
            mock_get_prov.assert_called_with("phonepe_v2")

    def test_initiate_subscription_rejects_provider_mismatch_against_existing_subscription(self):
        """initiate_subscription rejects when caller explicitly passes a provider differing from persisted provider."""
        # Create legacy subscription with payment_provider='phonepe'
        legacy_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe",
        )
        db.session.add(legacy_sub)
        db.session.commit()

        # Caller explicitly attempts to use phonepe_v2
        mock_v2_prov = MagicMock()
        mock_v2_prov.name = "phonepe_v2"

        res = SubscriptionService.initiate_subscription(
            org=self.org,
            plan=self.plan,
            billing_cycle="monthly",
            user=self.admin_user,
            provider=mock_v2_prov,
        )

        self.assertFalse(res.success)
        self.assertIn("Provider mismatch", res.error_message)
        self.assertIn("bound to provider 'phonepe'", res.error_message)

        # Database record must NOT be mutated
        refreshed = db.session.get(SaaSSubscription, legacy_sub.id)
        self.assertEqual(refreshed.payment_provider, "phonepe")
        mock_v2_prov.create_subscription_mandate.assert_not_called()

    def test_initiate_subscription_accepts_matching_explicit_provider(self):
        """initiate_subscription succeeds when caller explicitly passes provider matching persisted provider."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe_v2",
        )
        db.session.add(sub)
        db.session.commit()

        mock_v2_prov = MagicMock()
        mock_v2_prov.name = "phonepe_v2"
        mock_setup = MagicMock()
        mock_setup.success = True
        mock_setup.provider_subscription_id = "OMS_MATCHING_123"
        mock_setup.merchant_subscription_id = "MSUB_MATCHING_123"
        mock_setup.merchant_transaction_id = "MTX_MATCHING_123"
        mock_v2_prov.create_subscription_mandate.return_value = mock_setup

        res = SubscriptionService.initiate_subscription(
            org=self.org,
            plan=self.plan,
            billing_cycle="monthly",
            user=self.admin_user,
            provider=mock_v2_prov,
        )

        self.assertTrue(res.success)
        refreshed = db.session.get(SaaSSubscription, sub.id)
        self.assertEqual(refreshed.payment_provider, "phonepe_v2")

    def test_initiate_subscription_resolves_omitted_provider_from_persisted_subscription(self):
        """When provider is omitted, initiate_subscription resolves and delegates to the persisted provider."""
        legacy_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe",
        )
        db.session.add(legacy_sub)
        db.session.commit()

        mock_v3_prov = MagicMock()
        mock_v3_prov.name = "phonepe"
        mock_setup = MagicMock()
        mock_setup.success = True
        mock_setup.provider_subscription_id = "OMS_V3_123"
        mock_setup.merchant_subscription_id = "MSUB_V3_123"
        mock_setup.merchant_transaction_id = "MTX_V3_123"
        mock_v3_prov.create_subscription_mandate.return_value = mock_setup

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_v3_prov) as mock_get_prov:
            res = SubscriptionService.initiate_subscription(
                org=self.org,
                plan=self.plan,
                billing_cycle="monthly",
                user=self.admin_user,
                provider=None,
            )

            self.assertTrue(res.success)
            mock_get_prov.assert_called_with("phonepe")
            refreshed = db.session.get(SaaSSubscription, legacy_sub.id)
            self.assertEqual(refreshed.payment_provider, "phonepe")

    def test_get_or_create_subscription_raises_value_error_on_mismatched_provider(self):
        """get_or_create_subscription raises ValueError if existing subscription has differing provider."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe",
        )
        db.session.add(sub)
        db.session.commit()

        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.get_or_create_subscription(self.org, self.plan, provider="phonepe_v2")
        self.assertIn("Provider mismatch", str(ctx.exception))
        self.assertIn("bound to provider 'phonepe'", str(ctx.exception))

    def test_initiate_subscription_existing_empty_provider_with_explicit_v3_provider(self):
        """Existing subscription with empty payment_provider uses explicit V3 provider and persists it."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="",
        )
        db.session.add(sub)
        db.session.commit()

        mock_v3_prov = MagicMock()
        mock_v3_prov.name = "phonepe"
        mock_setup = MagicMock()
        mock_setup.success = True
        mock_setup.provider_subscription_id = "OMS_EMPTY_V3_123"
        mock_setup.merchant_subscription_id = "MSUB_EMPTY_V3_123"
        mock_setup.merchant_transaction_id = "MTX_EMPTY_V3_123"
        mock_v3_prov.create_subscription_mandate.return_value = mock_setup

        res = SubscriptionService.initiate_subscription(
            org=self.org,
            plan=self.plan,
            billing_cycle="monthly",
            user=self.admin_user,
            provider=mock_v3_prov,
        )

        self.assertTrue(res.success)
        mock_v3_prov.create_subscription_mandate.assert_called_once()
        refreshed = db.session.get(SaaSSubscription, sub.id)
        self.assertEqual(refreshed.payment_provider, "phonepe")

    def test_initiate_subscription_existing_empty_provider_with_omitted_provider(self):
        """Existing subscription with empty payment_provider resolves configured default provider and persists it."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="",
        )
        db.session.add(sub)
        db.session.commit()

        mock_v2_prov = MagicMock()
        mock_v2_prov.name = "phonepe_v2"
        mock_setup = MagicMock()
        mock_setup.success = True
        mock_setup.provider_subscription_id = "OMS_EMPTY_DEFAULT_123"
        mock_setup.merchant_subscription_id = "MSUB_EMPTY_DEFAULT_123"
        mock_setup.merchant_transaction_id = "MTX_EMPTY_DEFAULT_123"
        mock_v2_prov.create_subscription_mandate.return_value = mock_setup

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_v2_prov) as mock_get_prov:
            res = SubscriptionService.initiate_subscription(
                org=self.org,
                plan=self.plan,
                billing_cycle="monthly",
                user=self.admin_user,
                provider=None,
            )

            self.assertTrue(res.success)
            mock_get_prov.assert_called_with("phonepe_v2")
            mock_v2_prov.create_subscription_mandate.assert_called_once()
            refreshed = db.session.get(SaaSSubscription, sub.id)
            self.assertEqual(refreshed.payment_provider, "phonepe_v2")

    def test_initiate_subscription_existing_populated_legacy_rejects_conflicting_v2(self):
        """Existing subscription with populated 'phonepe' provider strictly rejects conflicting 'phonepe_v2'."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe",
        )
        db.session.add(sub)
        db.session.commit()

        mock_v2_prov = MagicMock()
        mock_v2_prov.name = "phonepe_v2"

        res = SubscriptionService.initiate_subscription(
            org=self.org,
            plan=self.plan,
            billing_cycle="monthly",
            user=self.admin_user,
            provider=mock_v2_prov,
        )

        self.assertFalse(res.success)
        self.assertIn("Provider mismatch", res.error_message)
        self.assertIn("bound to provider 'phonepe'", res.error_message)
        mock_v2_prov.create_subscription_mandate.assert_not_called()
        refreshed = db.session.get(SaaSSubscription, sub.id)
        self.assertEqual(refreshed.payment_provider, "phonepe")

    def test_no_external_provider_request_after_mismatch(self):
        """Verify external provider mandate request is never made when provider mismatch occurs."""
        sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            status=SaaSSubscriptionStatus.PENDING,
            payment_provider="phonepe_v2",
        )
        db.session.add(sub)
        db.session.commit()

        mock_v3_prov = MagicMock()
        mock_v3_prov.name = "phonepe"

        res = SubscriptionService.initiate_subscription(
            org=self.org,
            plan=self.plan,
            billing_cycle="monthly",
            user=self.admin_user,
            provider=mock_v3_prov,
        )

        self.assertFalse(res.success)
        self.assertIn("Provider mismatch", res.error_message)
        mock_v3_prov.create_subscription_mandate.assert_not_called()
        refreshed = db.session.get(SaaSSubscription, sub.id)
        self.assertEqual(refreshed.payment_provider, "phonepe_v2")

    # ─────────────────────────────────────────────────────────────────────────
    # Fix 2: Production Fail-Closed Configuration Tests
    # ─────────────────────────────────────────────────────────────────────────

    def _get_valid_prod_config(self):
        return {
            "FLASK_ENV": "production",
            "BILLING_DEFAULT_PROVIDER": "phonepe_v2",
            "PHONEPE_ENV": "PRODUCTION",
            "PHONEPE_CLIENT_ID": "prod_client_id_12345",
            "PHONEPE_CLIENT_SECRET": "prod_client_secret_67890",
            "PHONEPE_CLIENT_VERSION": "1",
            "PHONEPE_MERCHANT_ID": "",
            "PHONEPE_WEBHOOK_USERNAME": "prod_webhook_user",
            "PHONEPE_WEBHOOK_PASSWORD": "prod_webhook_pass",
            "RENEWAL_CRON_SECRET": "prod_cron_secret_dedicated_abc123",
            "SECRET_KEY": "different_flask_secret_key_xyz",
            "ENABLE_PHONEPE_V3_WEBHOOK": False,
            "ALLOW_LEGACY_PHONEPE_V3": False,
            "USE_AWS_SECRETS": False,
        }

    def test_validate_production_config_passes_valid_config(self):
        """Valid production configuration passes without exception."""
        cfg = self._get_valid_prod_config()
        validate_production_config(cfg, force=True)

    def test_validate_production_config_rejects_non_v2_provider(self):
        """Production requires BILLING_DEFAULT_PROVIDER=phonepe_v2."""
        cfg = self._get_valid_prod_config()
        cfg["BILLING_DEFAULT_PROVIDER"] = "phonepe"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("BILLING_DEFAULT_PROVIDER must be 'phonepe_v2'", str(ctx.exception))

    def test_validate_production_config_rejects_uat_env(self):
        """Production requires PHONEPE_ENV=PRODUCTION."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_ENV"] = "UAT"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_ENV must be 'PRODUCTION'", str(ctx.exception))

    def test_validate_production_config_rejects_missing_client_credentials(self):
        """Production requires non-empty client ID and secret, without disclosing secrets."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_CLIENT_ID"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_CLIENT_ID is required", str(ctx.exception))

        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_CLIENT_SECRET"] = "   "
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_CLIENT_SECRET is required", str(ctx.exception))
        self.assertNotIn("prod_client_secret_67890", str(ctx.exception))

    def test_validate_production_config_rejects_test_merchant_pgtestpayuat(self):
        """Production rejects UAT test merchant PGTESTPAYUAT or UAT when merchant ID is provided."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_MERCHANT_ID"] = "PGTESTPAYUAT"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("cannot be test merchant", str(ctx.exception))

        cfg["PHONEPE_MERCHANT_ID"] = "uat"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("cannot be test merchant", str(ctx.exception))

    def test_validate_production_config_accepts_v2_without_merchant_id(self):
        """Production accepts PhonePe AutoPay V2 when PHONEPE_MERCHANT_ID is empty or omitted."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_MERCHANT_ID"] = ""
        validate_production_config(cfg, force=True)

        # Omitted key entirely
        del cfg["PHONEPE_MERCHANT_ID"]
        validate_production_config(cfg, force=True)

    def test_validate_production_config_accepts_v2_with_optional_merchant_id(self):
        """Production accepts valid non-test PHONEPE_MERCHANT_ID when provided (partner mode)."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_MERCHANT_ID"] = "PROD_PARTNER_MERCHANT_123"
        validate_production_config(cfg, force=True)

    def test_validate_production_config_rejects_missing_webhook_creds(self):
        """Production requires non-empty webhook username and password."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_WEBHOOK_USERNAME"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_WEBHOOK_USERNAME is required", str(ctx.exception))

        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_WEBHOOK_PASSWORD"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_WEBHOOK_PASSWORD is required", str(ctx.exception))

    def test_validate_production_config_rejects_missing_renewal_cron_secret(self):
        """Production requires dedicated non-empty RENEWAL_CRON_SECRET."""
        cfg = self._get_valid_prod_config()
        cfg["RENEWAL_CRON_SECRET"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("Dedicated RENEWAL_CRON_SECRET is required", str(ctx.exception))

    def test_validate_production_config_rejects_renewal_secret_matching_secret_key(self):
        """Production rejects RENEWAL_CRON_SECRET reusing SECRET_KEY."""
        cfg = self._get_valid_prod_config()
        cfg["RENEWAL_CRON_SECRET"] = cfg["SECRET_KEY"]
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("must not reuse SECRET_KEY", str(ctx.exception))

    def test_validate_production_config_noop_when_not_production(self):
        """When FLASK_ENV is development or testing, validate_production_config does not raise."""
        dev_cfg = {
            "FLASK_ENV": "development",
            "BILLING_DEFAULT_PROVIDER": "phonepe",
            "PHONEPE_ENV": "UAT",
            "PHONEPE_MERCHANT_ID": "PGTESTPAYUAT",
        }
        validate_production_config(dev_cfg, force=False)

    def test_is_production_mode_returns_true_for_flask_env_production(self):
        """is_production_mode returns True when FLASK_ENV is 'production'."""
        cfg = {"FLASK_ENV": "production"}
        self.assertTrue(is_production_mode(cfg))

    def test_is_production_mode_returns_false_for_development(self):
        """is_production_mode returns False when FLASK_ENV is development or testing."""
        self.assertFalse(is_production_mode({"FLASK_ENV": "development"}))
        self.assertFalse(is_production_mode({"FLASK_ENV": "testing"}))
        self.assertFalse(is_production_mode({"FLASK_ENV": ""}))

    def test_production_mode_rejects_env_production_without_flask_env(self):
        """ENV=production without FLASK_ENV=production raises RuntimeError (must not bypass)."""
        cfg = {"ENV": "production", "FLASK_ENV": ""}
        with self.assertRaises(RuntimeError) as ctx:
            is_production_mode(cfg)
        self.assertIn("FLASK_ENV must be explicitly set to 'production'", str(ctx.exception))

    def test_validate_production_config_rejects_env_production_without_flask_env(self):
        """validate_production_config rejects ENV=production without FLASK_ENV=production."""
        cfg = {"ENV": "production", "FLASK_ENV": ""}
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=False)
        self.assertIn("FLASK_ENV must be explicitly set to 'production'", str(ctx.exception))

    def test_validate_production_config_rejects_missing_flask_env(self):
        """validate_production_config with force=True rejects missing/empty FLASK_ENV."""
        cfg = self._get_valid_prod_config()
        cfg["FLASK_ENV"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("FLASK_ENV must be explicitly set to 'production'", str(ctx.exception))

    def test_validate_production_config_rejects_missing_client_version(self):
        """Production requires non-empty PHONEPE_CLIENT_VERSION."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_CLIENT_VERSION"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_CLIENT_VERSION is required", str(ctx.exception))

    def test_validate_production_config_rejects_invalid_client_version(self):
        """Production requires positive integer for PHONEPE_CLIENT_VERSION."""
        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_CLIENT_VERSION"] = "0"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("positive integer", str(ctx.exception))

        cfg = self._get_valid_prod_config()
        cfg["PHONEPE_CLIENT_VERSION"] = "version_one"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("positive integer", str(ctx.exception))

    def test_validate_production_config_rejects_v3_webhook_true_without_authorization(self):
        """Production rejects ENABLE_PHONEPE_V3_WEBHOOK=true unless ALLOW_LEGACY_PHONEPE_V3=true."""
        cfg = self._get_valid_prod_config()
        cfg["ENABLE_PHONEPE_V3_WEBHOOK"] = True
        cfg["ALLOW_LEGACY_PHONEPE_V3"] = False
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("ALLOW_LEGACY_PHONEPE_V3=true", str(ctx.exception))

    def test_validate_production_config_accepts_v3_webhook_true_with_authorization(self):
        """Production accepts ENABLE_PHONEPE_V3_WEBHOOK=true when ALLOW_LEGACY_PHONEPE_V3=true and legacy creds present."""
        cfg = self._get_valid_prod_config()
        cfg["ENABLE_PHONEPE_V3_WEBHOOK"] = True
        cfg["ALLOW_LEGACY_PHONEPE_V3"] = True
        cfg["PHONEPE_MERCHANT_ID"] = "PROD_LEGACY_MID"
        cfg["PHONEPE_SALT_KEY"] = "prod_salt_key_12345"
        validate_production_config(cfg, force=True)

    def test_validate_production_config_requires_merchant_id_when_legacy_v3_authorized(self):
        """Production requires non-empty PHONEPE_MERCHANT_ID when legacy V3 webhook is authorized."""
        cfg = self._get_valid_prod_config()
        cfg["ENABLE_PHONEPE_V3_WEBHOOK"] = True
        cfg["ALLOW_LEGACY_PHONEPE_V3"] = True
        cfg["PHONEPE_MERCHANT_ID"] = ""
        cfg["PHONEPE_SALT_KEY"] = "prod_salt_key_12345"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("PHONEPE_MERCHANT_ID is required in production when legacy PhonePe V3 is enabled", str(ctx.exception))

    def test_validate_production_config_requires_valid_salt_key_when_legacy_v3_authorized(self):
        """Production requires non-test PHONEPE_SALT_KEY when legacy V3 webhook is authorized."""
        cfg = self._get_valid_prod_config()
        cfg["ENABLE_PHONEPE_V3_WEBHOOK"] = True
        cfg["ALLOW_LEGACY_PHONEPE_V3"] = True
        cfg["PHONEPE_MERCHANT_ID"] = "PROD_LEGACY_MID"
        cfg["PHONEPE_SALT_KEY"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("Valid production PHONEPE_SALT_KEY is required", str(ctx.exception))

        cfg["PHONEPE_SALT_KEY"] = "099eb0cd-0252-4e2a-9123-046648858427"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("Valid production PHONEPE_SALT_KEY is required", str(ctx.exception))

    def test_validate_production_config_rejects_aws_secrets_without_region_or_secret_name(self):
        """Production rejects USE_AWS_SECRETS=true if AWS_REGION or AWS_SECRET_NAME is missing."""
        cfg = self._get_valid_prod_config()
        cfg["USE_AWS_SECRETS"] = True
        cfg["AWS_REGION"] = ""
        cfg["AWS_SECRET_NAME"] = "my_secret"
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("AWS_REGION is required", str(ctx.exception))

        cfg = self._get_valid_prod_config()
        cfg["USE_AWS_SECRETS"] = True
        cfg["AWS_REGION"] = "ap-south-1"
        cfg["AWS_SECRET_NAME"] = ""
        with self.assertRaises(RuntimeError) as ctx:
            validate_production_config(cfg, force=True)
        self.assertIn("AWS_SECRET_NAME is required", str(ctx.exception))

    # ─────────────────────────────────────────────────────────────────────────
    # Fix 3: Dedicated Renewal Secret Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_process_renewals_returns_500_when_cron_secret_missing(self):
        """When RENEWAL_CRON_SECRET is empty/unset, reject cron execution with generic 500 error."""
        self.app.config["RENEWAL_CRON_SECRET"] = ""
        resp = self.client.post(
            "/saas/api/process-renewals",
            headers={"X-Renewal-Secret": "any_incoming_token"}
        )
        self.assertEqual(resp.status_code, 500)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "Renewal service configuration error")

    def test_process_renewals_rejects_secret_key_as_renewal_secret(self):
        """Passing SECRET_KEY when dedicated secret is set must return HTTP 403 Forbidden."""
        self.app.config["SECRET_KEY"] = "my_flask_secret_key_xyz"
        self.app.config["RENEWAL_CRON_SECRET"] = "my_dedicated_cron_secret_abc"

        resp = self.client.post(
            "/saas/api/process-renewals",
            headers={"X-Renewal-Secret": "my_flask_secret_key_xyz"}
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertEqual(data.get("error"), "Unauthorized renewal trigger")

    def test_process_renewals_accepts_valid_dedicated_cron_secret(self):
        """Passing dedicated RENEWAL_CRON_SECRET via X-Renewal-Secret succeeds."""
        self.app.config["RENEWAL_CRON_SECRET"] = "my_dedicated_cron_secret_abc"

        with patch("services.billing.renewal_service.RenewalService.process_due_renewals", return_value=[]), \
             patch("services.billing.renewal_service.RenewalService.process_upcoming_notifications", return_value=[]):
            resp = self.client.post(
                "/saas/api/process-renewals",
                headers={"X-Renewal-Secret": "my_dedicated_cron_secret_abc"}
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "completed")

    def test_process_renewals_allows_super_admin_session(self):
        """An authenticated Super Admin user can access renewals even without cron header."""
        self.app.config["RENEWAL_CRON_SECRET"] = ""
        self.client.get(f"/test-login/{self.admin_user.id}")

        with patch("services.billing.renewal_service.RenewalService.process_due_renewals", return_value=[]), \
             patch("services.billing.renewal_service.RenewalService.process_upcoming_notifications", return_value=[]):
            resp = self.client.post("/saas/api/process-renewals")
            self.assertEqual(resp.status_code, 200)

    # ─────────────────────────────────────────────────────────────────────────
    # Fix 4 & Fix 5: V3 Webhook Security Tests
    # ─────────────────────────────────────────────────────────────────────────

    def test_v3_webhook_disabled_when_flag_false(self):
        """When ENABLE_PHONEPE_V3_WEBHOOK is False, V3 webhook returns HTTP 403."""
        self.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = False

        resp = self.client.post(
            "/saas/phonepe-webhook",
            data=b'{"test": "payload"}',
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIn("Legacy V3 webhook is disabled", data.get("error"))

    def test_v3_webhook_rejects_mutation_of_v2_subscription(self):
        """V3 webhook rejects attempts to process or mutate a PhonePe V2 subscription."""
        self.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = True

        # Insert a PhonePe V2 subscription into the in-memory database
        v2_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe_v2",
            provider_subscription_id="OMS_V2_TARGET_123",
            status=SaaSSubscriptionStatus.PENDING,
        )
        db.session.add(v2_sub)
        db.session.commit()

        mock_webhook_res = WebhookResult(
            is_valid=True,
            event_id="EVT_TEST_V3_ATTACK",
            event_type="SUBSCRIPTION_ACTIVE",
            merchant_subscription_id=None,
            provider_subscription_id="OMS_V2_TARGET_123",
            amount_paise=1000,
            state="COMPLETED",
        )

        mock_provider = MagicMock()
        mock_provider.verify_webhook.return_value = mock_webhook_res

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_provider), \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:

            resp = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"response": "dummy"}',
                headers={"Content-Type": "application/json"}
            )

            # Must reject with HTTP 400
            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertIn("phonepe_v2", data.get("error"))
            # Activation must NEVER be called
            mock_activate.assert_not_called()
            # Subscription status must remain PENDING
            refreshed = db.session.get(SaaSSubscription, v2_sub.id)
            self.assertEqual(refreshed.status, SaaSSubscriptionStatus.PENDING)

    def test_v3_webhook_allows_legacy_v3_subscription(self):
        """V3 webhook allows processing a valid legacy PhonePe V3 subscription."""
        self.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = True

        v3_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe",
            provider_subscription_id="OMS_V3_LEGACY_123",
            status=SaaSSubscriptionStatus.PENDING,
        )
        db.session.add(v3_sub)
        db.session.commit()

        mock_webhook_res = WebhookResult(
            is_valid=True,
            event_id="EVT_TEST_V3_LEGIT",
            event_type="SUBSCRIPTION_ACTIVE",
            merchant_subscription_id=None,
            provider_subscription_id="OMS_V3_LEGACY_123",
            amount_paise=118000,
            state="COMPLETED",
        )

        mock_provider = MagicMock()
        mock_provider.verify_webhook.return_value = mock_webhook_res

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_provider), \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:

            resp = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"response": "dummy"}',
                headers={"Content-Type": "application/json"}
            )

            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "success")
            # Activation must be called for valid V3 sub
            mock_activate.assert_called_once()

    def test_v3_webhook_allows_legacy_compatibility_alias(self):
        """V3 webhook safely activates subscription when receiving legacy V3 runtime compatibility alias."""
        self.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = True

        v3_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe",
            provider_subscription_id="OMS_V3_ALIAS_123",
            status=SaaSSubscriptionStatus.PENDING,
        )
        db.session.add(v3_sub)
        db.session.commit()

        mock_webhook_res = WebhookResult(
            is_valid=True,
            event_id="EVT_TEST_V3_ALIAS",
            event_type="subscription.setup.order.completed",
            merchant_subscription_id=None,
            provider_subscription_id="OMS_V3_ALIAS_123",
            amount_paise=118000,
            state="COMPLETED",
        )

        mock_provider = MagicMock()
        mock_provider.verify_webhook.return_value = mock_webhook_res

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_provider), \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:

            resp = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"response": "dummy"}',
                headers={"Content-Type": "application/json"}
            )

            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertEqual(data.get("status"), "success")
            mock_activate.assert_called_once()

    def test_v3_webhook_rejects_v2_checkout_order_completed_even_if_state_completed(self):
        """V3 webhook rejects checkout.order.completed even if state is COMPLETED and subscription is V3."""
        self.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = True

        v3_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe",
            provider_subscription_id="OMS_V3_CHECKOUT_123",
            status=SaaSSubscriptionStatus.PENDING,
        )
        db.session.add(v3_sub)
        db.session.commit()

        mock_webhook_res = WebhookResult(
            is_valid=True,
            event_id="EVT_V2_CHECKOUT_IN_V3",
            event_type="checkout.order.completed",
            merchant_subscription_id=None,
            provider_subscription_id="OMS_V3_CHECKOUT_123",
            amount_paise=118000,
            state="COMPLETED",
        )

        mock_provider = MagicMock()
        mock_provider.verify_webhook.return_value = mock_webhook_res

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_provider), \
             patch("services.billing.subscription_service.SubscriptionService.activate_subscription") as mock_activate:

            resp = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"event": "checkout.order.completed"}',
                headers={"Content-Type": "application/json"}
            )

            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertIn("belongs to PhonePe AutoPay V2", data.get("error"))
            mock_activate.assert_not_called()
            # Subscription must not be activated
            refreshed = db.session.get(SaaSSubscription, v3_sub.id)
            self.assertEqual(refreshed.status, SaaSSubscriptionStatus.PENDING)

    def test_v3_webhook_rejects_v2_checkout_order_failed_even_if_state_failed(self):
        """V3 webhook rejects checkout.order.failed even if state is FAILED and subscription is V3."""
        self.app.config["ENABLE_PHONEPE_V3_WEBHOOK"] = True

        v3_sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe",
            provider_subscription_id="OMS_V3_FAILED_123",
            status=SaaSSubscriptionStatus.ACTIVE,
        )
        db.session.add(v3_sub)
        db.session.commit()

        mock_webhook_res = WebhookResult(
            is_valid=True,
            event_id="EVT_V2_FAILED_IN_V3",
            event_type="checkout.order.failed",
            merchant_subscription_id=None,
            provider_subscription_id="OMS_V3_FAILED_123",
            amount_paise=118000,
            state="FAILED",
        )

        mock_provider = MagicMock()
        mock_provider.verify_webhook.return_value = mock_webhook_res

        with patch("services.billing.billing_service.BillingService.get_provider", return_value=mock_provider), \
             patch("services.billing.subscription_service.SubscriptionService.cancel_subscription") as mock_cancel:

            resp = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"event": "checkout.order.failed"}',
                headers={"Content-Type": "application/json"}
            )

            self.assertEqual(resp.status_code, 400)
            data = resp.get_json()
            self.assertIn("belongs to PhonePe AutoPay V2", data.get("error"))
            mock_cancel.assert_not_called()
            # Subscription must remain ACTIVE
            refreshed = db.session.get(SaaSSubscription, v3_sub.id)
            self.assertEqual(refreshed.status, SaaSSubscriptionStatus.ACTIVE)

    def test_phonepe_webhook_router_case_insensitive_basic(self):
        """Router routes uppercase 'Basic', lowercase 'basic', and mixed-case 'BaSiC' to V2."""
        with patch("routes.saas._handle_v2_webhook") as mock_v2, \
             patch("routes.saas._handle_v3_webhook") as mock_v3:
            mock_v2.return_value = ("v2_response", 200)

            for auth_val in ["Basic valid_token", "basic valid_token", "BaSiC valid_token", "BASIC   valid_token"]:
                resp = self.client.post(
                    "/saas/phonepe-webhook",
                    data=b'{"event": "test"}',
                    headers={"Authorization": auth_val, "Content-Type": "application/json"}
                )
                self.assertEqual(resp.status_code, 200)

            self.assertEqual(mock_v2.call_count, 4)
            mock_v3.assert_not_called()

    def test_phonepe_webhook_router_malformed_basic_does_not_fall_through_to_v3(self):
        """Malformed Basic auth headers route to V2 and return 401; never fall through to V3."""
        with patch("routes.saas._handle_v3_webhook") as mock_v3:
            for auth_val in ["Basic", "basic  ", "BASIC", "basicfoo"]:
                resp = self.client.post(
                    "/saas/phonepe-webhook",
                    data=b'{"event": "test"}',
                    headers={"Authorization": auth_val, "Content-Type": "application/json"}
                )
                self.assertEqual(resp.status_code, 401, f"Failed for header: {auth_val}")
                data = resp.get_json()
                self.assertTrue("error" in data)

            mock_v3.assert_not_called()

    def test_phonepe_webhook_router_non_basic_and_x_verify_route_to_v3(self):
        """Non-Basic headers and X-VERIFY route to legacy V3 handler."""
        with patch("routes.saas._handle_v2_webhook") as mock_v2, \
             patch("routes.saas._handle_v3_webhook") as mock_v3:
            mock_v3.return_value = ("v3_response", 200)

            resp1 = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"event": "test"}',
                headers={"X-VERIFY": "checksum###1", "Content-Type": "application/json"}
            )
            self.assertEqual(resp1.status_code, 200)

            resp2 = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"event": "test"}',
                headers={"Authorization": "Bearer some_jwt", "Content-Type": "application/json"}
            )
            self.assertEqual(resp2.status_code, 200)

            resp3 = self.client.post(
                "/saas/phonepe-webhook",
                data=b'{"event": "test"}',
                headers={"Content-Type": "application/json"}
            )
            self.assertEqual(resp3.status_code, 200)

            self.assertEqual(mock_v3.call_count, 3)
            mock_v2.assert_not_called()

    # ─────────────────────────────────────────────────────────────────────────
    # PhonePe AutoPay V2 Merchant ID Decoupling & UAT Readiness
    # ─────────────────────────────────────────────────────────────────────────

    def test_v2_provider_merchant_id_property_returns_none_when_empty_or_unset(self):
        """PhonePeAutoPayV2Provider.merchant_id returns None when unset or empty string."""
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        with patch.dict(self.app.config, {"PHONEPE_MERCHANT_ID": ""}):
            prov = PhonePeAutoPayV2Provider()
            with self.app.app_context():
                self.assertIsNone(prov.merchant_id)

        with patch.dict(self.app.config, {"PHONEPE_MERCHANT_ID": "MY_PARTNER_MID"}):
            prov = PhonePeAutoPayV2Provider()
            with self.app.app_context():
                self.assertEqual(prov.merchant_id, "MY_PARTNER_MID")

    def test_v2_client_omits_x_merchant_id_header_when_merchant_id_is_none(self):
        """PhonePeAutoPayClient omits X-MERCHANT-ID header for direct merchants (merchant_id=None)."""
        from services.billing.providers.phonepe_autopay_client import PhonePeAutoPayClient
        mock_oauth = MagicMock()
        mock_oauth.auth_headers.return_value = {"Authorization": "O-Bearer test_token"}
        mock_oauth.api_base = "https://api-preprod.phonepe.com/apis/pg-sandbox"
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.content = b'{"state": "ACTIVE"}'
        mock_resp.json.return_value = {"state": "ACTIVE"}
        mock_session.request.return_value = mock_resp

        client = PhonePeAutoPayClient(oauth_client=mock_oauth, merchant_id=None, http_session=mock_session)
        client.get_subscription_status("SUB_123")
        headers = mock_session.request.call_args[1]["headers"]
        self.assertNotIn("X-MERCHANT-ID", headers)
        self.assertEqual(headers["Authorization"], "O-Bearer test_token")

    def test_v2_client_includes_x_merchant_id_header_when_partner_mid_is_provided(self):
        """PhonePeAutoPayClient attaches X-MERCHANT-ID header when partner MID is configured."""
        from services.billing.providers.phonepe_autopay_client import PhonePeAutoPayClient
        mock_oauth = MagicMock()
        mock_oauth.auth_headers.return_value = {"Authorization": "O-Bearer test_token"}
        mock_oauth.api_base = "https://api-preprod.phonepe.com/apis/pg-sandbox"
        mock_session = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.content = b'{"state": "ACTIVE"}'
        mock_resp.json.return_value = {"state": "ACTIVE"}
        mock_session.request.return_value = mock_resp

        client = PhonePeAutoPayClient(oauth_client=mock_oauth, merchant_id="PARTNER_MID_123", http_session=mock_session)
        client.get_subscription_status("SUB_123")
        headers = mock_session.request.call_args[1]["headers"]
        self.assertEqual(headers.get("X-MERCHANT-ID"), "PARTNER_MID_123")

    def test_v2_webhook_verification_without_configured_merchant_id(self):
        """Direct V2 merchant webhook succeeds with SHA-256 Basic Auth and documented payload structure."""
        import hashlib
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id=None)
        # Official PhonePe AutoPay webhook payload structure for setup callback
        raw_payload = json.dumps({
            "event": "checkout.order.completed",
            "payload": {
                "merchantOrderId": "MO1774243061089",
                "orderId": "OMO2603231047412984235557BW",
                "state": "COMPLETED",
                "currency": "INR",
                "amount": 200,
                "paymentFlow": {
                    "type": "SUBSCRIPTION_SETUP",
                    "merchantSubscriptionId": "MS1774243061089",
                    "subscriptionId": "OMS2603231047412704235838BW",
                    "frequency": "MONTHLY"
                },
                "paymentDetails": [
                    {
                        "transactionId": "TXN_SETUP_001",
                        "state": "COMPLETED",
                        "amount": 200,
                    }
                ]
            }
        }).encode("utf-8")
        user = "wh_user"
        pwd = "wh_password"
        # Official PhonePe SHA-256 Basic Auth format: SHA256(username:password)
        auth_token = hashlib.sha256(f"{user}:{pwd}".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": user,
            "PHONEPE_WEBHOOK_PASSWORD": pwd,
            "PHONEPE_MERCHANT_ID": "",
        }):
            with self.app.app_context():
                res = prov.verify_webhook(raw_payload, headers)
                self.assertTrue(res.is_valid)
                self.assertFalse(res.error_message)
                self.assertEqual(res.event_type, "checkout.order.completed")
                self.assertEqual(res.merchant_subscription_id, "MS1774243061089")
                self.assertEqual(res.provider_subscription_id, "OMS2603231047412704235838BW")
                self.assertEqual(res.provider_payment_id, "TXN_SETUP_001")
                self.assertEqual(res.phonepe_order_id, "OMO2603231047412984235557BW")
                self.assertEqual(res.state, "COMPLETED")
                self.assertEqual(res.amount_paise, 200)

    def test_v2_webhook_redemption_callback_extracts_top_level_transaction_id(self):
        """Redemption callback extracting top-level transactionId and orderId as phonepe_order_id."""
        import hashlib
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id=None)
        raw_payload = json.dumps({
            "event": "checkout.order.completed",
            "payload": {
                "merchantOrderId": "MO_RED_123",
                "orderId": "OMO_RED_999",
                "transactionId": "TXN_RED_888",
                "state": "COMPLETED",
                "amount": 50000,
                "paymentFlow": {
                    "type": "SUBSCRIPTION_REDEMPTION",
                    "merchantSubscriptionId": "MS_SUB_123",
                    "subscriptionId": "OMS_SUB_456",
                }
            }
        }).encode("utf-8")
        user = "wh_user"
        pwd = "wh_password"
        auth_token = hashlib.sha256(f"{user}:{pwd}".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": user,
            "PHONEPE_WEBHOOK_PASSWORD": pwd,
            "PHONEPE_MERCHANT_ID": "",
        }):
            with self.app.app_context():
                res = prov.verify_webhook(raw_payload, headers)
                self.assertTrue(res.is_valid)
                self.assertFalse(res.error_message)
                self.assertEqual(res.event_type, "checkout.order.completed")
                self.assertEqual(res.merchant_subscription_id, "MS_SUB_123")
                self.assertEqual(res.provider_subscription_id, "OMS_SUB_456")
                self.assertEqual(res.provider_payment_id, "TXN_RED_888")
                self.assertEqual(res.phonepe_order_id, "OMO_RED_999")
                self.assertEqual(res.state, "COMPLETED")
                self.assertEqual(res.amount_paise, 50000)

    def test_v2_webhook_verification_rejects_base64_authentication(self):
        """Standard Base64 Basic auth is strictly rejected; PhonePe requires SHA256(username:password)."""
        import base64
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id=None)
        raw_payload = json.dumps({"event": "checkout.order.completed", "payload": {"state": "COMPLETED"}}).encode("utf-8")
        user = "wh_user"
        pwd = "wh_password"
        # Standard RFC 7617 Base64
        base64_token = base64.b64encode(f"{user}:{pwd}".encode("utf-8")).decode("utf-8")
        headers = {"Authorization": f"Basic {base64_token}"}
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": user,
            "PHONEPE_WEBHOOK_PASSWORD": pwd,
        }):
            with self.app.app_context():
                res = prov.verify_webhook(raw_payload, headers)
                self.assertFalse(res.is_valid)
                self.assertEqual(res.error_message, "Invalid webhook authorization credentials")

    def test_v2_webhook_verification_rejects_invalid_credentials(self):
        """Webhook with wrong password or incorrect SHA digest is rejected."""
        import hashlib
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id=None)
        raw_payload = json.dumps({"event": "checkout.order.completed", "payload": {"state": "COMPLETED"}}).encode("utf-8")
        wrong_token = hashlib.sha256(b"wh_user:wrong_password").hexdigest()
        headers = {"Authorization": f"Basic {wrong_token}"}
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": "wh_user",
            "PHONEPE_WEBHOOK_PASSWORD": "correct_password",
        }):
            with self.app.app_context():
                res = prov.verify_webhook(raw_payload, headers)
                self.assertFalse(res.is_valid)
                self.assertEqual(res.error_message, "Invalid webhook authorization credentials")

    def test_v2_webhook_verification_rejects_missing_and_malformed_auth_header(self):
        """Webhook rejects missing header, non-Basic schemes, and empty tokens."""
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id=None)
        raw_payload = json.dumps({"event": "checkout.order.completed"}).encode("utf-8")

        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": "wh_user",
            "PHONEPE_WEBHOOK_PASSWORD": "wh_password",
        }):
            with self.app.app_context():
                # 1. Missing header
                res1 = prov.verify_webhook(raw_payload, {})
                self.assertFalse(res1.is_valid)
                self.assertEqual(res1.error_message, "Missing Authorization header")

                # 2. Malformed scheme (Bearer)
                res2 = prov.verify_webhook(raw_payload, {"Authorization": "Bearer some_token"})
                self.assertFalse(res2.is_valid)
                self.assertIn("expected Basic authentication", res2.error_message)

                # 3. Empty Basic token
                res3 = prov.verify_webhook(raw_payload, {"Authorization": "Basic  "})
                self.assertFalse(res3.is_valid)
                self.assertIn("empty Basic authentication token", res3.error_message)

                # 4. Malformed scheme boundary without whitespace separation (e.g. basicfoo)
                res4 = prov.verify_webhook(raw_payload, {"Authorization": "basicfoo some_token"})
                self.assertFalse(res4.is_valid)
                self.assertIn("expected Basic authentication", res4.error_message)

                # 5. Case-insensitive scheme: lowercase 'basic' and mixed-case 'BaSiC' are accepted
                import hashlib
                valid_sha = hashlib.sha256(b"wh_user:wh_password").hexdigest()
                res5 = prov.verify_webhook(raw_payload, {"Authorization": f"basic {valid_sha}"})
                self.assertTrue(res5.is_valid)
                res6 = prov.verify_webhook(raw_payload, {"Authorization": f"BaSiC   {valid_sha}"})
                self.assertTrue(res6.is_valid)

    def test_v2_webhook_verification_rejects_malformed_json_payload(self):
        """Webhook with invalid or corrupted JSON payload is rejected."""
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id=None)
        corrupted_payload = b'{"event": "test", "payload": {'  # Incomplete JSON
        headers = {"Authorization": "Basic dummy"}
        with self.app.app_context():
            res = prov.verify_webhook(corrupted_payload, headers)
            self.assertFalse(res.is_valid)
            self.assertIn("Invalid webhook JSON", res.error_message)

    def test_v2_webhook_verification_rejects_mismatched_merchant_id_when_partner_mid_configured(self):
        """When partner merchant ID is configured, authenticated payload with differing merchantId is rejected."""
        import hashlib
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id="EXPECTED_PARTNER_MID")
        raw_payload = json.dumps({
            "event": "checkout.order.completed",
            "payload": {
                "merchantId": "DIFFERENT_PARTNER_MID",
                "merchantOrderId": "MO123",
                "state": "COMPLETED",
            }
        }).encode("utf-8")
        user = "wh_user"
        pwd = "wh_password"
        auth_token = hashlib.sha256(f"{user}:{pwd}".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": user,
            "PHONEPE_WEBHOOK_PASSWORD": pwd,
        }):
            with self.app.app_context():
                res = prov.verify_webhook(raw_payload, headers)
                self.assertFalse(res.is_valid)
                self.assertIn("Merchant ID mismatch: DIFFERENT_PARTNER_MID", res.error_message)

    def test_v2_webhook_unauthenticated_mismatched_mid_rejected_as_auth_failure(self):
        """Authentication must strictly precede MID mismatch check: unauthenticated request fails on auth."""
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id="EXPECTED_PARTNER_MID")
        raw_payload = json.dumps({
            "event": "checkout.order.completed",
            "payload": {
                "merchantId": "DIFFERENT_PARTNER_MID",
                "merchantOrderId": "MO123",
                "state": "COMPLETED",
            }
        }).encode("utf-8")
        # 1. No Authorization header: fails as missing auth, not leaking MID mismatch
        with self.app.app_context():
            res_no_auth = prov.verify_webhook(raw_payload, {})
            self.assertFalse(res_no_auth.is_valid)
            self.assertEqual(res_no_auth.error_message, "Missing Authorization header")

        # 2. Invalid credentials: fails as invalid credentials, not leaking MID mismatch
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": "wh_user",
            "PHONEPE_WEBHOOK_PASSWORD": "wh_password",
        }):
            with self.app.app_context():
                res_bad_auth = prov.verify_webhook(raw_payload, {"Authorization": "Basic invalid_digest"})
                self.assertFalse(res_bad_auth.is_valid)
                self.assertEqual(res_bad_auth.error_message, "Invalid webhook authorization credentials")

    def test_v2_webhook_verification_accepts_matching_merchant_id_when_partner_mid_configured(self):
        """When partner merchant ID is configured, matching incoming merchantId is accepted."""
        import hashlib
        from services.billing.providers.phonepe_autopay_v2_provider import PhonePeAutoPayV2Provider
        prov = PhonePeAutoPayV2Provider(merchant_id="MY_PARTNER_MID")
        raw_payload = json.dumps({
            "event": "checkout.order.completed",
            "payload": {
                "merchantId": "MY_PARTNER_MID",
                "merchantOrderId": "MO123",
                "state": "COMPLETED",
            }
        }).encode("utf-8")
        user = "wh_user"
        pwd = "wh_password"
        auth_token = hashlib.sha256(f"{user}:{pwd}".encode("utf-8")).hexdigest()
        headers = {"Authorization": f"Basic {auth_token}"}
        with patch.dict(self.app.config, {
            "PHONEPE_WEBHOOK_USERNAME": user,
            "PHONEPE_WEBHOOK_PASSWORD": pwd,
        }):
            with self.app.app_context():
                res = prov.verify_webhook(raw_payload, headers)
                self.assertTrue(res.is_valid)
                self.assertFalse(res.error_message)


if __name__ == "__main__":
    unittest.main()

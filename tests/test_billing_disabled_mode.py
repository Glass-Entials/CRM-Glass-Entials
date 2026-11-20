import unittest

from flask import Flask
from flask_login import LoginManager

from config import validate_production_config
from routes.saas import saas_bp
from routes.auth import _free_plan_available_while_billing_disabled


class FreePlanSignupPolicyTests(unittest.TestCase):
    def test_free_plan_is_allowed_when_billing_is_disabled(self):
        plan = type("Plan", (), {"is_active": True, "monthly_price_paise": 0, "yearly_price_paise": 0})()
        self.assertTrue(_free_plan_available_while_billing_disabled(plan))

    def test_paid_plan_is_rejected_when_billing_is_disabled(self):
        plan = type("Plan", (), {"is_active": True, "monthly_price_paise": 10000, "yearly_price_paise": 100000})()
        self.assertFalse(_free_plan_available_while_billing_disabled(plan))

    def test_inactive_free_plan_is_rejected(self):
        plan = type("Plan", (), {"is_active": False, "monthly_price_paise": 0, "yearly_price_paise": 0})()
        self.assertFalse(_free_plan_available_while_billing_disabled(plan))


class BillingDisabledProductionConfigTests(unittest.TestCase):
    def test_production_config_allows_missing_gateway_credentials_when_billing_disabled(self):
        config = {
            "FLASK_ENV": "production",
            "SECRET_KEY": "test-production-secret",
            "BILLING_ENABLED": False,
            "USE_AWS_SECRETS": False,
        }
        validate_production_config(config, force=True)

    def test_production_config_still_requires_phonepe_when_billing_enabled(self):
        config = {
            "FLASK_ENV": "production",
            "SECRET_KEY": "test-production-secret",
            "BILLING_ENABLED": True,
            "USE_AWS_SECRETS": False,
        }
        with self.assertRaisesRegex(RuntimeError, "BILLING_DEFAULT_PROVIDER"):
            validate_production_config(config, force=True)

    def test_production_mode_is_still_required_when_billing_disabled(self):
        config = {
            "FLASK_ENV": "development",
            "ENV": "production",
            "SECRET_KEY": "test-production-secret",
            "BILLING_ENABLED": False,
            "USE_AWS_SECRETS": False,
        }
        with self.assertRaisesRegex(RuntimeError, "FLASK_ENV must be explicitly set"):
            validate_production_config(config, force=True)


class BillingDisabledRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = Flask(__name__)
        app.config.update(
            SECRET_KEY="test-billing-disabled-secret",
            TESTING=True,
            BILLING_ENABLED=False,
        )
        login_manager = LoginManager()
        login_manager.init_app(app)
        app.register_blueprint(saas_bp)
        cls.app = app

    def setUp(self):
        self.client = self.app.test_client()

    def test_phonepe_callback_is_closed(self):
        response = self.client.get("/saas/phonepe-callback")
        self.assertEqual(response.status_code, 503)

    def test_phonepe_webhook_is_closed(self):
        response = self.client.post("/saas/phonepe-webhook", data=b'{}')
        self.assertEqual(response.status_code, 503)

    def test_legacy_webhook_alias_is_closed(self):
        response = self.client.post("/saas/webhook", data=b'{}')
        self.assertEqual(response.status_code, 503)

    def test_renewal_endpoint_is_closed(self):
        response = self.client.post("/saas/api/process-renewals")
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()

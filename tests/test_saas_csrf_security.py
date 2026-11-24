import unittest
from flask import Flask
from app import app
from model import db, User, Organization, OrganizationStatus, Plan
from flask_wtf.csrf import generate_csrf


class SaaSCSRFSecurityTests(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = True
        self.app.config["BILLING_ENABLED"] = True
        self.app.config["RENEWAL_CRON_SECRET"] = "test-cron-secret-12345"
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        self.client = self.app.test_client()

    def test_create_subscription_without_csrf_is_blocked(self):
        """POST /saas/api/create-subscription must be rejected with 400 when CSRF token is missing."""
        resp = self.client.post("/saas/api/create-subscription", json={"plan_id": 1, "billing_cycle": "MONTHLY"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_data(as_text=True)
        self.assertTrue("CSRF" in data or "csrf" in data.lower())

    def test_verify_payment_without_csrf_is_blocked(self):
        """POST /saas/api/verify-payment must be rejected with 400 when CSRF token is missing."""
        resp = self.client.post("/saas/api/verify-payment", json={"merchant_order_id": "TEST-123"})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_data(as_text=True)
        self.assertTrue("CSRF" in data or "csrf" in data.lower())

    from unittest.mock import patch

    @patch("routes.saas._handle_v3_webhook")
    @patch("routes.saas._handle_v2_webhook")
    def test_webhook_is_csrf_exempt(self, mock_v2, mock_v3):
        """POST /saas/phonepe-webhook must be CSRF exempt and reach webhook handler."""
        mock_v2.return_value = ('{"status": "v2_ok"}', 200, {"Content-Type": "application/json"})
        mock_v3.return_value = ('{"status": "v3_ok"}', 200, {"Content-Type": "application/json"})

        resp = self.client.post(
            "/saas/phonepe-webhook",
            data=b'{"event": "test"}',
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("v3_ok", resp.get_data(as_text=True))

    def test_process_renewals_is_csrf_exempt(self):
        """POST /saas/api/process-renewals must be CSRF exempt and authenticated by X-Cron-Secret."""
        resp = self.client.post(
            "/saas/api/process-renewals",
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIn("error", data)

    def test_reconcile_notifications_is_csrf_exempt(self):
        """POST /saas/api/reconcile-notifications must be CSRF exempt and authenticated by X-Cron-Secret."""
        resp = self.client.post(
            "/saas/api/reconcile-notifications",
            headers={"Content-Type": "application/json"}
        )
        self.assertEqual(resp.status_code, 403)
        data = resp.get_json()
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()

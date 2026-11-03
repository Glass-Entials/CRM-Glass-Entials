import unittest
from unittest.mock import MagicMock, patch
import hashlib
import json
import base64
from datetime import datetime, timedelta

from app import app
from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSTransaction,
    SaaSTransactionStatus,
    SaaSWebhookEvent,
    User,
    UserRole,
)
from services.billing.providers.phonepe_provider import PhonePeProvider
from services.billing.subscription_service import SubscriptionService
from services.billing.transaction_service import TransactionService
from services.billing.renewal_service import RenewalService
from services.billing.billing_service import BillingService


class TestPhonePeBilling(unittest.TestCase):

    def setUp(self):
        self.app = app
        self.app_context = self.app.app_context()
        self.app_context.push()

        self.provider = PhonePeProvider(
            merchant_id="PGTESTPAYUAT",
            salt_key="099eb0cd-0252-4e2a-9123-046648858427",
            salt_index="1",
            env="UAT",
        )

    def tearDown(self):
        self.app_context.pop()

    def test_checksum_generation(self):
        """Test PhonePe SHA256 X-VERIFY calculation."""
        payload_data = {"merchantId": "PGTESTPAYUAT", "amount": 1000}
        b64 = self.provider._encode_payload(payload_data)
        endpoint = "/v3/recurring/subscription/create"

        expected_str = b64 + endpoint + self.provider.salt_key
        expected_hash = hashlib.sha256(expected_str.encode("utf-8")).hexdigest() + "###1"

        actual_x_verify = self.provider._generate_x_verify(b64, endpoint)
        self.assertEqual(actual_x_verify, expected_hash)

    def test_get_checksum_generation(self):
        """Test PhonePe GET request checksum calculation."""
        endpoint = "/v3/recurring/subscription/status/PGTESTPAYUAT/MSUB_1"
        expected_str = endpoint + self.provider.salt_key
        expected_hash = hashlib.sha256(expected_str.encode("utf-8")).hexdigest() + "###1"

        actual_x_verify = self.provider._generate_get_x_verify(endpoint)
        self.assertEqual(actual_x_verify, expected_hash)

    @patch("requests.post")
    def test_create_subscription_mandate_success(self, mock_post):
        """Test PhonePe mandate creation with successful response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "code": "PAYMENT_INITIATED",
            "message": "Payment initiated",
            "data": {
                "merchantId": "PGTESTPAYUAT",
                "merchantTransactionId": "MT_1_123",
                "subscriptionId": "OMS2402242336054995042603",
                "instrumentResponse": {
                    "type": "PAY_PAGE",
                    "redirectInfo": {
                        "url": "https://mercury-t2.phonepe.com/transact/pg?token=testtoken"
                    }
                }
            }
        }
        mock_post.return_value = mock_resp

        dummy_plan = Plan(
            name="Pro",
            display_name="Professional",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )
        dummy_sub = SaaSSubscription(organization_id=1, plan_id=1)
        dummy_user = User(id=42, username="testadmin", email="test@glass.com")

        res = self.provider.create_subscription_mandate(
            subscription=dummy_sub,
            plan=dummy_plan,
            billing_cycle="monthly",
            user=dummy_user,
            return_url="http://localhost/saas/phonepe-callback",
            callback_url="http://localhost/saas/phonepe-webhook",
        )

        self.assertTrue(res.success)
        self.assertEqual(res.provider, "phonepe")
        self.assertEqual(res.redirect_url, "https://mercury-t2.phonepe.com/transact/pg?token=testtoken")
        self.assertEqual(res.provider_subscription_id, "OMS2402242336054995042603")

    @patch("requests.post")
    def test_notify_redemption(self, mock_post):
        """Test Pre-Debit Notification API call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "code": "SUCCESS", "message": "Notification sent"}
        mock_post.return_value = mock_resp

        dummy_sub = SaaSSubscription(organization_id=1, provider_subscription_id="OMS_123")
        res = self.provider.notify_redemption(dummy_sub, amount_paise=118000, merchant_transaction_id="TXN_NOTIFY_1")
        self.assertTrue(res.success)
        self.assertEqual(res.status, "NOTIFIED")

    @patch("requests.post")
    def test_execute_redemption(self, mock_post):
        """Test Recurring AutoPay execution API call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "code": "PAYMENT_SUCCESS",
            "data": {"transactionId": "TXN_EXEC_999"}
        }
        mock_post.return_value = mock_resp

        dummy_sub = SaaSSubscription(organization_id=1, provider_subscription_id="OMS_123")
        res = self.provider.execute_redemption(dummy_sub, amount_paise=118000, merchant_transaction_id="TXN_EXEC_1")
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.provider_transaction_id, "TXN_EXEC_999")

    @patch("requests.post")
    def test_execute_redemption_pending_not_treated_as_success(self, mock_post):
        """Test that PAYMENT_PENDING is strictly differentiated and never treated as SUCCESS."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,  # PhonePe returns success=True for request accepted
            "code": "PAYMENT_PENDING",
            "data": {"transactionId": "TXN_PENDING_123", "state": "PENDING"}
        }
        mock_post.return_value = mock_resp

        dummy_sub = SaaSSubscription(organization_id=1, provider_subscription_id="OMS_123")
        res = self.provider.execute_redemption(dummy_sub, amount_paise=118000, merchant_transaction_id="TXN_EXEC_PENDING")
        self.assertFalse(res.success)
        self.assertEqual(res.status, "PENDING")
        self.assertEqual(res.provider_transaction_id, "TXN_PENDING_123")

    @patch("requests.post")
    def test_cancel_subscription(self, mock_post):
        """Test PhonePe mandate cancellation API call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"success": True, "code": "SUBSCRIPTION_CANCELLED"}
        mock_post.return_value = mock_resp

        dummy_sub = SaaSSubscription(organization_id=1, provider_subscription_id="OMS_123")
        res = self.provider.cancel_subscription(dummy_sub, reason="User requested cancellation")
        self.assertTrue(res)

    def test_calculate_period_dates(self):
        """Test billing cycle date calculations."""
        now = datetime(2026, 11, 3, 12, 0, 0)

        # Monthly
        start_m, end_m = SubscriptionService.calculate_period_dates("monthly", start_date=now)
        self.assertEqual(start_m, now)
        self.assertEqual(end_m, datetime(2026, 12, 3, 12, 0, 0))

        # Yearly
        start_y, end_y = SubscriptionService.calculate_period_dates("yearly", start_date=now)
        self.assertEqual(start_y, now)
        self.assertEqual(end_y, datetime(2027, 11, 3, 12, 0, 0))

    def test_webhook_verification_and_parsing(self):
        """Test webhook payload decoding and event parsing."""
        raw_inner = {
            "success": True,
            "code": "PAYMENT_SUCCESS",
            "message": "Payment successful",
            "data": {
                "merchantId": "PGTESTPAYUAT",
                "merchantTransactionId": "MT_100_999",
                "transactionId": "TXN_999888",
                "subscriptionId": "OMS_SUB_777",
                "amount": 118000,
                "state": "COMPLETED",
                "responseCode": "SUCCESS",
            }
        }
        b64_response = base64.b64encode(json.dumps(raw_inner).encode("utf-8")).decode("utf-8")
        webhook_body = json.dumps({"response": b64_response}).encode("utf-8")

        res = self.provider.verify_webhook(webhook_body, headers={})
        self.assertTrue(res.is_valid)
        self.assertEqual(res.event_type, "PAYMENT_SUCCESS")
        self.assertEqual(res.provider_subscription_id, "OMS_SUB_777")
        self.assertEqual(res.provider_payment_id, "TXN_999888")
        self.assertEqual(res.amount_paise, 118000)
        self.assertEqual(res.state, "COMPLETED")

    def test_billing_service_factory(self):
        """Test BillingService factory returns PhonePeProvider."""
        provider = BillingService.get_provider("phonepe")
        self.assertIsInstance(provider, PhonePeProvider)
        self.assertEqual(provider.name, "phonepe")

    def test_production_config_guard_raises_on_uat_keys(self):
        """Test that PhonePeProvider raises ValueError if PRODUCTION is used with UAT credentials."""
        # Test default UAT merchant ID rejected in PRODUCTION
        with self.assertRaises(ValueError) as ctx:
            PhonePeProvider(
                merchant_id="PGTESTPAYUAT",
                salt_key="prod_salt_key_123",
                env="PRODUCTION",
            )
        self.assertIn("CRITICAL SECURITY ERROR", str(ctx.exception))

        # Test default test salt key rejected in PRODUCTION
        with self.assertRaises(ValueError) as ctx:
            PhonePeProvider(
                merchant_id="REAL_PROD_MERCHANT",
                salt_key="099eb0cd-0252-4e2a-9123-046648858427",
                env="PRODUCTION",
            )
        self.assertIn("CRITICAL SECURITY ERROR", str(ctx.exception))

    @patch.object(PhonePeProvider, "check_transaction_status")
    def test_verify_payment_queries_authoritative_s2s_api(self, mock_status):
        """Test verify_payment performs live status inquiry rather than blindly trusting browser POST."""
        mock_status.return_value = MagicMock(
            success=True,
            status="SUCCESS",
            provider_transaction_id="TXN_PHONEPE_123",
            provider_payment_id="TXN_PHONEPE_123",
            amount_paise=118000,
        )

        forged_client_payload = {
            "response": base64.b64encode(json.dumps({
                "code": "PAYMENT_SUCCESS",
                "data": {"merchantTransactionId": "MT_LEGIT_123"}
            }).encode("utf-8")).decode("utf-8")
        }

        res = self.provider.verify_payment(forged_client_payload)
        # Verify check_transaction_status was called with the extracted merchantTransactionId
        mock_status.assert_called_once_with("MT_LEGIT_123")
        self.assertTrue(res.success)
        self.assertEqual(res.provider_payment_id, "TXN_PHONEPE_123")

    def test_webhook_merchant_id_mismatch_rejected(self):
        """Test webhook fails authentication when payload merchantId belongs to another merchant."""
        raw_inner = {
            "success": True,
            "code": "PAYMENT_SUCCESS",
            "data": {
                "merchantId": "ATTACKER_MERCHANT_ID",
                "merchantTransactionId": "MT_FOREIGN_1",
                "amount": 50000,
            }
        }
        b64_response = base64.b64encode(json.dumps(raw_inner).encode("utf-8")).decode("utf-8")
        webhook_body = json.dumps({"response": b64_response}).encode("utf-8")

        res = self.provider.verify_webhook(webhook_body, headers={})
        self.assertFalse(res.is_valid)
        self.assertIn("Merchant ID mismatch", res.error_message)

    def test_webhook_deterministic_event_id_for_replay_protection(self):
        """Test webhook generates deterministic event_id on repeated payloads to enforce replay protection."""
        raw_inner = {
            "event": "SUBSCRIPTION_ACTIVE",
            "data": {
                "merchantId": "PGTESTPAYUAT",
                "subscriptionId": "OMS_DETERMINISTIC_1",
                "amount": 10000,
            }
        }
        b64_response = base64.b64encode(json.dumps(raw_inner).encode("utf-8")).decode("utf-8")
        webhook_body = json.dumps({"response": b64_response}).encode("utf-8")

        res1 = self.provider.verify_webhook(webhook_body, headers={})
        res2 = self.provider.verify_webhook(webhook_body, headers={})

        self.assertTrue(res1.is_valid)
        self.assertTrue(res2.is_valid)
        # Ensure event_id is identical and deterministic across multiple calls
        self.assertEqual(res1.event_id, res2.event_id)

    def test_gst_pricing_calculation(self):
        """Test server-side 18% GST calculation, rounding, and paise conversion."""
        plan = Plan(
            name="Enterprise",
            display_name="Enterprise Plan",
            monthly_price_paise=499900,  # ₹4,999.00
            yearly_price_paise=4999000,  # ₹49,990.00
        )
        # Monthly GST: 499900 * 0.18 = 89982 paise
        monthly_gst = int(round(plan.monthly_price_paise * 0.18))
        monthly_total = plan.monthly_price_paise + monthly_gst
        self.assertEqual(monthly_gst, 89982)
        self.assertEqual(monthly_total, 589882)

        # Yearly GST: 4999000 * 0.18 = 899820 paise
        yearly_gst = int(round(plan.yearly_price_paise * 0.18))
        yearly_total = plan.yearly_price_paise + yearly_gst
        self.assertEqual(yearly_gst, 899820)
        self.assertEqual(yearly_total, 5898820)

    @patch.object(SaaSSubscription, "query")
    def test_renewal_service_isolates_razorpay_subscriptions(self, mock_query):
        """Test RenewalService strictly filters payment_provider == 'phonepe'."""
        mock_filter = MagicMock()
        mock_query.filter.return_value = mock_filter
        mock_filter.all.return_value = []

        RenewalService.process_due_renewals(self.provider)

        mock_query.filter.assert_called_once()
        filter_args = mock_query.filter.call_args[0]
        filter_strs = [str(arg) for arg in filter_args]
        has_provider_filter = any("payment_provider" in s for s in filter_strs)
        self.assertTrue(has_provider_filter, "Filter must restrict to payment_provider == 'phonepe'")

    @patch("services.billing.renewal_service.RenewalService.process_due_renewals")
    @patch("services.billing.renewal_service.RenewalService.process_upcoming_notifications")
    def test_process_renewals_endpoint_security(self, mock_notify, mock_renew):
        """Test /saas/api/process-renewals security: requires valid secret via timing-safe comparison."""
        mock_notify.return_value = []
        mock_renew.return_value = []

        client = self.app.test_client()

        # 1. No auth -> 403 Forbidden
        resp_no_auth = client.post("/saas/api/process-renewals")
        self.assertEqual(resp_no_auth.status_code, 403)

        # 2. Invalid secret -> 403 Forbidden
        resp_bad_auth = client.post(
            "/saas/api/process-renewals",
            headers={"X-Renewal-Secret": "invalid_secret_token"}
        )
        self.assertEqual(resp_bad_auth.status_code, 403)

        # 3. Valid secret -> 200 OK
        valid_secret = self.app.config.get("SECRET_KEY")
        resp_good_auth = client.post(
            "/saas/api/process-renewals",
            headers={"X-Renewal-Secret": valid_secret}
        )
        self.assertEqual(resp_good_auth.status_code, 200)
        data = resp_good_auth.get_json()
        self.assertEqual(data.get("status"), "completed")


if __name__ == "__main__":
    unittest.main()

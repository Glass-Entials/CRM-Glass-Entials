"""
Unit tests for PhonePe AutoPay V2 API Client
============================================
Tests PhonePeAutoPayClient against mocked HTTP responses.
No real gateway calls are made.

Run with:
    pytest test_phonepe_autopay_client.py -o cache_dir=tmp_pytest_cache -v
"""

import unittest
from unittest.mock import MagicMock, call, patch
import requests

from services.billing.providers.phonepe_oauth import PhonePeOAuthClient
from services.billing.providers.phonepe_autopay_client import (
    PhonePeAutoPayClient,
    PhonePeAPIError,
    PhonePeAuthenticationError,
    PhonePeNetworkError,
    PhonePeResponseError,
    _PATH_SETUP,
    _PATH_SUB_STATUS,
    _PATH_NOTIFY,
    _PATH_ORDER_STATUS,
    _PATH_REDEEM,
    _PATH_CANCEL,
)


class BaseAutoPayClientTest(unittest.TestCase):
    """Base setup for mocking OAuth and HTTP session."""

    def setUp(self):
        self.mock_oauth = MagicMock(spec=PhonePeOAuthClient)
        self.mock_oauth.env = "UAT"
        self.mock_oauth.api_base = "https://api-preprod.phonepe.com/apis/pg-sandbox"
        self.mock_oauth.auth_headers.return_value = {"Authorization": "O-Bearer mock_token_123"}

        self.mock_session = MagicMock(spec=requests.Session)

        self.client = PhonePeAutoPayClient(
            oauth_client=self.mock_oauth,
            merchant_id="TEST_MERCHANT_123",
            http_session=self.mock_session,
        )

    def _make_response(self, status_code=200, json_data=None, content=b"mock", ok=None):
        resp = MagicMock(spec=requests.Response)
        resp.status_code = status_code
        resp.ok = (status_code < 400) if ok is None else ok
        resp.content = content
        if json_data is not None:
            resp.json.return_value = json_data
        elif content == b"":
            resp.json.side_effect = ValueError("No JSON object could be decoded")
        else:
            resp.json.return_value = {}
        return resp


class TestEndpointAndHeaderSelection(BaseAutoPayClientTest):

    def test_uat_endpoint_selection(self):
        """1. UAT endpoint selection."""
        self.mock_oauth.env = "UAT"
        self.mock_oauth.api_base = "https://api-preprod.phonepe.com/apis/pg-sandbox"
        client = PhonePeAutoPayClient(self.mock_oauth, http_session=self.mock_session)

        self.mock_session.request.return_value = self._make_response(200, {"success": True})
        client.get_subscription_status("SUB_123")

        call_args = self.mock_session.request.call_args
        called_url = call_args[0][1]
        self.assertTrue(called_url.startswith("https://api-preprod.phonepe.com/apis/pg-sandbox/subscriptions/v2/"))

    def test_production_endpoint_selection(self):
        """2. Production endpoint selection."""
        self.mock_oauth.env = "PRODUCTION"
        self.mock_oauth.api_base = "https://api.phonepe.com/apis/pg"
        client = PhonePeAutoPayClient(self.mock_oauth, http_session=self.mock_session)

        self.mock_session.request.return_value = self._make_response(200, {"success": True})
        client.get_subscription_status("SUB_123")

        call_args = self.mock_session.request.call_args
        called_url = call_args[0][1]
        self.assertTrue(called_url.startswith("https://api.phonepe.com/apis/pg/subscriptions/v2/"))

    def test_correct_oauth_authorization_header(self):
        """3. Correct OAuth Authorization header."""
        self.mock_session.request.return_value = self._make_response(200, {"state": "ACTIVE"})
        self.client.get_subscription_status("SUB_123")

        call_args = self.mock_session.request.call_args
        headers = call_args[1].get("headers", {})
        self.assertEqual(headers.get("Authorization"), "O-Bearer mock_token_123")
        self.assertEqual(headers.get("Accept"), "application/json")
        self.assertEqual(headers.get("Content-Type"), "application/json")

    def test_optional_x_merchant_id_header(self):
        """10. Optional X-MERCHANT-ID header."""
        # When configured
        self.mock_session.request.return_value = self._make_response(200, {})
        self.client.get_subscription_status("SUB_123")
        headers = self.mock_session.request.call_args[1]["headers"]
        self.assertEqual(headers.get("X-MERCHANT-ID"), "TEST_MERCHANT_123")

        # When omitted / None
        client_no_mid = PhonePeAutoPayClient(
            self.mock_oauth, merchant_id=None, http_session=self.mock_session
        )
        self.mock_session.request.reset_mock()
        client_no_mid.get_subscription_status("SUB_123")
        headers_no_mid = self.mock_session.request.call_args[1]["headers"]
        self.assertNotIn("X-MERCHANT-ID", headers_no_mid)


class TestApiMethods(BaseAutoPayClientTest):

    def test_setup_subscription_correct_body_and_path(self):
        """4. Correct JSON body for setup."""
        payload = {
            "merchantSubscriptionId": "MSUB_123",
            "amount": 49900,
            "paymentFlow": {"type": "SUBSCRIPTION"},
        }
        resp_data = {"status": "SUCCESS", "redirectUrl": "https://phonepe.com/pay"}
        self.mock_session.request.return_value = self._make_response(200, resp_data)

        res = self.client.setup_subscription(payload)

        self.assertEqual(res, resp_data)
        self.mock_session.request.assert_called_once()
        method, url = self.mock_session.request.call_args[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith(_PATH_SETUP))
        self.assertEqual(self.mock_session.request.call_args[1]["json"], payload)

    def test_subscription_status_query_params(self):
        """5. Subscription status query parameters."""
        resp_data = {"state": "ACTIVE", "subscriptionId": "SUB_PROV_1"}
        self.mock_session.request.return_value = self._make_response(200, resp_data)

        # Default details=True
        res = self.client.get_subscription_status("MSUB_001")
        self.assertEqual(res, resp_data)
        call_kwargs = self.mock_session.request.call_args[1]
        self.assertEqual(call_kwargs["params"], {"details": "true"})

        # details=False
        self.mock_session.request.reset_mock()
        self.client.get_subscription_status("MSUB_001", details=False)
        call_kwargs = self.mock_session.request.call_args[1]
        self.assertEqual(call_kwargs["params"], {"details": "false"})

    def test_notify_redemption_request(self):
        """6. Notify request."""
        payload = {
            "merchantOrderId": "MO_001",
            "merchantSubscriptionId": "MSUB_001",
            "amount": 99900,
            "paymentFlow": {"type": "SUBSCRIPTION_REDEMPTION"},
        }
        resp_data = {"state": "NOTIFICATION_ACCEPTED"}
        self.mock_session.request.return_value = self._make_response(200, resp_data)

        res = self.client.notify_redemption(payload)
        self.assertEqual(res, resp_data)

        method, url = self.mock_session.request.call_args[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith(_PATH_NOTIFY))
        self.assertEqual(self.mock_session.request.call_args[1]["json"], payload)

    def test_order_status_request(self):
        """7. Order status request."""
        resp_data = {"state": "COMPLETED", "amount": 99900}
        self.mock_session.request.return_value = self._make_response(200, resp_data)

        res = self.client.get_order_status("MO_001", details=True)
        self.assertEqual(res, resp_data)

        method, url = self.mock_session.request.call_args[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/checkout/v2/order/MO_001/status"))
        self.assertEqual(self.mock_session.request.call_args[1]["params"], {"details": "true"})

    def test_redemption_request(self):
        """8. Redemption request."""
        payload = {
            "merchantOrderId": "MO_001",
            "merchantSubscriptionId": "MSUB_001",
            "amount": 99900,
        }
        resp_data = {"state": "PENDING"}
        self.mock_session.request.return_value = self._make_response(200, resp_data)

        res = self.client.redeem(payload)
        self.assertEqual(res, resp_data)

        method, url = self.mock_session.request.call_args[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith(_PATH_REDEEM))
        self.assertEqual(self.mock_session.request.call_args[1]["json"], payload)

    def test_cancellation_request(self):
        """9. Cancellation request."""
        self.mock_session.request.return_value = self._make_response(204, content=b"")

        res = self.client.cancel_subscription("MSUB_999")
        self.assertEqual(res, {})

        method, url = self.mock_session.request.call_args[0]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/checkout/v2/subscriptions/MSUB_999/cancel"))

    def test_http_204_handling(self):
        """11. HTTP 204 handling."""
        resp = self._make_response(204, content=b"")
        self.mock_session.request.return_value = resp

        res = self.client.cancel_subscription("MSUB_001")
        self.assertEqual(res, {})
        # Ensure json() was not called
        resp.json.assert_not_called()


class TestErrorHandlingAndRetries(BaseAutoPayClientTest):

    def test_http_400_error_handling(self):
        """12. HTTP 400 error handling."""
        resp = self._make_response(
            400,
            json_data={"code": "BAD_REQUEST", "message": "Invalid subscription payload"},
        )
        self.mock_session.request.return_value = resp

        with self.assertRaises(PhonePeResponseError) as ctx:
            self.client.setup_subscription({"some": "data"})

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.error_code, "BAD_REQUEST")
        self.assertIn("Invalid subscription payload", str(ctx.exception))

    def test_http_401_token_invalidation_and_one_retry(self):
        """13. HTTP 401 token invalidation and one retry."""
        resp_401 = self._make_response(401, json_data={"code": "UNAUTHORIZED"})
        resp_200 = self._make_response(200, json_data={"state": "ACTIVE"})

        self.mock_session.request.side_effect = [resp_401, resp_200]

        res = self.client.get_subscription_status("MSUB_1")
        self.assertEqual(res, {"state": "ACTIVE"})

        # OAuth cache was invalidated
        self.mock_oauth.invalidate_cache.assert_called_once()
        # Two HTTP requests made
        self.assertEqual(self.mock_session.request.call_count, 2)

    def test_second_401_rejection(self):
        """14. Second 401 rejection."""
        resp_401_1 = self._make_response(401, json_data={"code": "UNAUTHORIZED"})
        resp_401_2 = self._make_response(401, json_data={"code": "UNAUTHORIZED"})

        self.mock_session.request.side_effect = [resp_401_1, resp_401_2]

        with self.assertRaises(PhonePeAuthenticationError) as ctx:
            self.client.get_subscription_status("MSUB_1")

        self.assertEqual(ctx.exception.status_code, 401)
        self.assertIn("HTTP 401 Unauthorized after token refresh", str(ctx.exception))
        # Invalidated cache once before the retry
        self.mock_oauth.invalidate_cache.assert_called_once()
        # Exactly two requests made
        self.assertEqual(self.mock_session.request.call_count, 2)

    def test_http_403_404_429_5xx_handling(self):
        """15. HTTP 403 / 404 / 429 / 5xx handling."""
        status_map = [
            (403, PhonePeAuthenticationError),
            (404, PhonePeResponseError),
            (429, PhonePeResponseError),
            (500, PhonePeResponseError),
            (502, PhonePeResponseError),
            (503, PhonePeResponseError),
        ]

        for status, exc_class in status_map:
            with self.subTest(status=status):
                self.mock_session.request.reset_mock()
                self.mock_session.request.side_effect = None
                self.mock_session.request.return_value = self._make_response(
                    status, json_data={"code": f"ERR_{status}", "message": f"Error {status}"}
                )

                with self.assertRaises(exc_class) as ctx:
                    self.client.get_subscription_status("MSUB_1")

                self.assertEqual(ctx.exception.status_code, status)

    def test_connection_timeout(self):
        """16. Connection timeout."""
        self.mock_session.request.side_effect = requests.exceptions.Timeout("Connection timed out")

        with self.assertRaises(PhonePeNetworkError) as ctx:
            self.client.setup_subscription({"amount": 100})

        self.assertIn("timed out", str(ctx.exception))

    def test_network_errors(self):
        """17. Network errors."""
        self.mock_session.request.side_effect = requests.exceptions.ConnectionError("Network unreachable")

        with self.assertRaises(PhonePeNetworkError) as ctx:
            self.client.get_subscription_status("MSUB_1")

        self.assertIn("connection error", str(ctx.exception))

    def test_non_json_response_on_200(self):
        """18. Non-JSON response on 200."""
        resp = self._make_response(200, content=b"<html>Internal Server Error Mock</html>")
        resp.json.side_effect = ValueError("Invalid JSON")
        self.mock_session.request.return_value = resp

        with self.assertRaises(PhonePeResponseError) as ctx:
            self.client.get_subscription_status("MSUB_1")

        self.assertEqual(ctx.exception.status_code, 200)
        self.assertIn("returned non-JSON body", str(ctx.exception))

    def test_malformed_json_response_on_error(self):
        """19. Malformed JSON response on 500 error."""
        resp = self._make_response(500, content=b"Gateway Error HTML")
        resp.json.side_effect = ValueError("Invalid JSON")
        self.mock_session.request.return_value = resp

        with self.assertRaises(PhonePeResponseError) as ctx:
            self.client.get_subscription_status("MSUB_1")

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("HTTP 500", str(ctx.exception))

    def test_missing_required_method_arguments(self):
        """20. Missing required method arguments."""
        # Empty dicts or non-dicts
        with self.assertRaises(ValueError):
            self.client.setup_subscription({})
        with self.assertRaises(ValueError):
            self.client.setup_subscription(None)

        with self.assertRaises(ValueError):
            self.client.notify_redemption({})
        with self.assertRaises(ValueError):
            self.client.notify_redemption(None)

        with self.assertRaises(ValueError):
            self.client.redeem({})
        with self.assertRaises(ValueError):
            self.client.redeem(None)

        # Empty strings or non-strings
        with self.assertRaises(ValueError):
            self.client.get_subscription_status("")
        with self.assertRaises(ValueError):
            self.client.get_subscription_status(None)

        with self.assertRaises(ValueError):
            self.client.get_order_status("")
        with self.assertRaises(ValueError):
            self.client.get_order_status(None)

        with self.assertRaises(ValueError):
            self.client.cancel_subscription("")
        with self.assertRaises(ValueError):
            self.client.cancel_subscription(None)

    def test_safe_url_encoding_of_identifiers(self):
        """21. Safe URL encoding of identifiers."""
        self.mock_session.request.return_value = self._make_response(200, {"state": "ACTIVE"})

        self.client.get_subscription_status("MSUB/123+special#id")
        method, url = self.mock_session.request.call_args[0]
        self.assertIn("MSUB%2F123%2Bspecial%23id", url)

        self.client.get_order_status("ORDER/456 special")
        method, url = self.mock_session.request.call_args[0]
        self.assertIn("ORDER%2F456%20special", url)

        self.client.cancel_subscription("CANCEL/789")
        method, url = self.mock_session.request.call_args[0]
        self.assertIn("CANCEL%2F789", url)

    def test_no_network_retry_after_ambiguous_post_failure(self):
        """22. Verify that no network retry occurs after ambiguous POST failure."""
        # 1. Timeout during redeem()
        self.mock_session.request.side_effect = requests.exceptions.Timeout("Read timeout on redeem")
        with self.assertRaises(PhonePeNetworkError):
            self.client.redeem({"merchantOrderId": "MO_1", "amount": 100})
        self.assertEqual(self.mock_session.request.call_count, 1)

        # 2. 500 error during setup_subscription()
        self.mock_session.request.reset_mock()
        self.mock_session.request.side_effect = None
        self.mock_session.request.return_value = self._make_response(500, json_data={"code": "INTERNAL_ERROR"})

        with self.assertRaises(PhonePeResponseError):
            self.client.setup_subscription({"merchantSubscriptionId": "MSUB_1"})
        self.assertEqual(self.mock_session.request.call_count, 1)

        # 3. ConnectionError during notify_redemption()
        self.mock_session.request.reset_mock()
        self.mock_session.request.side_effect = requests.exceptions.ConnectionError("Connection aborted")

        with self.assertRaises(PhonePeNetworkError):
            self.client.notify_redemption({"merchantOrderId": "MO_1"})
        self.assertEqual(self.mock_session.request.call_count, 1)


class TestFactoryAndRepr(BaseAutoPayClientTest):

    def test_from_oauth_client_factory(self):
        client = PhonePeAutoPayClient.from_oauth_client(
            self.mock_oauth,
            merchant_id="M_ID",
            connect_timeout=5.0,
            read_timeout=15.0,
            http_session=self.mock_session,
        )
        self.assertEqual(client._merchant_id, "M_ID")
        self.assertEqual(client._connect_timeout, 5.0)
        self.assertEqual(client._read_timeout, 15.0)
        self.assertIn("PhonePeAutoPayClient", repr(client))
        self.assertIn("TEST_MERCHANT_123", repr(self.client))


if __name__ == "__main__":
    unittest.main()

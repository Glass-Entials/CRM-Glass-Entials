"""
test_phonepe_oauth.py
=====================
Unit tests for services/billing/providers/phonepe_oauth.py

All HTTP calls are mocked — no real PhonePe gateway contact is made.
Run with:
    pytest test_phonepe_oauth.py -o cache_dir=tmp_pytest_cache -v
"""

import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

import requests

from services.billing.providers.phonepe_oauth import (
    PhonePeOAuthClient,
    PhonePeOAuthConfigError,
    PhonePeOAuthError,
    UAT_OAUTH_URL,
    PROD_OAUTH_URL,
    EXPIRY_BUFFER_SECONDS,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_session(access_token="tok_test_abc", expires_at=None, status_code=200):
    """Return a mock session whose .post() returns a well-formed token response."""
    if expires_at is None:
        expires_at = int(time.time()) + 3600  # 1 hour from now

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.ok = (status_code < 400)
    mock_response.json.return_value = {
        "access_token": access_token,
        "expires_at": expires_at,
        "token_type": "O-Bearer",
    }

    mock_session = MagicMock()
    mock_session.post.return_value = mock_response
    return mock_session, mock_response


def _make_client(mock_session, env="UAT", client_id="test-client-id",
                 client_secret="test-client-secret", client_version="1"):
    """Convenience: build a PhonePeOAuthClient with an injected mock session."""
    return PhonePeOAuthClient(
        client_id=client_id,
        client_secret=client_secret,
        client_version=client_version,
        env=env,
        http_session=mock_session,
    )


# ── Test Cases ─────────────────────────────────────────────────────────────────

class TestSuccessfulTokenGeneration(unittest.TestCase):
    """TC-1: get_access_token() returns the token string on a 200 response."""

    def test_returns_access_token_string(self):
        mock_session, _ = _make_session(access_token="tok_abc123")
        client = _make_client(mock_session)

        token = client.get_access_token()

        self.assertEqual(token, "tok_abc123")


class TestCorrectRequestFormat(unittest.TestCase):
    """TC-2: Verify Content-Type header and form fields sent to the endpoint."""

    def test_form_urlencoded_content_type(self):
        mock_session, _ = _make_session()
        client = _make_client(mock_session, client_id="my-id",
                              client_secret="my-secret", client_version="2")
        client.get_access_token()

        post_call_kwargs = mock_session.post.call_args
        sent_headers = post_call_kwargs.kwargs.get("headers") or post_call_kwargs[1].get("headers", {})
        self.assertEqual(sent_headers.get("Content-Type"), "application/x-www-form-urlencoded")

    def test_required_form_fields(self):
        mock_session, _ = _make_session()
        client = _make_client(mock_session, client_id="my-id",
                              client_secret="my-secret", client_version="2")
        client.get_access_token()

        post_call_kwargs = mock_session.post.call_args
        sent_data = post_call_kwargs.kwargs.get("data") or post_call_kwargs[1].get("data", {})
        self.assertEqual(sent_data.get("client_id"), "my-id")
        self.assertEqual(sent_data.get("client_secret"), "my-secret")
        self.assertEqual(sent_data.get("client_version"), "2")
        self.assertEqual(sent_data.get("grant_type"), "client_credentials")


class TestCorrectUATEndpoint(unittest.TestCase):
    """TC-3: env='UAT' uses the pre-production OAuth URL."""

    def test_uat_endpoint_is_used(self):
        mock_session, _ = _make_session()
        client = _make_client(mock_session, env="UAT")
        client.get_access_token()

        actual_url = mock_session.post.call_args.args[0] if mock_session.post.call_args.args \
            else mock_session.post.call_args.kwargs.get("url") \
            or mock_session.post.call_args[0][0]
        self.assertEqual(actual_url, UAT_OAUTH_URL)


class TestCorrectProductionEndpoint(unittest.TestCase):
    """TC-4: env='PRODUCTION' uses the live OAuth URL."""

    def test_production_endpoint_is_used(self):
        mock_session, _ = _make_session()
        client = _make_client(mock_session, env="PRODUCTION")
        client.get_access_token()

        actual_url = mock_session.post.call_args.args[0] if mock_session.post.call_args.args \
            else mock_session.post.call_args.kwargs.get("url") \
            or mock_session.post.call_args[0][0]
        self.assertEqual(actual_url, PROD_OAUTH_URL)


class TestAccessTokenCaching(unittest.TestCase):
    """TC-5: Second call to get_access_token() does NOT make a second HTTP POST."""

    def test_only_one_http_call_for_two_get_access_token_calls(self):
        mock_session, _ = _make_session()
        client = _make_client(mock_session)

        token1 = client.get_access_token()
        token2 = client.get_access_token()

        self.assertEqual(token1, token2)
        self.assertEqual(mock_session.post.call_count, 1)


class TestTokenRefreshNearExpiry(unittest.TestCase):
    """TC-6: Token with expires_at within EXPIRY_BUFFER_SECONDS triggers a fresh fetch."""

    def test_nearly_expired_token_is_refreshed(self):
        # First fetch: token expires in 30 seconds (within 60 s buffer)
        near_expiry = int(time.time()) + 30
        mock_session, _ = _make_session(access_token="old_token", expires_at=near_expiry)
        client = _make_client(mock_session)

        # Prime the cache with the nearly-expired token
        token1 = client.get_access_token()
        # Sanity: we got the first token
        self.assertEqual(token1, "old_token")

        # Now prepare a fresh token for the second call
        fresh_expiry = int(time.time()) + 3600
        fresh_response = MagicMock()
        fresh_response.status_code = 200
        fresh_response.ok = True
        fresh_response.json.return_value = {
            "access_token": "fresh_token",
            "expires_at": fresh_expiry,
            "token_type": "O-Bearer",
        }
        mock_session.post.return_value = fresh_response

        token2 = client.get_access_token()

        # The cache should have been deemed invalid and a second call made
        self.assertEqual(token2, "fresh_token")
        self.assertEqual(mock_session.post.call_count, 2)


class TestInvalidCredentials(unittest.TestCase):
    """TC-7: HTTP 401 raises PhonePeOAuthError."""

    def test_http_401_raises_oauth_error(self):
        mock_session, _ = _make_session(status_code=401)
        client = _make_client(mock_session)

        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()


class TestHTTPServerError(unittest.TestCase):
    """TC-8: HTTP 500 raises PhonePeOAuthError."""

    def test_http_500_raises_oauth_error(self):
        mock_session, _ = _make_session(status_code=500)
        client = _make_client(mock_session)

        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()


class TestNetworkTimeout(unittest.TestCase):
    """TC-9: requests.exceptions.Timeout raises PhonePeOAuthError."""

    def test_timeout_raises_oauth_error(self):
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.exceptions.Timeout("timed out")
        client = _make_client(mock_session)

        with self.assertRaises(PhonePeOAuthError) as ctx:
            client.get_access_token()

        self.assertIn("timeout", str(ctx.exception).lower())


class TestMalformedTokenResponse(unittest.TestCase):
    """TC-10: Response missing access_token raises PhonePeOAuthError."""

    def test_missing_access_token_field(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            # access_token intentionally absent
            "expires_at": int(time.time()) + 3600,
            "token_type": "O-Bearer",
        }
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        client = _make_client(mock_session)

        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    def test_empty_access_token_field(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = {
            "access_token": "",
            "expires_at": int(time.time()) + 3600,
            "token_type": "O-Bearer",
        }
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        client = _make_client(mock_session)

        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()


class TestMissingConfiguration(unittest.TestCase):
    """TC-11: Empty client_id or client_secret raises PhonePeOAuthConfigError."""

    def test_missing_client_id_raises_config_error(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(
                client_id="",
                client_secret="some-secret",
            )

    def test_missing_client_secret_raises_config_error(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(
                client_id="some-id",
                client_secret="",
            )

    def test_from_config_missing_client_id(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient.from_config(
                {"PHONEPE_CLIENT_ID": "", "PHONEPE_CLIENT_SECRET": "s"}
            )

    def test_from_config_missing_client_secret(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient.from_config(
                {"PHONEPE_CLIENT_ID": "id", "PHONEPE_CLIENT_SECRET": ""}
            )


class TestConcurrentTokenRequests(unittest.TestCase):
    """
    TC-12: Two threads calling get_access_token() simultaneously result in
    exactly ONE HTTP call.

    Strategy: use a threading.Event to make the first POST block until both
    threads have started, then release.  Both threads race toward the lock;
    whichever wins performs the single HTTP call; the loser sees a valid cache
    and returns without a second call.
    """

    def test_concurrent_calls_produce_single_http_request(self):
        release_event = threading.Event()   # controls when the POST returns
        start_barrier = threading.Barrier(2)  # both threads start at the same time

        def slow_post(*args, **kwargs):
            # Block until the event is set (released from the main thread)
            release_event.wait(timeout=5)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.ok = True
            mock_resp.json.return_value = {
                "access_token": "shared_token",
                "expires_at": int(time.time()) + 3600,
                "token_type": "O-Bearer",
            }
            return mock_resp

        mock_session = MagicMock()
        mock_session.post.side_effect = slow_post

        client = _make_client(mock_session)

        results = []
        errors   = []

        def fetch():
            # Synchronise at the barrier so both threads enter get_access_token()
            # as close together as possible, then one wins the lock and blocks in
            # slow_post while the other waits on the lock.
            start_barrier.wait(timeout=5)
            try:
                results.append(client.get_access_token())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t1 = threading.Thread(target=fetch)
        t2 = threading.Thread(target=fetch)
        t1.start()
        t2.start()

        # Give both threads time to reach get_access_token() and for one to
        # enter the lock before we unblock the POST response.
        time.sleep(0.1)
        release_event.set()

        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, [], f"Unexpected errors in threads: {errors}")
        self.assertEqual(len(results), 2, "Both threads should have returned a token")
        self.assertTrue(all(r == "shared_token" for r in results))
        # The critical assertion: only ONE HTTP request despite two concurrent calls
        self.assertEqual(
            mock_session.post.call_count, 1,
            f"Expected 1 HTTP call; got {mock_session.post.call_count}",
        )


class TestFromConfigFactory(unittest.TestCase):
    """Additional coverage for the from_config() factory classmethod."""

    def test_from_config_selects_uat(self):
        mock_session, _ = _make_session()
        client = PhonePeOAuthClient.from_config(
            {
                "PHONEPE_CLIENT_ID":      "cfg-id",
                "PHONEPE_CLIENT_SECRET":  "cfg-secret",
                "PHONEPE_CLIENT_VERSION": "3",
                "PHONEPE_ENV":            "UAT",
            },
            http_session=mock_session,
        )
        self.assertEqual(client.env, "UAT")
        self.assertEqual(client.oauth_url, UAT_OAUTH_URL)

    def test_from_config_selects_production(self):
        mock_session, _ = _make_session()
        client = PhonePeOAuthClient.from_config(
            {
                "PHONEPE_CLIENT_ID":      "cfg-id",
                "PHONEPE_CLIENT_SECRET":  "cfg-secret",
                "PHONEPE_CLIENT_VERSION": "1",
                "PHONEPE_ENV":            "PRODUCTION",
            },
            http_session=mock_session,
        )
        self.assertEqual(client.env, "PRODUCTION")
        self.assertEqual(client.oauth_url, PROD_OAUTH_URL)


class TestAuthHeaders(unittest.TestCase):
    """auth_headers() must return the correct Authorization dict."""

    def test_auth_headers_format(self):
        mock_session, _ = _make_session(access_token="my_token_xyz")
        client = _make_client(mock_session)

        headers = client.auth_headers()

        self.assertIn("Authorization", headers)
        self.assertEqual(headers["Authorization"], "O-Bearer my_token_xyz")


class TestInvalidateCache(unittest.TestCase):
    """invalidate_cache() forces a fresh HTTP call on the next get_access_token()."""

    def test_invalidate_forces_refetch(self):
        mock_session, _ = _make_session(access_token="first_token")
        client = _make_client(mock_session)

        client.get_access_token()                     # Primes cache
        self.assertEqual(mock_session.post.call_count, 1)

        client.invalidate_cache()

        # Set up a second different response
        fresh = MagicMock()
        fresh.status_code = 200
        fresh.ok = True
        fresh.json.return_value = {
            "access_token": "second_token",
            "expires_at": int(time.time()) + 3600,
            "token_type": "O-Bearer",
        }
        mock_session.post.return_value = fresh


        token = client.get_access_token()
        self.assertEqual(token, "second_token")
        self.assertEqual(mock_session.post.call_count, 2)


# ── NEW: Strict environment validation tests (Fix 1) ──────────────────────────

class TestStrictEnvironmentValidation(unittest.TestCase):
    """Strict env validation: only 'UAT' and 'PRODUCTION' (case-insensitive)."""

    # -- Invalid values that must raise PhonePeOAuthConfigError ---------------

    def test_invalid_env_staging_raises(self):
        with self.assertRaises(PhonePeOAuthConfigError) as ctx:
            PhonePeOAuthClient(client_id="id", client_secret="sec", env="staging")
        self.assertIn("PHONEPE_ENV", str(ctx.exception))

    def test_invalid_env_sandbox_raises(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(client_id="id", client_secret="sec", env="sandbox")

    def test_invalid_env_empty_string_raises(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(client_id="id", client_secret="sec", env="")

    def test_invalid_env_dev_raises(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(client_id="id", client_secret="sec", env="dev")

    def test_invalid_env_test_raises(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(client_id="id", client_secret="sec", env="test")

    def test_invalid_env_is_config_error_not_generic(self):
        """Must be PhonePeOAuthConfigError (a subclass of PhonePeOAuthError)."""
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient(client_id="id", client_secret="sec", env="PROD")

    # -- Valid lowercase values must be accepted without error -----------------

    def test_lowercase_uat_accepted(self):
        mock_session, _ = _make_session()
        client = PhonePeOAuthClient(
            client_id="id", client_secret="sec", env="uat",
            http_session=mock_session,
        )
        self.assertEqual(client.env, "UAT")
        self.assertEqual(client.oauth_url, UAT_OAUTH_URL)

    def test_lowercase_production_accepted(self):
        mock_session, _ = _make_session()
        client = PhonePeOAuthClient(
            client_id="id", client_secret="sec", env="production",
            http_session=mock_session,
        )
        self.assertEqual(client.env, "PRODUCTION")
        self.assertEqual(client.oauth_url, PROD_OAUTH_URL)

    def test_mixed_case_uat_accepted(self):
        mock_session, _ = _make_session()
        client = PhonePeOAuthClient(
            client_id="id", client_secret="sec", env="Uat",
            http_session=mock_session,
        )
        self.assertEqual(client.env, "UAT")

    def test_mixed_case_production_accepted(self):
        mock_session, _ = _make_session()
        client = PhonePeOAuthClient(
            client_id="id", client_secret="sec", env="Production",
            http_session=mock_session,
        )
        self.assertEqual(client.env, "PRODUCTION")

    def test_from_config_invalid_env_raises(self):
        with self.assertRaises(PhonePeOAuthConfigError):
            PhonePeOAuthClient.from_config({
                "PHONEPE_CLIENT_ID":     "id",
                "PHONEPE_CLIENT_SECRET": "sec",
                "PHONEPE_ENV":           "sandbox",
            })


# ── NEW: Strict expires_at validation tests (Fix 2) ───────────────────────────

class TestStrictExpiresAtValidation(unittest.TestCase):
    """Strict expires_at: missing/null/non-numeric/zero/negative/past are all rejected."""

    def _response_with(self, expires_at_value, include_key=True):
        """Build a mock session whose POST returns a response with the given expires_at."""
        body = {"access_token": "tok", "token_type": "O-Bearer"}
        if include_key:
            body["expires_at"] = expires_at_value
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.json.return_value = body
        mock_session = MagicMock()
        mock_session.post.return_value = mock_response
        return mock_session

    # -- Missing / null --------------------------------------------------------

    def test_missing_expires_at_raises(self):
        mock_session = self._response_with(None, include_key=False)
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    def test_null_expires_at_raises(self):
        mock_session = self._response_with(None, include_key=True)
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    # -- Non-numeric -----------------------------------------------------------

    def test_string_nonnumeric_expires_at_raises(self):
        mock_session = self._response_with("never")
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    def test_empty_string_expires_at_raises(self):
        mock_session = self._response_with("")
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    def test_dict_expires_at_raises(self):
        mock_session = self._response_with({"ts": 9999999999})
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    # -- Zero / negative -------------------------------------------------------

    def test_zero_expires_at_raises(self):
        mock_session = self._response_with(0)
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    def test_negative_expires_at_raises(self):
        mock_session = self._response_with(-1000)
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    # -- Already expired -------------------------------------------------------

    def test_already_expired_timestamp_raises(self):
        past = int(time.time()) - 600   # 10 minutes ago
        mock_session = self._response_with(past)
        client = _make_client(mock_session)
        with self.assertRaises(PhonePeOAuthError):
            client.get_access_token()

    # -- Valid numeric types that MUST succeed ---------------------------------

    def test_integer_expires_at_accepted(self):
        future = int(time.time()) + 3600
        mock_session = self._response_with(future)
        client = _make_client(mock_session)
        token = client.get_access_token()
        self.assertEqual(token, "tok")

    def test_float_expires_at_accepted(self):
        future = time.time() + 3600.5
        mock_session = self._response_with(future)
        client = _make_client(mock_session)
        token = client.get_access_token()
        self.assertEqual(token, "tok")

    def test_numeric_string_expires_at_accepted(self):
        future = str(int(time.time()) + 3600)
        mock_session = self._response_with(future)
        client = _make_client(mock_session)
        token = client.get_access_token()
        self.assertEqual(token, "tok")

    def test_numeric_float_string_expires_at_accepted(self):
        future = str(time.time() + 3600.0)
        mock_session = self._response_with(future)
        client = _make_client(mock_session)
        token = client.get_access_token()
        self.assertEqual(token, "tok")


if __name__ == "__main__":
    unittest.main()

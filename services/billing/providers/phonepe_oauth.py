"""
PhonePe AutoPay V2 — OAuth 2.0 Client Credentials Token Manager
================================================================
Provides a thread-safe, auto-refreshing access-token cache for the
PhonePe Payment Gateway V2 API which uses O-Bearer authentication.

Usage
-----
    from services.billing.providers.phonepe_oauth import PhonePeOAuthClient

    client = PhonePeOAuthClient.from_config(app.config)
    token  = client.get_access_token()          # returns plain str, never logs it
    headers = client.auth_headers()             # {"Authorization": "O-Bearer <token>"}

Design decisions
----------------
* Token is cached in-process (class-level per client instance) and refreshed
  automatically when it is within EXPIRY_BUFFER_SECONDS of its expiry.
* A threading.Lock guards the refresh path so that only ONE thread performs the
  network call; all other threads block briefly then re-use the freshly cached
  token.  This is appropriate for a single-process gunicorn/Flask deployment
  with a thread pool or for APScheduler running in the same process.
* HTTP session is injectable for unit testing (pass a ``requests.Session``-like
  object).  In production, a private session with a 10 s connect / 15 s read
  timeout is used.
* Credentials are never logged.  On error the exception message references the
  config key name, not its value.
* ``expires_at`` in the PhonePe token response is documented as a Unix epoch
  timestamp (integer seconds).  If PhonePe ever changes this the guard branches
  below handle int/float/string inputs safely.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
UAT_OAUTH_URL  = "https://api-preprod.phonepe.com/apis/pg-sandbox/v1/oauth/token"
PROD_OAUTH_URL = "https://api.phonepe.com/apis/identity-manager/v1/oauth/token"

UAT_API_BASE   = "https://api-preprod.phonepe.com/apis/pg-sandbox"
PROD_API_BASE  = "https://api.phonepe.com/apis/pg"

# Refresh token this many seconds *before* the reported expiry to provide a
# safety margin for clock skew and slow network round-trips.
EXPIRY_BUFFER_SECONDS = 60


# ── Public dataclasses ─────────────────────────────────────────────────────────
@dataclass
class TokenResponse:
    """Parsed representation of a PhonePe OAuth token response."""
    access_token: str
    expires_at: float           # Unix epoch (seconds) when the token expires
    token_type: str = "O-Bearer"


@dataclass
class _CachedToken:
    """Internal cache entry (not part of the public API)."""
    token_response: TokenResponse
    fetched_at: float = field(default_factory=time.time)

    def is_valid(self, buffer_seconds: int = EXPIRY_BUFFER_SECONDS) -> bool:
        """Return True if the token can still be used after the safety margin."""
        return time.time() < (self.token_response.expires_at - buffer_seconds)


# ── Custom exceptions ──────────────────────────────────────────────────────────
class PhonePeOAuthError(RuntimeError):
    """Raised when an access token cannot be obtained."""


class PhonePeOAuthConfigError(PhonePeOAuthError):
    """Raised when required OAuth configuration is missing or invalid."""


# ── Main client ───────────────────────────────────────────────────────────────
class PhonePeOAuthClient:
    """
    Thread-safe, cached OAuth 2.0 Client-Credentials token manager for
    PhonePe Payment Gateway V2.

    Parameters
    ----------
    client_id:
        PhonePe dashboard Client ID (``PHONEPE_CLIENT_ID``).
    client_secret:
        PhonePe dashboard Client Secret (``PHONEPE_CLIENT_SECRET``).
    client_version:
        Integer version string from the dashboard (``PHONEPE_CLIENT_VERSION``).
        Defaults to ``"1"``.
    env:
        ``"UAT"`` (default) or ``"PRODUCTION"``.
    http_session:
        Optional injectable ``requests.Session``-compatible object.  Pass a
        mock here in unit tests to avoid real network calls.
    connect_timeout:
        TCP connection timeout in seconds.
    read_timeout:
        HTTP read timeout in seconds.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        client_version: str = "1",
        env: str = "UAT",
        http_session: Optional[Any] = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 15.0,
    ) -> None:
        if not client_id:
            raise PhonePeOAuthConfigError(
                "PHONEPE_CLIENT_ID is required but was not provided."
            )
        if not client_secret:
            raise PhonePeOAuthConfigError(
                "PHONEPE_CLIENT_SECRET is required but was not provided."
            )

        self._client_id      = client_id
        self._client_secret  = client_secret
        self._client_version = str(client_version or "1")
        self._connect_timeout = connect_timeout
        self._read_timeout    = read_timeout

        # Strict environment validation — only UAT or PRODUCTION are accepted.
        # Never silently fall back to UAT for an unrecognised value.
        _env_upper = env.upper() if isinstance(env, str) else ""
        if _env_upper not in {"UAT", "PRODUCTION"}:
            raise PhonePeOAuthConfigError(
                f"PHONEPE_ENV must be 'UAT' or 'PRODUCTION'; got {env!r}."
            )
        self.env = _env_upper

        # Endpoints selected by environment
        if self.env == "PRODUCTION":
            self.oauth_url = PROD_OAUTH_URL
            self.api_base  = PROD_API_BASE
        else:
            self.oauth_url = UAT_OAUTH_URL
            self.api_base  = UAT_API_BASE

        # Injected or private HTTP session
        if http_session is not None:
            self._session = http_session
            self._owns_session = False
        else:
            s = requests.Session()
            s.headers.update({"Accept": "application/json"})
            self._session = s
            self._owns_session = True

        # Thread-safety primitives
        self._lock:  threading.Lock       = threading.Lock()
        self._cache: Optional[_CachedToken] = None

    # ── Factory ───────────────────────────────────────────────────────────────
    @classmethod
    def from_config(cls, config: dict, **kwargs) -> "PhonePeOAuthClient":
        """
        Construct a client from a Flask ``app.config`` (or any mapping).

        Raises ``PhonePeOAuthConfigError`` if ``PHONEPE_CLIENT_ID`` or
        ``PHONEPE_CLIENT_SECRET`` are absent.
        """
        client_id     = config.get("PHONEPE_CLIENT_ID", "")
        client_secret = config.get("PHONEPE_CLIENT_SECRET", "")
        client_version = config.get("PHONEPE_CLIENT_VERSION", "1")
        env            = config.get("PHONEPE_ENV", "UAT")

        # Validate before constructing so the error message names the variable
        if not client_id:
            raise PhonePeOAuthConfigError(
                "PHONEPE_CLIENT_ID is missing from application configuration."
            )
        if not client_secret:
            raise PhonePeOAuthConfigError(
                "PHONEPE_CLIENT_SECRET is missing from application configuration."
            )

        return cls(
            client_id=client_id,
            client_secret=client_secret,
            client_version=client_version,
            env=env,
            **kwargs,
        )

    # ── Public API ────────────────────────────────────────────────────────────
    def get_access_token(self) -> str:
        """
        Return a valid OAuth access token string, refreshing if necessary.

        The result is suitable for direct inclusion in an ``Authorization``
        header as ``O-Bearer <token>``.  The raw token string is never written
        to any log.

        Raises
        ------
        PhonePeOAuthError
            If the token cannot be obtained due to invalid credentials, an HTTP
            error, a network timeout, or a malformed response.
        """
        # Fast path: read cache without acquiring the lock
        cached = self._cache
        if cached is not None and cached.is_valid():
            return cached.token_response.access_token

        # Slow path: acquire lock, re-check, then fetch
        with self._lock:
            # Double-checked locking — another thread may have refreshed while
            # we were waiting for the lock.
            if self._cache is not None and self._cache.is_valid():
                return self._cache.token_response.access_token

            token_response = self._fetch_token()
            self._cache = _CachedToken(token_response=token_response)
            logger.info(
                "PhonePe OAuth: obtained new access token (env=%s, expires_at=%.0f)",
                self.env,
                token_response.expires_at,
            )
            return token_response.access_token

    def auth_headers(self) -> dict:
        """
        Return a dict suitable for use as HTTP request headers for PhonePe V2
        API calls.

        Example::

            {"Authorization": "O-Bearer <access_token>"}

        The Authorization header value is built at call time and never stored
        in a variable that could be logged by this module.
        """
        return {"Authorization": f"O-Bearer {self.get_access_token()}"}

    def invalidate_cache(self) -> None:
        """
        Forcibly clear the cached token so the next call to ``get_access_token``
        will fetch a fresh one.  Useful after receiving a 401 from an API call.
        """
        with self._lock:
            self._cache = None
        logger.debug("PhonePe OAuth: token cache invalidated.")

    # ── Internal ──────────────────────────────────────────────────────────────
    def _fetch_token(self) -> TokenResponse:
        """
        Perform the Client Credentials grant request against the PhonePe OAuth
        endpoint.

        Uses ``application/x-www-form-urlencoded`` as required by PhonePe.
        Credentials are NEVER written to any log line in this method.

        Raises
        ------
        PhonePeOAuthError
            On credentials rejection, HTTP error, network timeout, or malformed
            response body.
        """
        form_data = {
            "client_id":      self._client_id,
            "client_version": self._client_version,
            "client_secret":  self._client_secret,
            "grant_type":     "client_credentials",
        }

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept":       "application/json",
        }

        logger.debug(
            "PhonePe OAuth: requesting access token from %s (env=%s)",
            self.oauth_url,
            self.env,
        )

        try:
            response = self._session.post(
                self.oauth_url,
                data=form_data,
                headers=headers,
                timeout=(self._connect_timeout, self._read_timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise PhonePeOAuthError(
                f"PhonePe OAuth: network timeout contacting {self.oauth_url}"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise PhonePeOAuthError(
                f"PhonePe OAuth: connection error contacting {self.oauth_url}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise PhonePeOAuthError(
                f"PhonePe OAuth: unexpected request error — {type(exc).__name__}"
            ) from exc

        # HTTP-level errors
        if response.status_code == 401:
            raise PhonePeOAuthError(
                "PhonePe OAuth: credentials rejected (HTTP 401). "
                "Check PHONEPE_CLIENT_ID and PHONEPE_CLIENT_SECRET."
            )
        if not response.ok:
            raise PhonePeOAuthError(
                f"PhonePe OAuth: HTTP {response.status_code} from token endpoint."
            )

        # Parse JSON body
        try:
            body: dict = response.json()
        except ValueError as exc:
            raise PhonePeOAuthError(
                "PhonePe OAuth: token response is not valid JSON."
            ) from exc

        return self._parse_token_body(body)

    @staticmethod
    def _parse_token_body(body: dict) -> TokenResponse:
        """
        Extract and validate fields from the OAuth JSON response.

        PhonePe documents ``expires_at`` as a Unix epoch integer (seconds).
        Integer, float and numeric-string inputs are accepted.

        Raises ``PhonePeOAuthError`` if:
        * ``access_token`` is absent or empty.
        * ``expires_at`` is missing, null, non-numeric, zero, negative, or
          already in the past.  No silent fallback is applied.
        """
        access_token = body.get("access_token", "")
        if not access_token:
            raise PhonePeOAuthError(
                "PhonePe OAuth: response did not contain an access_token field."
            )

        token_type = body.get("token_type", "O-Bearer")

        # Strictly parse and validate expires_at.
        # PhonePe documents this as a Unix epoch integer (seconds).
        # We also accept float and numeric-string inputs defensively.
        # Missing, null, non-numeric, zero, negative, or already-expired
        # values are all rejected — no silent 30-minute fallback.
        raw_expires = body.get("expires_at")
        if raw_expires is None:
            raise PhonePeOAuthError(
                "PhonePe OAuth: 'expires_at' is missing from the token response. "
                "The token cannot be cached safely without a known expiry time."
            )

        try:
            expires_at = float(raw_expires)
        except (TypeError, ValueError) as exc:
            raise PhonePeOAuthError(
                f"PhonePe OAuth: 'expires_at' value could not be parsed as a "
                f"number (got type {type(raw_expires).__name__!r}, value {raw_expires!r})."
            ) from exc

        if expires_at <= 0:
            raise PhonePeOAuthError(
                f"PhonePe OAuth: 'expires_at' is zero or negative ({expires_at!r}), "
                "which is not a valid Unix epoch timestamp."
            )

        if expires_at <= time.time():
            raise PhonePeOAuthError(
                f"PhonePe OAuth: 'expires_at' ({expires_at!r}) is already in the past. "
                "The token is expired and cannot be used."
            )

        return TokenResponse(
            access_token=access_token,
            expires_at=expires_at,
            token_type=token_type,
        )

    def __repr__(self) -> str:
        # Never include client_id or secret in repr
        return (
            f"<PhonePeOAuthClient env={self.env!r} "
            f"oauth_url={self.oauth_url!r}>"
        )


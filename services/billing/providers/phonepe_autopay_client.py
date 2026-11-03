"""
PhonePe AutoPay V2 API Client
==============================
Implements the Standard Checkout AutoPay V2 HTTP API for GlassEntials CRM.

This module provides ``PhonePeAutoPayClient`` — a thin, transport-level HTTP
client that wraps the six PhonePe AutoPay V2 API endpoints.  It is NOT a
lifecycle manager: it does not orchestrate subscription states, schedule
renewals, write database records, or enforce business rules.  All of that
belongs in the caller (services that use this client).

Initialisation
--------------
The client reuses the Phase 1 OAuth client (``PhonePeOAuthClient``) for
authentication.  Construct it directly or via the ``from_oauth_client``
factory::

    from services.billing.providers.phonepe_oauth import PhonePeOAuthClient
    from services.billing.providers.phonepe_autopay_client import PhonePeAutoPayClient

    oauth = PhonePeOAuthClient(
        client_id=app.config["PHONEPE_CLIENT_ID"],
        client_secret=app.config["PHONEPE_CLIENT_SECRET"],
        env=app.config.get("PHONEPE_ENV", "UAT"),
    )
    client = PhonePeAutoPayClient.from_oauth_client(oauth)

    # Optional: partner merchant header
    client = PhonePeAutoPayClient.from_oauth_client(oauth, merchant_id="MY_MID")

Available methods
-----------------
* ``setup_subscription(payload)``        — POST /subscriptions/v2/setup
* ``get_subscription_status(id, ...)``   — GET  /subscriptions/v2/{id}/status
* ``notify_redemption(payload)``         — POST /subscriptions/v2/notify
* ``get_order_status(id, ...)``          — GET  /checkout/v2/order/{id}/status
* ``redeem(payload)``                    — POST /subscriptions/v2/redeem
* ``cancel_subscription(id)``           — POST /checkout/v2/subscriptions/{id}/cancel

Required environment variables
-------------------------------
* ``PHONEPE_CLIENT_ID``      — PhonePe dashboard OAuth Client ID
* ``PHONEPE_CLIENT_SECRET``  — PhonePe dashboard OAuth Client Secret
* ``PHONEPE_CLIENT_VERSION`` — Credential version (default: "1")
* ``PHONEPE_ENV``            — "UAT" or "PRODUCTION"
* ``PHONEPE_MERCHANT_ID``    — (optional) Partner merchant ID for X-MERCHANT-ID
                               header.  Distinct from OAuth Client ID.

Error handling
--------------
All errors surface as subclasses of ``PhonePeAPIError``:

* ``PhonePeAuthenticationError`` — HTTP 401/403 (after one token refresh retry)
* ``PhonePeNetworkError``        — Timeout, connection failure
* ``PhonePeResponseError``       — HTTP 4xx/5xx, malformed JSON, unexpected body

HTTP 204 No Content is handled without JSON parsing (cancel_subscription).

Authentication errors (HTTP 401) trigger a single token-cache invalidation
and one automatic retry.  A second 401 raises ``PhonePeAuthenticationError``.
No other automatic retries are performed; ambiguous POST failures must be
reconciled by the caller using the appropriate status endpoint.

Running mocked tests
--------------------
    pytest test_phonepe_autopay_client.py -o cache_dir=tmp_pytest_cache -v
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import quote, urlencode

import requests

from services.billing.providers.phonepe_oauth import PhonePeOAuthClient, PhonePeOAuthError

logger = logging.getLogger(__name__)

# ── API path constants (V2 Standard Checkout AutoPay) ─────────────────────────
_PATH_SETUP          = "/subscriptions/v2/setup"
_PATH_SUB_STATUS     = "/subscriptions/v2/{merchant_subscription_id}/status"
_PATH_NOTIFY         = "/subscriptions/v2/notify"
_PATH_ORDER_STATUS   = "/checkout/v2/order/{merchant_order_id}/status"
_PATH_REDEEM         = "/subscriptions/v2/redeem"
_PATH_CANCEL         = "/checkout/v2/subscriptions/{merchant_subscription_id}/cancel"

# Default HTTP timeouts (seconds)
_DEFAULT_CONNECT_TIMEOUT = 10.0
_DEFAULT_READ_TIMEOUT    = 20.0


# ── Structured exceptions ─────────────────────────────────────────────────────

class PhonePeAPIError(RuntimeError):
    """Base class for all PhonePe AutoPay V2 client errors."""

    def __init__(self, message: str, status_code: Optional[int] = None,
                 error_code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code  = error_code

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} status_code={self.status_code!r} "
            f"error_code={self.error_code!r} message={str(self)!r}>"
        )


class PhonePeAuthenticationError(PhonePeAPIError):
    """Raised on HTTP 401 after one token refresh retry, or on HTTP 403."""


class PhonePeNetworkError(PhonePeAPIError):
    """Raised on connection timeout or other transport-level failure."""


class PhonePeResponseError(PhonePeAPIError):
    """Raised on HTTP 4xx/5xx (other than 401/403), malformed JSON, etc."""


# ── Main client ───────────────────────────────────────────────────────────────

class PhonePeAutoPayClient:
    """
    Transport-level HTTP client for the PhonePe AutoPay V2 Standard Checkout API.

    Parameters
    ----------
    oauth_client:
        A configured ``PhonePeOAuthClient`` instance (Phase 1).
        Provides ``auth_headers()`` and ``api_base`` used for all requests.
    merchant_id:
        Optional partner Merchant ID sent as ``X-MERCHANT-ID``.
        Must NOT be the same value as the OAuth Client ID.
        Pass ``None`` (default) to omit the header entirely.
    http_session:
        Injectable ``requests.Session``-compatible object for unit testing.
        If ``None``, a private session is created.
    connect_timeout:
        TCP connection timeout in seconds.
    read_timeout:
        HTTP read timeout in seconds.
    """

    def __init__(
        self,
        oauth_client: PhonePeOAuthClient,
        merchant_id: Optional[str] = None,
        http_session: Optional[Any] = None,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT,
        read_timeout:    float = _DEFAULT_READ_TIMEOUT,
    ) -> None:
        self._oauth          = oauth_client
        self._merchant_id    = merchant_id or None   # never store empty string
        self._connect_timeout = connect_timeout
        self._read_timeout    = read_timeout
        self._api_base        = oauth_client.api_base.rstrip("/")

        if http_session is not None:
            self._session      = http_session
            self._owns_session = False
        else:
            s = requests.Session()
            s.headers.update({"Accept": "application/json"})
            self._session      = s
            self._owns_session = True

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_oauth_client(
        cls,
        oauth_client: PhonePeOAuthClient,
        merchant_id: Optional[str] = None,
        **kwargs,
    ) -> "PhonePeAutoPayClient":
        """
        Preferred factory: build the API client from an existing OAuth client.

        ``merchant_id`` is the optional partner Merchant ID (``X-MERCHANT-ID``).
        It is separate from the OAuth Client ID stored in ``oauth_client``.
        """
        return cls(oauth_client=oauth_client, merchant_id=merchant_id, **kwargs)

    # ── Public API methods ────────────────────────────────────────────────────

    def setup_subscription(self, payload: dict) -> dict:
        """
        Set up a new AutoPay subscription mandate.

        POST /subscriptions/v2/setup

        Parameters
        ----------
        payload:
            Complete PhonePe subscription setup request body as documented.
            Must include at minimum ``merchantSubscriptionId``, ``amount``,
            ``paymentFlow``, and customer details.

        Returns
        -------
        dict
            Raw PhonePe response including redirect information.

        Notes
        -----
        The caller must redirect the customer to the URL returned in the
        response.  Do NOT mark the subscription ACTIVE based on this response.
        Verify via ``get_subscription_status`` after the customer returns.
        """
        if not isinstance(payload, dict) or not payload:
            raise ValueError("setup_subscription: payload must be a non-empty dict.")
        return self._request("POST", _PATH_SETUP, json_body=payload)

    def get_subscription_status(
        self,
        merchant_subscription_id: str,
        details: bool = True,
    ) -> dict:
        """
        Query the current status of an AutoPay subscription mandate.

        GET /subscriptions/v2/{merchantSubscriptionId}/status

        Parameters
        ----------
        merchant_subscription_id:
            The merchant-generated subscription identifier.
        details:
            Whether to request detailed status data (default: True).

        Returns
        -------
        dict
            Raw PhonePe response including ``state`` and ``subscriptionId``.
        """
        if not merchant_subscription_id or not isinstance(merchant_subscription_id, str):
            raise ValueError(
                "get_subscription_status: merchant_subscription_id must be a non-empty string."
            )
        path   = _PATH_SUB_STATUS.format(
            merchant_subscription_id=quote(merchant_subscription_id, safe="")
        )
        params = {"details": "true" if details else "false"}
        return self._request("GET", path, params=params)

    def notify_redemption(self, payload: dict) -> dict:
        """
        Send a Pre-Debit Notification (PDN) for an upcoming recurring debit.

        POST /subscriptions/v2/notify

        Parameters
        ----------
        payload:
            Complete notification request body.  Must contain
            ``merchantOrderId``, ``merchantSubscriptionId``, ``amount``
            and ``paymentFlow`` at minimum.

        Returns
        -------
        dict
            Raw PhonePe response.

        Notes
        -----
        A successful HTTP response does NOT confirm the notification was
        accepted by the UPI system.  The caller must verify using
        ``get_order_status`` with the same ``merchantOrderId``.

        Do NOT execute redemption immediately after this call.
        """
        if not isinstance(payload, dict) or not payload:
            raise ValueError("notify_redemption: payload must be a non-empty dict.")
        return self._request("POST", _PATH_NOTIFY, json_body=payload)

    def get_order_status(
        self,
        merchant_order_id: str,
        details: bool = True,
    ) -> dict:
        """
        Query the status of a setup or notification order.

        GET /checkout/v2/order/{merchantOrderId}/status

        Parameters
        ----------
        merchant_order_id:
            The merchant-generated order/transaction identifier (NOT PhonePe's
            internal ``orderId``).
        details:
            Whether to request full payment details (default: True).

        Returns
        -------
        dict
            Raw PhonePe response including ``paymentFlow``, ``state`` and
            ``paymentDetails``.
        """
        if not merchant_order_id or not isinstance(merchant_order_id, str):
            raise ValueError(
                "get_order_status: merchant_order_id must be a non-empty string."
            )
        path   = _PATH_ORDER_STATUS.format(
            merchant_order_id=quote(merchant_order_id, safe="")
        )
        params = {"details": "true" if details else "false"}
        return self._request("GET", path, params=params)

    def redeem(self, payload: dict) -> dict:
        """
        Execute a recurring debit redemption against an active AutoPay mandate.

        POST /subscriptions/v2/redeem

        Parameters
        ----------
        payload:
            Complete redemption request body.  The ``merchantOrderId`` must
            match the one used in the preceding ``notify_redemption`` call.

        Returns
        -------
        dict
            Raw PhonePe response.  ``state`` may be ``PENDING``, ``COMPLETED``
            or ``FAILED``.

        Notes
        -----
        * PENDING does NOT mean success.  The caller must poll
          ``get_order_status`` to confirm the final state.
        * Do NOT retry this call after a network error or ambiguous response.
          Always check ``get_order_status`` for the original ``merchantOrderId``
          before any retry decision.
        * The caller is responsible for verifying that the subscription is
          ACTIVE and the notification was accepted before calling this method.
        * When ``autoDebit`` is ``true`` on the mandate, PhonePe handles the
          debit automatically; this method must NOT be called.
        """
        if not isinstance(payload, dict) or not payload:
            raise ValueError("redeem: payload must be a non-empty dict.")
        return self._request("POST", _PATH_REDEEM, json_body=payload)

    def cancel_subscription(self, merchant_subscription_id: str) -> dict:
        """
        Cancel an active AutoPay subscription mandate.

        POST /checkout/v2/subscriptions/{merchantSubscriptionId}/cancel

        Parameters
        ----------
        merchant_subscription_id:
            The merchant-generated subscription identifier.

        Returns
        -------
        dict
            Raw PhonePe response, or an empty dict for HTTP 204 No Content.

        Notes
        -----
        A 204 response indicates PhonePe accepted the cancellation request.
        It does NOT guarantee the final subscription state.  The caller should
        verify via ``get_subscription_status`` after this call.
        """
        if not merchant_subscription_id or not isinstance(merchant_subscription_id, str):
            raise ValueError(
                "cancel_subscription: merchant_subscription_id must be a non-empty string."
            )
        path = _PATH_CANCEL.format(
            merchant_subscription_id=quote(merchant_subscription_id, safe="")
        )
        return self._request("POST", path)

    # ── Internal request engine ───────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict] = None,
        params: Optional[dict]   = None,
        *,
        _is_retry: bool = False,
    ) -> dict:
        """
        Shared internal HTTP request dispatcher.

        Responsibilities
        ----------------
        * Build the full URL from the configured API base.
        * Attach ``Authorization: O-Bearer <token>`` from the OAuth client.
        * Attach optional ``X-MERCHANT-ID`` header.
        * Serialize JSON request body.
        * Apply connection and read timeouts.
        * Handle HTTP 401 with a single token-cache invalidation and retry.
        * Parse JSON responses safely.
        * Handle HTTP 204 (No Content) without JSON parsing.
        * Raise structured exceptions for all error conditions.

        The ``Authorization`` header value is NEVER logged.

        Parameters
        ----------
        method:
            HTTP verb ("GET", "POST").
        path:
            API path, e.g. "/subscriptions/v2/setup".
        json_body:
            Optional request body dict (serialised to JSON).
        params:
            Optional URL query parameters.
        _is_retry:
            Internal flag — True when this is the one-time 401 retry.
            Prevents infinite retry loops.
        """
        url = f"{self._api_base}{path}"

        # Build authentication header — never log the value
        try:
            auth_hdr = self._oauth.auth_headers()
        except PhonePeOAuthError as exc:
            raise PhonePeAuthenticationError(
                f"PhonePe AutoPay: failed to obtain OAuth token — {exc}"
            ) from exc

        headers: Dict[str, str] = {
            "Accept":        "application/json",
            "Content-Type":  "application/json",
        }
        headers.update(auth_hdr)   # adds Authorization: O-Bearer …

        # Optional partner merchant header — separate from OAuth Client ID
        if self._merchant_id:
            headers["X-MERCHANT-ID"] = self._merchant_id

        logger.debug(
            "PhonePe AutoPay: %s %s (retry=%s)",
            method, path, _is_retry,
        )

        # Execute request
        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=headers,
                timeout=(self._connect_timeout, self._read_timeout),
            )
        except requests.exceptions.Timeout as exc:
            raise PhonePeNetworkError(
                f"PhonePe AutoPay: request timed out — {method} {path}"
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise PhonePeNetworkError(
                f"PhonePe AutoPay: connection error — {method} {path}"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise PhonePeNetworkError(
                f"PhonePe AutoPay: transport error ({type(exc).__name__}) — {method} {path}"
            ) from exc

        # ── HTTP 204 No Content (cancel returns this) ──────────────────────
        if response.status_code == 204:
            logger.info("PhonePe AutoPay: %s %s → 204 No Content", method, path)
            return {}

        # ── HTTP 401 — invalidate token cache and retry once ───────────────
        if response.status_code == 401:
            if not _is_retry:
                logger.warning(
                    "PhonePe AutoPay: HTTP 401 on %s %s — invalidating token cache and retrying.",
                    method, path,
                )
                self._oauth.invalidate_cache()
                return self._request(
                    method, path,
                    json_body=json_body, params=params,
                    _is_retry=True,
                )
            # Second 401 — credentials are genuinely rejected
            raise PhonePeAuthenticationError(
                f"PhonePe AutoPay: HTTP 401 Unauthorized after token refresh — {method} {path}.",
                status_code=401,
            )

        # ── HTTP 403 ───────────────────────────────────────────────────────
        if response.status_code == 403:
            raise PhonePeAuthenticationError(
                f"PhonePe AutoPay: HTTP 403 Forbidden — {method} {path}.",
                status_code=403,
            )

        # ── Other HTTP errors (400, 404, 429, 5xx) ─────────────────────────
        if not response.ok:
            error_code = None
            error_msg  = f"HTTP {response.status_code}"
            try:
                err_body   = response.json()
                error_code = (
                    err_body.get("code")
                    or err_body.get("errorCode")
                    or err_body.get("error")
                )
                error_msg  = (
                    err_body.get("message")
                    or err_body.get("error_description")
                    or error_msg
                )
            except ValueError:
                pass   # non-JSON body; keep the generic HTTP status message

            logger.warning(
                "PhonePe AutoPay: %s %s → HTTP %s (code=%s)",
                method, path, response.status_code, error_code,
            )
            raise PhonePeResponseError(
                f"PhonePe AutoPay: {method} {path} failed — {error_msg}",
                status_code=response.status_code,
                error_code=error_code,
            )

        # ── Parse success body ─────────────────────────────────────────────
        # Some 2xx responses may have an empty body (not 204) — handle safely.
        if not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise PhonePeResponseError(
                f"PhonePe AutoPay: {method} {path} returned non-JSON body "
                f"(HTTP {response.status_code}).",
                status_code=response.status_code,
            ) from exc

    def __repr__(self) -> str:
        return (
            f"<PhonePeAutoPayClient env={self._oauth.env!r} "
            f"api_base={self._api_base!r} "
            f"merchant_id={self._merchant_id!r}>"
        )


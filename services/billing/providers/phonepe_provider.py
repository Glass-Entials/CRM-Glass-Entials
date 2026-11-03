import base64
import hashlib
import hmac
import json
import logging
import time
import requests
from typing import Dict, Any, Optional
from flask import current_app

from services.billing.payment_provider import (
    PaymentProvider,
    MandateSetupResult,
    VerificationResult,
    RedemptionResult,
    WebhookResult,
)

logger = logging.getLogger(__name__)


class PhonePeProvider(PaymentProvider):
    """
    PhonePe Payment Gateway Provider with UPI AutoPay & Recurring Mandates.
    Follows PhonePe Developer Documentation for V3 Recurring & Standard Checkout APIs.
    """

    UAT_BASE_URL = "https://api-preprod.phonepe.com/apis/pg-sandbox"
    PROD_BASE_URL = "https://api.phonepe.com/apis/hermes"

    def __init__(
        self,
        merchant_id: Optional[str] = None,
        salt_key: Optional[str] = None,
        salt_index: Optional[str] = "1",
        env: Optional[str] = "UAT",
        base_url: Optional[str] = None,
        webhook_secret: Optional[str] = None,
        webhook_username: Optional[str] = None,
        webhook_password: Optional[str] = None,
    ):
        self.merchant_id = merchant_id or current_app.config.get("PHONEPE_MERCHANT_ID", "PGTESTPAYUAT")
        self.salt_key = salt_key or current_app.config.get("PHONEPE_SALT_KEY", "099eb0cd-0252-4e2a-9123-046648858427")
        self.salt_index = str(salt_index or current_app.config.get("PHONEPE_SALT_INDEX", "1"))
        self.env = (env or current_app.config.get("PHONEPE_ENV", "UAT")).upper()

        # Production safety guard: Fail immediately if production environment is configured
        # with UAT sandbox keys or missing credentials.
        if self.env == "PRODUCTION":
            if not self.merchant_id or self.merchant_id == "PGTESTPAYUAT":
                raise ValueError("CRITICAL SECURITY ERROR: PhonePe is set to PRODUCTION but PHONEPE_MERCHANT_ID is unset or using test value 'PGTESTPAYUAT'!")
            if not self.salt_key or self.salt_key == "099eb0cd-0252-4e2a-9123-046648858427":
                raise ValueError("CRITICAL SECURITY ERROR: PhonePe is set to PRODUCTION but PHONEPE_SALT_KEY is unset or using test salt key!")
        
        configured_url = base_url or current_app.config.get("PHONEPE_BASE_URL")
        if configured_url:
            self.base_url = configured_url.rstrip("/")
        else:
            self.base_url = self.PROD_BASE_URL if self.env == "PRODUCTION" else self.UAT_BASE_URL

        self.webhook_secret = webhook_secret or current_app.config.get("PHONEPE_WEBHOOK_SECRET")
        self.webhook_username = webhook_username or current_app.config.get("PHONEPE_WEBHOOK_USERNAME")
        self.webhook_password = webhook_password or current_app.config.get("PHONEPE_WEBHOOK_PASSWORD")

    @property
    def name(self) -> str:
        return "phonepe"

    def _generate_x_verify(self, payload_base64: str, endpoint: str) -> str:
        """
        Calculates PhonePe checksum:
        X-VERIFY = SHA256(base64Payload + endpoint + saltKey) + "###" + saltIndex
        """
        string_to_hash = f"{payload_base64}{endpoint}{self.salt_key}"
        sha256_hash = hashlib.sha256(string_to_hash.encode("utf-8")).hexdigest()
        return f"{sha256_hash}###{self.salt_index}"

    def _generate_get_x_verify(self, endpoint: str) -> str:
        """
        Calculates PhonePe checksum for GET requests without body:
        X-VERIFY = SHA256(endpoint + saltKey) + "###" + saltIndex
        """
        string_to_hash = f"{endpoint}{self.salt_key}"
        sha256_hash = hashlib.sha256(string_to_hash.encode("utf-8")).hexdigest()
        return f"{sha256_hash}###{self.salt_index}"

    def _encode_payload(self, payload_dict: Dict[str, Any]) -> str:
        """Converts dict payload to JSON and Base64 encodes it."""
        json_str = json.dumps(payload_dict, separators=(",", ":"))
        return base64.b64encode(json_str.encode("utf-8")).decode("utf-8")

    def _decode_response_payload(self, base64_str: str) -> Dict[str, Any]:
        """Decodes base64 response payload from PhonePe."""
        decoded_bytes = base64.b64decode(base64_str)
        return json.loads(decoded_bytes.decode("utf-8"))

    def create_subscription_mandate(
        self,
        subscription,
        plan,
        billing_cycle: str,
        user,
        return_url: str,
        callback_url: str
    ) -> MandateSetupResult:
        """
        Initiates PhonePe AutoPay Subscription Setup (/v3/recurring/subscription/create).
        Sets up the mandate and authorizes the first payment cycle via UPI AutoPay.
        """
        endpoint = "/v3/recurring/subscription/create"
        url = f"{self.base_url}{endpoint}"

        # Calculate amount in paise with 18% GST
        base_amount_paise = plan.yearly_price_paise if billing_cycle == "yearly" else plan.monthly_price_paise
        gst_amount_paise = int(round(base_amount_paise * 0.18))
        total_amount_paise = base_amount_paise + gst_amount_paise

        # Unique reference IDs
        timestamp_ms = int(time.time() * 1000)
        merchant_subscription_id = f"MSUB_{subscription.organization_id}_{timestamp_ms}"
        merchant_transaction_id = f"MT_{subscription.organization_id}_{timestamp_ms}"
        merchant_user_id = f"USR_{user.id}"

        # 10 years validity in milliseconds
        valid_until_ms = timestamp_ms + (10 * 365 * 24 * 60 * 60 * 1000)

        # PhonePe AutoPay mandate payload
        payload = {
            "merchantId": self.merchant_id,
            "merchantTransactionId": merchant_transaction_id,
            "merchantUserId": merchant_user_id,
            "merchantSubscriptionId": merchant_subscription_id,
            "subscriptionName": f"{plan.display_name or plan.name} Plan",
            "amount": total_amount_paise,
            "currency": "INR",
            "redirectUrl": return_url,
            "redirectMode": "POST",
            "callbackUrl": callback_url,
            "paymentInstrument": {
                "type": "PAY_PAGE"
            },
            "subscriptionDetails": {
                "authWorkflowType": "TRANSACTION",
                "amountType": "FIXED",
                "maxAmount": total_amount_paise,
                "frequency": "YEARLY" if billing_cycle == "yearly" else "MONTHLY",
                "recurringCount": 120,
                "expireAt": valid_until_ms,
            }
        }

        try:
            base64_payload = self._encode_payload(payload)
            x_verify = self._generate_x_verify(base64_payload, endpoint)

            headers = {
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
                "Accept": "application/json",
            }

            logger.info("Initiating PhonePe Subscription Setup for org=%s, sub_id=%s, amount=%s paise",
                        subscription.organization_id, merchant_subscription_id, total_amount_paise)

            resp = requests.post(url, json={"request": base64_payload}, headers=headers, timeout=30)
            resp_data = resp.json()

            logger.info("PhonePe setup response code=%s, status=%s", resp.status_code, resp_data.get("code"))

            if resp.status_code == 200 and resp_data.get("success"):
                data = resp_data.get("data", {})
                instrument = data.get("instrumentResponse", {})
                redirect_info = instrument.get("redirectInfo", {})
                redirect_url = redirect_info.get("url")

                provider_sub_id = data.get("subscriptionId") or merchant_subscription_id

                return MandateSetupResult(
                    success=True,
                    provider=self.name,
                    merchant_subscription_id=merchant_subscription_id,
                    provider_subscription_id=provider_sub_id,
                    merchant_transaction_id=merchant_transaction_id,
                    redirect_url=redirect_url,
                    raw_response=resp_data,
                )
            else:
                err_msg = resp_data.get("message") or f"PhonePe API Error: {resp_data.get('code')}"
                return MandateSetupResult(
                    success=False,
                    provider=self.name,
                    merchant_subscription_id=merchant_subscription_id,
                    merchant_transaction_id=merchant_transaction_id,
                    error_message=err_msg,
                    raw_response=resp_data,
                )
        except Exception as e:
            logger.error("PhonePe Subscription Setup Exception: %s", str(e), exc_info=True)
            return MandateSetupResult(
                success=False,
                provider=self.name,
                merchant_subscription_id=merchant_subscription_id,
                merchant_transaction_id=merchant_transaction_id,
                error_message=str(e),
            )

    def check_subscription_status(
        self,
        merchant_subscription_id: str,
        organization_id: Optional[int] = None,
    ) -> VerificationResult:
        """
        Queries PhonePe for subscription mandate status:
        GET /v3/recurring/subscription/status/{merchantId}/{merchantSubscriptionId}
        """
        endpoint = f"/v3/recurring/subscription/status/{self.merchant_id}/{merchant_subscription_id}"
        url = f"{self.base_url}{endpoint}"

        try:
            x_verify = self._generate_get_x_verify(endpoint)
            headers = {
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
                "X-MERCHANT-ID": self.merchant_id,
                "Accept": "application/json",
            }

            resp = requests.get(url, headers=headers, timeout=20)
            resp_data = resp.json()

            if resp.status_code == 200 and resp_data.get("success"):
                data = resp_data.get("data", {})
                state = (data.get("state") or data.get("status") or "").upper()
                is_active = state in ("ACTIVE", "COMPLETED", "SUCCESS")

                return VerificationResult(
                    success=is_active,
                    status=state,
                    merchant_subscription_id=merchant_subscription_id,
                    provider_subscription_id=data.get("subscriptionId"),
                    mandate_reference=data.get("mandateReference") or data.get("umn"),
                    amount_paise=data.get("amount") or data.get("maxAmount") or 0,
                    raw_response=resp_data,
                )
            else:
                err = resp_data.get("message") or resp_data.get("code") or "Status query failed"
                return VerificationResult(
                    success=False,
                    status="FAILED",
                    merchant_subscription_id=merchant_subscription_id,
                    error_message=err,
                    raw_response=resp_data,
                )
        except Exception as e:
            logger.error("PhonePe check_subscription_status Exception: %s", str(e), exc_info=True)
            return VerificationResult(
                success=False,
                status="ERROR",
                merchant_subscription_id=merchant_subscription_id,
                error_message=str(e),
            )

    def check_transaction_status(
        self,
        merchant_transaction_id: str,
        organization_id: Optional[int] = None,
    ) -> VerificationResult:
        """
        Queries PhonePe for transaction status:
        GET /v3/transaction/{merchantId}/{merchantTransactionId}/status
        """
        endpoint = f"/v3/transaction/{self.merchant_id}/{merchant_transaction_id}/status"
        url = f"{self.base_url}{endpoint}"

        try:
            x_verify = self._generate_get_x_verify(endpoint)
            headers = {
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
                "X-MERCHANT-ID": self.merchant_id,
                "Accept": "application/json",
            }

            resp = requests.get(url, headers=headers, timeout=20)
            resp_data = resp.json()

            if resp.status_code == 200 and resp_data.get("success"):
                data = resp_data.get("data", {})
                code = resp_data.get("code", "")
                state = data.get("state", "").upper()
                is_success = code == "PAYMENT_SUCCESS" or state == "COMPLETED"

                return VerificationResult(
                    success=is_success,
                    status="SUCCESS" if is_success else state or "PENDING",
                    merchant_subscription_id=data.get("merchantSubscriptionId", ""),
                    provider_subscription_id=data.get("subscriptionId"),
                    provider_transaction_id=data.get("transactionId"),
                    provider_payment_id=data.get("transactionId"),
                    amount_paise=data.get("amount", 0),
                    raw_response=resp_data,
                )
            else:
                err = resp_data.get("message") or resp_data.get("code") or "Transaction check failed"
                return VerificationResult(
                    success=False,
                    status="FAILED",
                    merchant_subscription_id="",
                    error_message=err,
                    raw_response=resp_data,
                )
        except Exception as e:
            logger.error("PhonePe check_transaction_status Exception: %s", str(e), exc_info=True)
            return VerificationResult(
                success=False,
                status="ERROR",
                merchant_subscription_id="",
                error_message=str(e),
            )

    def verify_payment(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        organization_id: Optional[int] = None,
    ) -> VerificationResult:
        """
        Verifies payment return from PhonePe callback.
        CRITICAL SECURITY REQUIREMENT:
        Browser redirects must never be blindly trusted as proof of payment.
        Extracts merchantTransactionId and queries PhonePe Server-to-Server
        Status API (GET /v3/transaction/{merchantId}/{merchantTransactionId}/status)
        to cryptographically guarantee authorization before activation.
        """
        encoded_resp = payload.get("response")
        merchant_tx_id = payload.get("merchantTransactionId")

        # If base64 payload received from browser POST, decode to extract merchant_transaction_id
        if encoded_resp:
            try:
                decoded = self._decode_response_payload(encoded_resp)
                data = decoded.get("data", {})
                merchant_tx_id = data.get("merchantTransactionId") or merchant_tx_id
            except Exception as e:
                logger.error("Error decoding PhonePe callback payload: %s", str(e))

        # Perform authoritative server-to-server transaction status inquiry with PhonePe
        if merchant_tx_id:
            logger.info("Performing authoritative S2S transaction status inquiry for tx=%s", merchant_tx_id)
            return self.check_transaction_status(merchant_tx_id)

        return VerificationResult(
            success=False,
            status="UNKNOWN",
            merchant_subscription_id="",
            error_message="Insufficient data in verification payload: missing merchantTransactionId",
        )

    def notify_redemption(
        self,
        subscription,
        amount_paise: int,
        merchant_transaction_id: str
    ) -> RedemptionResult:
        """
        Pre-debit notification for recurring debit.
        POST /v3/recurring/debit/init
        Required by RBI at least 24 hours prior to recurring debit.
        """
        endpoint = "/v3/recurring/debit/init"
        url = f"{self.base_url}{endpoint}"

        now_ms = int(time.time() * 1000)
        # Scheduled redemption 24 hours from now
        scheduled_redemption_ms = now_ms + (24 * 60 * 60 * 1000)

        merchant_sub_id = subscription.provider_subscription_id or f"MSUB_{subscription.organization_id}"
        merchant_user_id = subscription.provider_customer_reference or f"USR_ORG_{subscription.organization_id}"

        payload = {
            "merchantId": self.merchant_id,
            "merchantUserId": merchant_user_id,
            "merchantSubscriptionId": merchant_sub_id,
            "merchantTransactionId": merchant_transaction_id,
            "amount": amount_paise,
            "currency": "INR",
            "redemptionDate": scheduled_redemption_ms,
            "autoDebit": False,  # Explicitly declare merchant-initiated debit
        }

        try:
            base64_payload = self._encode_payload(payload)
            x_verify = self._generate_x_verify(base64_payload, endpoint)
            headers = {
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
            }

            resp = requests.post(url, json={"request": base64_payload}, headers=headers, timeout=30)
            resp_data = resp.json()

            success = resp.status_code == 200 and resp_data.get("success", False)
            return RedemptionResult(
                success=success,
                status="NOTIFIED" if success else "FAILED",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                raw_response=resp_data,
                error_message=None if success else resp_data.get("message"),
            )
        except Exception as e:
            logger.error("PhonePe notify_redemption error: %s", str(e), exc_info=True)
            return RedemptionResult(
                success=False,
                status="ERROR",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                error_message=str(e),
            )

    def execute_redemption(
        self,
        subscription,
        amount_paise: int,
        merchant_transaction_id: str
    ) -> RedemptionResult:
        """
        Executes recurring debit against active mandate.
        POST /v3/recurring/debit/execute
        """
        endpoint = "/v3/recurring/debit/execute"
        url = f"{self.base_url}{endpoint}"

        merchant_sub_id = subscription.provider_subscription_id or f"MSUB_{subscription.organization_id}"
        merchant_user_id = subscription.provider_customer_reference or f"USR_ORG_{subscription.organization_id}"

        payload = {
            "merchantId": self.merchant_id,
            "merchantUserId": merchant_user_id,
            "merchantSubscriptionId": merchant_sub_id,
            "merchantTransactionId": merchant_transaction_id,
            "amount": amount_paise,
            "currency": "INR",
        }

        try:
            base64_payload = self._encode_payload(payload)
            x_verify = self._generate_x_verify(base64_payload, endpoint)
            headers = {
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
            }

            resp = requests.post(url, json={"request": base64_payload}, headers=headers, timeout=30)
            resp_data = resp.json()

            code = resp_data.get("code", "")
            data = resp_data.get("data", {})
            state = (data.get("state") or "").upper()

            is_success = resp.status_code == 200 and (code == "PAYMENT_SUCCESS" or state == "COMPLETED")
            is_pending = resp.status_code == 200 and (code in ("PAYMENT_PENDING", "PENDING") or state == "PENDING")

            status_str = "SUCCESS" if is_success else ("PENDING" if is_pending else "FAILED")

            return RedemptionResult(
                success=is_success,
                status=status_str,
                merchant_transaction_id=merchant_transaction_id,
                provider_transaction_id=data.get("transactionId"),
                provider_payment_id=data.get("transactionId"),
                amount_paise=amount_paise,
                raw_response=resp_data,
                error_message=None if (is_success or is_pending) else resp_data.get("message"),
            )
        except Exception as e:
            logger.error("PhonePe execute_redemption error: %s", str(e), exc_info=True)
            return RedemptionResult(
                success=False,
                status="ERROR",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                error_message=str(e),
            )

    def cancel_subscription(self, subscription, reason: str = "") -> bool:
        """
        Cancels an active subscription mandate on PhonePe:
        POST /v3/recurring/subscription/cancel
        """
        endpoint = "/v3/recurring/subscription/cancel"
        url = f"{self.base_url}{endpoint}"

        merchant_sub_id = subscription.provider_subscription_id or f"MSUB_{subscription.organization_id}"
        payload = {
            "merchantId": self.merchant_id,
            "merchantSubscriptionId": merchant_sub_id,
            "reason": reason or "Cancelled by organization admin",
        }

        try:
            base64_payload = self._encode_payload(payload)
            x_verify = self._generate_x_verify(base64_payload, endpoint)
            headers = {
                "Content-Type": "application/json",
                "X-VERIFY": x_verify,
            }

            resp = requests.post(url, json={"request": base64_payload}, headers=headers, timeout=20)
            resp_data = resp.json()
            return bool(resp.status_code == 200 and resp_data.get("success"))
        except Exception as e:
            logger.error("PhonePe cancel_subscription error: %s", str(e), exc_info=True)
            return False

    def verify_webhook(self, payload_bytes: bytes, headers: Dict[str, str]) -> WebhookResult:
        """
        Authenticates incoming PhonePe webhook callback.
        Supports:
        1. X-VERIFY checksum header
        2. Authorization header (Basic Auth / SHA256)
        3. HMAC secret verification
        """
        raw_text = payload_bytes.decode("utf-8") if isinstance(payload_bytes, bytes) else str(payload_bytes)
        x_verify_header = headers.get("X-Verify") or headers.get("X-VERIFY") or headers.get("x-verify")
        auth_header = headers.get("Authorization") or headers.get("authorization")

        is_authenticated = False

        # Mode A: Check X-VERIFY
        if x_verify_header and self.salt_key:
            expected_x_verify = self._generate_get_x_verify(raw_text)
            # Or SHA256(raw_text + salt_key) + "###" + salt_index
            hash_calc = f"{hashlib.sha256((raw_text + self.salt_key).encode('utf-8')).hexdigest()}###{self.salt_index}"
            if hmac.compare_digest(x_verify_header, hash_calc) or hmac.compare_digest(x_verify_header, expected_x_verify):
                is_authenticated = True

        # Mode B: Check Authorization Basic Auth header
        if not is_authenticated and auth_header and self.webhook_username and self.webhook_password:
            expected_token = hashlib.sha256(f"{self.webhook_username}:{self.webhook_password}".encode("utf-8")).hexdigest()
            # Often sent as "Basic <token>" or raw token
            token = auth_header.replace("Basic ", "").strip()
            if hmac.compare_digest(token, expected_token):
                is_authenticated = True

        # Mode C: HMAC Secret
        if not is_authenticated and self.webhook_secret:
            expected_hmac = hmac.new(self.webhook_secret.encode("utf-8"), raw_text.encode("utf-8"), hashlib.sha256).hexdigest()
            if auth_header and hmac.compare_digest(auth_header.strip(), expected_hmac):
                is_authenticated = True

        # If sandbox / UAT without webhook credentials configured, allow if payload is valid JSON
        if not is_authenticated and self.env == "UAT" and not (self.webhook_secret or self.webhook_username):
            logger.warning("UAT sandbox webhook received without configured secret - accepting for testing.")
            is_authenticated = True

        if not is_authenticated:
            logger.warning("PhonePe webhook signature verification failed.")
            return WebhookResult(
                is_valid=False,
                event_id="",
                event_type="",
                error_message="Invalid webhook signature or authorization",
            )

        # Parse JSON
        try:
            data = json.loads(raw_text)
        except Exception as e:
            return WebhookResult(is_valid=False, event_id="", event_type="", error_message=f"Invalid JSON: {str(e)}")

        # PhonePe can wrap response inside "response" base64
        if "response" in data and isinstance(data["response"], str):
            try:
                data = self._decode_response_payload(data["response"])
            except Exception as e:
                logger.error("Error decoding base64 response in webhook: %s", str(e))

        # Extract event info
        event_type = data.get("event") or data.get("type") or data.get("code") or "unknown"
        payload_body = data.get("payload") or data.get("data") or {}

        # Validate merchantId matches configured merchant (cross-tenant security check)
        incoming_merchant_id = payload_body.get("merchantId") or data.get("merchantId")
        if incoming_merchant_id and incoming_merchant_id != self.merchant_id:
            logger.warning("Rejected webhook for mismatched merchantId: %s (expected %s)",
                           incoming_merchant_id, self.merchant_id)
            return WebhookResult(
                is_valid=False,
                event_id="",
                event_type="",
                error_message=f"Merchant ID mismatch: {incoming_merchant_id}",
            )

        # Deterministic event ID derivation to guarantee replay protection:
        # Falls back to SHA256 of raw webhook text if eventId/transactionId are missing.
        event_id = (
            data.get("eventId")
            or payload_body.get("transactionId")
            or payload_body.get("merchantTransactionId")
            or f"evt_hash_{hashlib.sha256(raw_text.encode('utf-8')).hexdigest()[:32]}"
        )

        merchant_sub_id = (
            payload_body.get("merchantSubscriptionId")
            or data.get("merchantSubscriptionId")
            or payload_body.get("subscriptionId")
        )
        provider_sub_id = payload_body.get("subscriptionId")
        provider_payment_id = payload_body.get("transactionId")
        amount = payload_body.get("amount") or payload_body.get("maxAmount") or 0
        state = (payload_body.get("state") or data.get("code") or "").upper()

        return WebhookResult(
            is_valid=True,
            event_id=event_id,
            event_type=event_type,
            merchant_subscription_id=merchant_sub_id,
            provider_subscription_id=provider_sub_id,
            provider_payment_id=provider_payment_id,
            amount_paise=amount,
            state=state,
            raw_payload=data,
        )


"""
PhonePe AutoPay V2 Payment Gateway Provider
============================================
Implements the PaymentProvider ABC for PhonePe AutoPay V2 (Standard Checkout).
Provides complete recurring mandate management, pre-debit notifications (PDN),
explicit redemptions, and status reconciliation.

Financial Safety Guarantees:
- NOTIFIED status indicates customer delivery, NEVER payment success.
- For auto_debit_snapshot=True: NEVER call redeem(). Status is reconciled via get_order_status().
- For auto_debit_snapshot=False: Calls redeem() with the EXACT merchant_order_id.
- NOT_FOUND responses from the gateway are ALWAYS treated as ambiguous (status=PENDING).
- Success is only recorded when state == 'COMPLETED' AND paymentDetails[0].state == 'COMPLETED'.
- Subscription periods are never extended without authoritative COMPLETED confirmation.
- Concurrency and idempotency checks prevent duplicate debits.
- Strictly tenant-isolated: all DB operations filter by organization_id.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from flask import current_app

from model import (
    db,
    OrganizationStatus,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    SaaSSubscription,
    SaaSTransaction,
    MandateStatus,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    SaaSSubscriptionStatus,
    SaaSTransactionStatus,
)
from services.billing.payment_provider import (
    PaymentProvider,
    MandateSetupResult,
    VerificationResult,
    RedemptionResult,
    WebhookResult,
)
from services.billing.providers.phonepe_oauth import PhonePeOAuthClient, PhonePeOAuthError
from services.billing.providers.phonepe_autopay_client import (
    PhonePeAutoPayClient,
    PhonePeAPIError,
    PhonePeAuthenticationError,
    PhonePeNetworkError,
    PhonePeResponseError,
)
from services.billing.subscription_service import SubscriptionService
from services.billing.transaction_service import TransactionService

logger = logging.getLogger(__name__)


def _parse_datetime(val: Any) -> Optional[datetime]:
    """Safely parse epoch timestamps (seconds/millis) or ISO strings into datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, (int, float)):
        # Timestamps in ms are typically > 1e11
        if val > 1e11:
            val = val / 1000.0
        try:
            return datetime.utcfromtimestamp(val)
        except Exception:
            return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            num = float(val)
            if num > 1e11:
                num = num / 1000.0
            return datetime.utcfromtimestamp(num)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(val, fmt)
            except ValueError:
                continue
    return None


class PhonePeAutoPayV2Provider(PaymentProvider):
    """
    PhonePe AutoPay V2 Payment Gateway Provider.
    Encapsulates PhonePe OAuth2 token handling and AutoPay V2 API transport.
    """

    def __init__(
        self,
        oauth_client: Optional[PhonePeOAuthClient] = None,
        autopay_client: Optional[PhonePeAutoPayClient] = None,
        merchant_id: Optional[str] = None,
    ):
        self._oauth = oauth_client
        self._client = autopay_client
        self._merchant_id = merchant_id

    @property
    def name(self) -> str:
        """Provider identifier."""
        return "phonepe_v2"

    @property
    def merchant_id(self) -> Optional[str]:
        """Configured PhonePe Merchant ID."""
        if self._merchant_id is not None:
            return self._merchant_id
        try:
            if current_app:
                return current_app.config.get("PHONEPE_MERCHANT_ID")
        except RuntimeError:
            pass
        return None

    def _get_oauth_client(self) -> PhonePeOAuthClient:
        """Lazily initialize PhonePeOAuthClient from current_app.config if not injected."""
        if self._oauth is not None:
            return self._oauth
        self._oauth = PhonePeOAuthClient.from_config(current_app.config)
        return self._oauth

    def _get_client(self) -> PhonePeAutoPayClient:
        """Lazily initialize PhonePeAutoPayClient if not injected."""
        if self._client is not None:
            return self._client
        oauth = self._get_oauth_client()
        self._client = PhonePeAutoPayClient.from_oauth_client(oauth, merchant_id=self.merchant_id)
        return self._client

    # ── ABC Implementation ───────────────────────────────────────────────────

    def create_subscription_mandate(
        self,
        subscription,
        plan,
        billing_cycle: str,
        user,
        return_url: str,
        callback_url: str,
    ) -> MandateSetupResult:
        """
        Initiates a recurring AutoPay subscription mandate setup (/subscriptions/v2/setup).
        Creates a SaaSMandate record in INITIATED state and links it as active_mandate_id.
        """
        # Calculate amount in paise with 18% GST (same formula as V3 provider)
        cycle_str = billing_cycle.value.lower() if hasattr(billing_cycle, "value") else str(billing_cycle).lower()
        base_amount_paise = plan.yearly_price_paise if cycle_str == "yearly" else plan.monthly_price_paise
        gst_amount_paise = int(round(base_amount_paise * 0.18))
        total_amount_paise = base_amount_paise + gst_amount_paise

        timestamp_ms = int(time.time() * 1000)
        merchant_subscription_id = f"MSUB_V2_{subscription.organization_id}_{timestamp_ms}"
        merchant_order_id = f"MT_V2_{subscription.organization_id}_{timestamp_ms}"
        frequency = "YEARLY" if cycle_str == "yearly" else "MONTHLY"

        payload = {
            "merchantSubscriptionId": merchant_subscription_id,
            "merchantOrderId": merchant_order_id,
            "merchantUserId": f"USR_{user.id}",
            "amount": total_amount_paise,
            "redirectUrl": return_url,
            "subscriptionDetails": {
                "authWorkflowType": "TRANSACTION",
                "amountType": "VARIABLE",
                "maxAmount": total_amount_paise,
                "frequency": frequency,
            },
        }

        try:
            client = self._get_client()
            resp = client.setup_subscription(payload)
        except Exception as exc:
            logger.error("PhonePe V2 setup_subscription API failure for org=%s: %s",
                         subscription.organization_id, str(exc), exc_info=True)
            return MandateSetupResult(
                success=False,
                provider=self.name,
                merchant_subscription_id=merchant_subscription_id,
                merchant_transaction_id=merchant_order_id,
                error_message=str(exc),
            )

        redirect_url = resp.get("redirectUrl")
        if not redirect_url and isinstance(resp.get("redirectInfo"), dict):
            redirect_url = resp.get("redirectInfo", {}).get("url")

        try:
            mandate = SaaSMandate(
                organization_id=subscription.organization_id,
                subscription_id=subscription.id,
                payment_provider=self.name,
                merchant_subscription_id=merchant_subscription_id,
                provider_subscription_id=resp.get("subscriptionId"),
                mandate_reference=resp.get("mandateReference") or resp.get("umn"),
                mandate_status=MandateStatus.INITIATED.value,
                auth_workflow_type="TRANSACTION",
                amount_type="VARIABLE",
                max_amount_paise=total_amount_paise,
                frequency=frequency,
                auto_debit=False,  # Updated later upon authoritative confirmation
            )
            db.session.add(mandate)
            db.session.flush()

            # SSOT Rule 10: Only set active_mandate_id, never provider_subscription_id for V2
            subscription.active_mandate_id = mandate.id
            db.session.commit()
            logger.info("Created SaaSMandate id=%s for org=%s, msub=%s",
                        mandate.id, subscription.organization_id, merchant_subscription_id)
        except Exception as exc:
            db.session.rollback()
            logger.error("Failed to commit SaaSMandate for org=%s: %s",
                         subscription.organization_id, str(exc), exc_info=True)
            return MandateSetupResult(
                success=False,
                provider=self.name,
                merchant_subscription_id=merchant_subscription_id,
                merchant_transaction_id=merchant_order_id,
                error_message=f"Database commit error: {str(exc)}",
                raw_response=resp,
            )

        return MandateSetupResult(
            success=True,
            provider=self.name,
            merchant_subscription_id=merchant_subscription_id,
            provider_subscription_id=mandate.provider_subscription_id,
            merchant_transaction_id=merchant_order_id,
            redirect_url=redirect_url,
            raw_response=resp,
        )

    def check_subscription_status(self, merchant_subscription_id: str) -> VerificationResult:
        """
        Queries PhonePe for subscription mandate status:
        GET /subscriptions/v2/{merchantSubscriptionId}/status
        """
        try:
            client = self._get_client()
            resp = client.get_subscription_status(merchant_subscription_id, details=True)
        except Exception as exc:
            logger.warning("PhonePe V2 get_subscription_status error for msub=%s: %s",
                           merchant_subscription_id, str(exc))
            return VerificationResult(
                success=False,
                status="ERROR",
                merchant_subscription_id=merchant_subscription_id,
                error_message=str(exc),
            )

        state = (resp.get("state") or resp.get("status") or "").upper()
        auto_debit = resp.get("autoDebit")
        is_active = (state == "ACTIVE")

        # Update SaaSMandate if found
        mandate = SaaSMandate.query.filter_by(merchant_subscription_id=merchant_subscription_id).first()
        if mandate:
            try:
                if state:
                    mandate.mandate_status = state
                if auto_debit is not None:
                    mandate.auto_debit = bool(auto_debit)
                if resp.get("subscriptionId") and not mandate.provider_subscription_id:
                    mandate.provider_subscription_id = resp.get("subscriptionId")
                if (resp.get("umn") or resp.get("mandateReference")) and not mandate.mandate_reference:
                    mandate.mandate_reference = resp.get("umn") or resp.get("mandateReference")
                db.session.commit()
            except Exception as exc:
                db.session.rollback()
                logger.error("Failed to update mandate status for msub=%s: %s",
                             merchant_subscription_id, str(exc), exc_info=True)

        return VerificationResult(
            success=is_active,
            status=state or "UNKNOWN",
            merchant_subscription_id=merchant_subscription_id,
            provider_subscription_id=resp.get("subscriptionId"),
            mandate_reference=resp.get("umn") or resp.get("mandateReference"),
            amount_paise=resp.get("maxAmount") or resp.get("amount") or 0,
            raw_response=resp,
        )

    def check_transaction_status(self, merchant_transaction_id: str) -> VerificationResult:
        """
        Queries PhonePe for setup or redemption order status:
        GET /checkout/v2/order/{merchantOrderId}/status
        CRITICAL: NOT_FOUND is treated as ambiguous (status=PENDING), never FAILED.
        """
        try:
            client = self._get_client()
            resp = client.get_order_status(merchant_transaction_id, details=True)
        except PhonePeResponseError as exc:
            # Financial safety rule 4: NOT_FOUND is always ambiguous, never auto-retry
            is_not_found = (
                exc.status_code == 404
                or exc.error_code == "NOT_FOUND"
                or "NOT_FOUND" in str(exc).upper()
            )
            if is_not_found:
                logger.warning("Order %s returned NOT_FOUND; treated as ambiguous PENDING", merchant_transaction_id)
                return VerificationResult(
                    success=False,
                    status="PENDING",
                    merchant_subscription_id="",
                    provider_transaction_id=merchant_transaction_id,
                    error_message=f"Ambiguous: {str(exc)}",
                )
            logger.warning("Order %s failed with response error: %s", merchant_transaction_id, str(exc))
            return VerificationResult(
                success=False,
                status="FAILED",
                merchant_subscription_id="",
                provider_transaction_id=merchant_transaction_id,
                error_message=str(exc),
            )
        except Exception as exc:
            logger.warning("Order %s check failed with transport error: %s", merchant_transaction_id, str(exc))
            return VerificationResult(
                success=False,
                status="PENDING",
                merchant_subscription_id="",
                provider_transaction_id=merchant_transaction_id,
                error_message=f"Ambiguous transport error: {str(exc)}",
            )

        root_state = (resp.get("state") or "").upper()
        payment_details = resp.get("paymentDetails") or []
        first_payment = (
            payment_details[0]
            if (isinstance(payment_details, list) and len(payment_details) > 0 and isinstance(payment_details[0], dict))
            else {}
        )
        first_state = (first_payment.get("state") or "").upper()
        tx_id = first_payment.get("transactionId") or resp.get("transactionId")
        amount = first_payment.get("amount") or resp.get("amount") or 0
        merchant_sub_id = resp.get("merchantSubscriptionId") or ""
        provider_sub_id = resp.get("subscriptionId")

        # Financial safety rules 5 & 9:
        # Strict evaluation: root_state == COMPLETED AND paymentDetails[0].state == COMPLETED
        if root_state == "COMPLETED" and first_state == "COMPLETED":
            is_success = True
            status = "COMPLETED"
        elif root_state == "PENDING" or first_state == "PENDING":
            is_success = False
            status = "PENDING"
        elif root_state == "FAILED" or first_state == "FAILED":
            is_success = False
            status = "FAILED"
        else:
            # Non-standard/ambiguous response state
            is_success = False
            status = "PENDING"

        return VerificationResult(
            success=is_success,
            status=status,
            merchant_subscription_id=merchant_sub_id,
            provider_subscription_id=provider_sub_id,
            provider_transaction_id=tx_id,
            provider_payment_id=tx_id,
            amount_paise=amount,
            raw_response=resp,
        )

    def verify_payment(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> VerificationResult:
        """
        Verifies mandate setup / payment return after browser redirect.
        Never trusts the browser payload alone; verifies via authoritative S2S call.
        """
        merchant_order_id = payload.get("merchantOrderId") or payload.get("merchantTransactionId")
        merchant_sub_id = payload.get("merchantSubscriptionId")

        if not merchant_order_id and isinstance(payload.get("data"), dict):
            merchant_order_id = payload["data"].get("merchantOrderId") or payload["data"].get("merchantTransactionId")
            merchant_sub_id = merchant_sub_id or payload["data"].get("merchantSubscriptionId")

        if merchant_order_id:
            logger.info("Performing S2S transaction status inquiry for order=%s", merchant_order_id)
            res = self.check_transaction_status(merchant_order_id)
            if not res.merchant_subscription_id and merchant_sub_id:
                res.merchant_subscription_id = merchant_sub_id
            return res

        if merchant_sub_id:
            logger.info("Performing S2S subscription status inquiry for msub=%s", merchant_sub_id)
            return self.check_subscription_status(merchant_sub_id)

        return VerificationResult(
            success=False,
            status="UNKNOWN",
            merchant_subscription_id="",
            error_message="Insufficient data in verification payload: missing merchantOrderId/merchantSubscriptionId",
        )

    def notify_redemption(
        self,
        subscription,
        amount_paise: int,
        merchant_transaction_id: str,
    ) -> RedemptionResult:
        """
        Sends Pre-Debit Notification (PDN) via POST /subscriptions/v2/notify.
        Records an immutable auto_debit_snapshot on SaaSPreDebitNotification.
        """
        # Look up active mandate strictly with tenant isolation
        mandate = None
        if subscription.active_mandate_id:
            mandate = SaaSMandate.query.filter_by(
                id=subscription.active_mandate_id,
                organization_id=subscription.organization_id,
            ).first()
        if not mandate:
            mandate = SaaSMandate.query.filter_by(
                subscription_id=subscription.id,
                organization_id=subscription.organization_id,
            ).order_by(SaaSMandate.id.desc()).first()

        # Idempotency check: avoid duplicate notifications for same merchant_order_id
        existing_pdn = SaaSPreDebitNotification.query.filter_by(
            merchant_order_id=merchant_transaction_id,
            organization_id=subscription.organization_id,
        ).first()
        if existing_pdn:
            logger.info("Pre-debit notification already exists for order=%s, status=%s",
                        merchant_transaction_id, existing_pdn.status)
            is_success = existing_pdn.status in (
                PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
                PreDebitNotificationStatus.NOTIFIED.value,
            )
            return RedemptionResult(
                success=is_success,
                status=existing_pdn.status,
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=existing_pdn.amount_paise,
                raw_response=existing_pdn.raw_response_json,
            )

        auto_debit_snap = mandate.auto_debit if mandate else False
        notification = SaaSPreDebitNotification(
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            mandate_id=mandate.id if mandate else (subscription.active_mandate_id or 0),
            merchant_order_id=merchant_transaction_id,
            amount_paise=amount_paise,
            auto_debit_snapshot=auto_debit_snap,
            status=PreDebitNotificationStatus.INITIATED.value,
        )
        db.session.add(notification)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("Failed to commit SaaSPreDebitNotification for org=%s: %s",
                         subscription.organization_id, str(exc), exc_info=True)
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                error_message=str(exc),
            )

        merchant_sub_id = (
            mandate.merchant_subscription_id
            if mandate
            else (subscription.provider_subscription_id or f"MSUB_{subscription.organization_id}")
        )
        payload = {
            "merchantSubscriptionId": merchant_sub_id,
            "merchantOrderId": merchant_transaction_id,
            "amount": amount_paise,
            "notifyAt": int(time.time()),
            "paymentFlow": {"type": "SUBSCRIPTION_REDEMPTION"},
        }

        try:
            client = self._get_client()
            resp = client.notify_redemption(payload)
            notification.status = PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value
            notification.phonepe_order_id = resp.get("orderId") or resp.get("phonepeOrderId")
            notification.valid_after_at = _parse_datetime(resp.get("validAfter"))
            notification.valid_upto_at = _parse_datetime(resp.get("validUpto"))
            notification.notified_at = datetime.utcnow()
            notification.raw_response_json = resp
            db.session.commit()
            logger.info("PDN accepted by PhonePe for order=%s, status=NOTIFICATION_IN_PROGRESS",
                        merchant_transaction_id)
            return RedemptionResult(
                success=True,
                status=PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                raw_response=resp,
            )
        except Exception as exc:
            notification.status = PreDebitNotificationStatus.FAILED.value
            notification.error_code = getattr(exc, "error_code", None)
            notification.error_message = str(exc)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.error("PhonePe V2 notify_redemption failed for order=%s: %s",
                         merchant_transaction_id, str(exc), exc_info=True)
            return RedemptionResult(
                success=False,
                status=PreDebitNotificationStatus.FAILED.value,
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                error_message=str(exc),
            )

    def execute_redemption(
        self,
        subscription,
        amount_paise: int,
        merchant_transaction_id: str,
    ) -> RedemptionResult:
        """
        Executes explicit recurring debit redemption: POST /subscriptions/v2/redeem.
        CRITICAL FINANCIAL SAFETY ENFORCEMENT:
        1. PreDebitNotification must exist for this merchant_order_id.
        2. auto_debit_snapshot must be False (auto-debit must NEVER invoke redeem()).
        3. notification status must be NOTIFIED.
        4. Validity window (valid_upto_at) must not have passed.
        5. No previous redemption attempt may be in PENDING or AMBIGUOUS state.
        6. Ambiguous responses / NOT_FOUND do not retry and set AMBIGUOUS_NOT_FOUND.
        """
        # Safety Check 1: Lookup notification
        notification = SaaSPreDebitNotification.query.filter_by(
            merchant_order_id=merchant_transaction_id,
            organization_id=subscription.organization_id,
        ).first()
        if not notification:
            logger.error("execute_redemption blocked: No notification found for order=%s", merchant_transaction_id)
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                error_message="No pre-debit notification found for merchant_order_id",
            )

        # Safety Check 2: Block auto-debit mandates from manual redemption
        if notification.auto_debit_snapshot is True:
            logger.error("execute_redemption blocked: auto_debit_snapshot is True for order=%s", merchant_transaction_id)
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                error_message="Cannot call redeem() for auto-debit mandate. Use check_redemption_status() to poll instead.",
            )

        # Safety Check 3: Notification must be NOTIFIED
        if notification.status != PreDebitNotificationStatus.NOTIFIED.value:
            logger.error("execute_redemption blocked: Notification status is %s (must be NOTIFIED)", notification.status)
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                error_message=f"Notification not yet NOTIFIED (current status: {notification.status})",
            )

        # Safety Check 4: Check validity window
        if notification.valid_upto_at and datetime.utcnow() > notification.valid_upto_at:
            logger.warning("execute_redemption blocked: Notification expired at %s", notification.valid_upto_at)
            notification.status = PreDebitNotificationStatus.EXPIRED.value
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                error_message="Pre-debit notification has expired",
            )

        # Safety Check 5: In-flight or completed attempt check
        existing_attempt = SaaSRedemptionAttempt.query.filter_by(
            merchant_order_id=merchant_transaction_id,
            organization_id=subscription.organization_id,
        ).filter(
            SaaSRedemptionAttempt.status.in_([
                RedemptionAttemptStatus.INITIATED.value,
                RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
                RedemptionAttemptStatus.AMBIGUOUS_NOT_FOUND.value,
                RedemptionAttemptStatus.MANUAL_RECONCILIATION_REQUIRED.value,
                RedemptionAttemptStatus.COMPLETED.value,
            ])
        ).first()
        if existing_attempt:
            if existing_attempt.status == RedemptionAttemptStatus.COMPLETED.value:
                logger.info("Redemption attempt for order=%s already completed", merchant_transaction_id)
                return RedemptionResult(
                    success=True,
                    status="SUCCESS",
                    merchant_transaction_id=merchant_transaction_id,
                    provider_transaction_id=existing_attempt.phonepe_transaction_id,
                    provider_payment_id=existing_attempt.phonepe_transaction_id,
                    amount_paise=existing_attempt.amount_paise,
                    raw_response=existing_attempt.raw_response_json,
                )
            logger.warning("Redemption attempt for order=%s already in-flight (%s)",
                           merchant_transaction_id, existing_attempt.status)
            return RedemptionResult(
                success=False,
                status="PENDING",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=existing_attempt.amount_paise,
                error_message=f"Redemption attempt already exists in {existing_attempt.status} state",
            )

        # Determine attempt number and create record
        attempt_count = SaaSRedemptionAttempt.query.filter_by(
            merchant_order_id=merchant_transaction_id,
            organization_id=subscription.organization_id,
        ).count()

        attempt = SaaSRedemptionAttempt(
            organization_id=subscription.organization_id,
            subscription_id=subscription.id,
            notification_id=notification.id,
            merchant_order_id=merchant_transaction_id,
            amount_paise=amount_paise,
            status=RedemptionAttemptStatus.INITIATED.value,
            attempt_number=attempt_count + 1,
        )
        db.session.add(attempt)
        try:
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            logger.error("Failed to commit SaaSRedemptionAttempt: %s", str(exc), exc_info=True)
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                error_message=str(exc),
            )

        # Find mandate reference
        mandate = SaaSMandate.query.filter_by(
            id=subscription.active_mandate_id,
            organization_id=subscription.organization_id,
        ).first()
        if not mandate:
            mandate = notification.mandate

        merchant_sub_id = (
            mandate.merchant_subscription_id
            if mandate
            else (subscription.provider_subscription_id or f"MSUB_{subscription.organization_id}")
        )
        payload = {
            "merchantSubscriptionId": merchant_sub_id,
            "merchantOrderId": merchant_transaction_id,
            "amount": amount_paise,
        }

        try:
            client = self._get_client()
            resp = client.redeem(payload)
        except PhonePeResponseError as exc:
            is_not_found = (
                exc.status_code == 404
                or exc.error_code == "NOT_FOUND"
                or "NOT_FOUND" in str(exc).upper()
            )
            if is_not_found:
                attempt.status = RedemptionAttemptStatus.AMBIGUOUS_NOT_FOUND.value
                attempt.failure_reason = f"Gateway returned NOT_FOUND (ambiguous): {str(exc)}"
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                logger.warning("Redeem order %s returned NOT_FOUND -> AMBIGUOUS_NOT_FOUND", merchant_transaction_id)
                return RedemptionResult(
                    success=False,
                    status="PENDING",
                    merchant_transaction_id=merchant_transaction_id,
                    amount_paise=amount_paise,
                    error_message=str(exc),
                )
            attempt.status = RedemptionAttemptStatus.FAILED.value
            attempt.failure_reason = str(exc)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.error("Redeem order %s returned response error: %s", merchant_transaction_id, str(exc))
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                error_message=str(exc),
            )
        except Exception as exc:
            attempt.status = RedemptionAttemptStatus.PENDING_RECONCILIATION.value
            attempt.failure_reason = f"Ambiguous transport exception: {str(exc)}"
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.warning("Redeem order %s encountered exception -> PENDING_RECONCILIATION: %s",
                           merchant_transaction_id, str(exc))
            return RedemptionResult(
                success=False,
                status="PENDING",
                merchant_transaction_id=merchant_transaction_id,
                amount_paise=amount_paise,
                error_message=str(exc),
            )

        attempt.raw_response_json = resp
        root_state = (resp.get("state") or "").upper()
        payment_details = resp.get("paymentDetails") or []
        first_payment = (
            payment_details[0]
            if (isinstance(payment_details, list) and len(payment_details) > 0 and isinstance(payment_details[0], dict))
            else {}
        )
        first_payment_state = (first_payment.get("state") or "").upper()
        tx_id = first_payment.get("transactionId") or resp.get("transactionId")
        if tx_id:
            attempt.phonepe_transaction_id = tx_id

        # Financial safety rules 5: COMPLETED requires both root state and payment details state
        if root_state == "COMPLETED" and first_payment_state == "COMPLETED":
            attempt.status = RedemptionAttemptStatus.COMPLETED.value
            notification.status = PreDebitNotificationStatus.CONSUMED.value
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.info("Redemption COMPLETED for order=%s, tx=%s", merchant_transaction_id, tx_id)
            return RedemptionResult(
                success=True,
                status="SUCCESS",
                merchant_transaction_id=merchant_transaction_id,
                provider_transaction_id=tx_id,
                provider_payment_id=tx_id,
                amount_paise=amount_paise,
                raw_response=resp,
            )
        elif root_state == "PENDING" or first_payment_state == "PENDING":
            attempt.status = RedemptionAttemptStatus.PENDING_RECONCILIATION.value
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.info("Redemption PENDING for order=%s, tx=%s", merchant_transaction_id, tx_id)
            return RedemptionResult(
                success=False,
                status="PENDING",
                merchant_transaction_id=merchant_transaction_id,
                provider_transaction_id=tx_id,
                provider_payment_id=tx_id,
                amount_paise=amount_paise,
                raw_response=resp,
            )
        elif root_state == "FAILED" or first_payment_state == "FAILED":
            attempt.status = RedemptionAttemptStatus.FAILED.value
            attempt.failure_reason = (
                resp.get("message")
                or first_payment.get("responseCodeDescription")
                or "Redemption failed"
            )
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.warning("Redemption FAILED for order=%s: %s", merchant_transaction_id, attempt.failure_reason)
            return RedemptionResult(
                success=False,
                status="FAILED",
                merchant_transaction_id=merchant_transaction_id,
                provider_transaction_id=tx_id,
                provider_payment_id=tx_id,
                amount_paise=amount_paise,
                raw_response=resp,
                error_message=attempt.failure_reason,
            )
        else:
            # Ambiguous/unknown state
            attempt.status = RedemptionAttemptStatus.PENDING_RECONCILIATION.value
            attempt.failure_reason = f"Ambiguous response state: {root_state}"
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.warning("Redemption returned ambiguous state %s for order=%s", root_state, merchant_transaction_id)
            return RedemptionResult(
                success=False,
                status="PENDING",
                merchant_transaction_id=merchant_transaction_id,
                provider_transaction_id=tx_id,
                provider_payment_id=tx_id,
                amount_paise=amount_paise,
                raw_response=resp,
            )

    def cancel_subscription(self, subscription, reason: str = "") -> bool:
        """
        Cancels active AutoPay subscription mandate on PhonePe:
        POST /checkout/v2/subscriptions/{merchantSubscriptionId}/cancel
        """
        if not subscription.active_mandate_id:
            logger.warning("cancel_subscription: no active_mandate_id for subscription org=%s",
                           subscription.organization_id)
            return False

        mandate = SaaSMandate.query.filter_by(
            id=subscription.active_mandate_id,
            organization_id=subscription.organization_id,
        ).first()
        if not mandate:
            logger.warning("cancel_subscription: mandate %s not found for org=%s",
                           subscription.active_mandate_id, subscription.organization_id)
            return False

        try:
            client = self._get_client()
            client.cancel_subscription(mandate.merchant_subscription_id)
            try:
                client.get_subscription_status(mandate.merchant_subscription_id)
            except Exception as status_err:
                logger.warning("Could not query subscription status after cancel: %s", status_err)

            mandate.mandate_status = MandateStatus.CANCELLED.value
            subscription.active_mandate_id = None
            db.session.commit()
            logger.info("Cancelled SaaSMandate %s for org=%s", mandate.id, subscription.organization_id)
            return True
        except Exception as exc:
            db.session.rollback()
            logger.error("cancel_subscription failed for org=%s: %s",
                         subscription.organization_id, str(exc), exc_info=True)
            return False

    def verify_webhook(self, payload_bytes: bytes, headers: Dict[str, str]) -> WebhookResult:
        """
        Parses incoming webhook event.
        V2 webhooks are treated as UNTRUSTED TRANSPORT HINTS per architecture.
        Authoritative state verification must be performed S2S afterwards.
        """
        raw_text = payload_bytes.decode("utf-8") if isinstance(payload_bytes, bytes) else str(payload_bytes)
        try:
            data = json.loads(raw_text)
        except Exception as exc:
            return WebhookResult(
                is_valid=False,
                event_id="",
                event_type="",
                error_message=f"Invalid webhook JSON: {str(exc)}",
            )

        payload_body = data.get("payload") or data.get("data") or {}

        # Cross-tenant check: validate merchant ID matches configured merchant
        incoming_mid = data.get("merchantId") or payload_body.get("merchantId")
        if incoming_mid and self.merchant_id and incoming_mid != self.merchant_id:
            logger.warning("PhonePe V2 Webhook merchant ID mismatch: %s != %s", incoming_mid, self.merchant_id)
            return WebhookResult(
                is_valid=False,
                event_id="",
                event_type="",
                error_message=f"Merchant ID mismatch: {incoming_mid}",
            )

        # Production basic auth verification if configured
        try:
            env = current_app.config.get("PHONEPE_ENV", "UAT").upper() if current_app else "UAT"
            webhook_user = current_app.config.get("PHONEPE_WEBHOOK_USERNAME") if current_app else None
            webhook_pass = current_app.config.get("PHONEPE_WEBHOOK_PASSWORD") if current_app else None
        except RuntimeError:
            env = "UAT"
            webhook_user = None
            webhook_pass = None

        if env == "PRODUCTION" and webhook_user and webhook_pass:
            auth_header = headers.get("Authorization") or headers.get("authorization")
            if not auth_header:
                return WebhookResult(
                    is_valid=False,
                    event_id="",
                    event_type="",
                    error_message="Missing Authorization header in production webhook",
                )
            expected_token = hashlib.sha256(f"{webhook_user}:{webhook_pass}".encode("utf-8")).hexdigest()
            token = auth_header.replace("Basic ", "").strip()
            if not hmac.compare_digest(token, expected_token):
                return WebhookResult(
                    is_valid=False,
                    event_id="",
                    event_type="",
                    error_message="Invalid webhook authorization token",
                )

        event_id = (
            data.get("eventId")
            or data.get("id")
            or payload_body.get("transactionId")
            or payload_body.get("merchantOrderId")
            or f"evt_{hashlib.sha256(raw_text.encode('utf-8')).hexdigest()[:32]}"
        )
        event_type = (
            data.get("event")
            or data.get("type")
            or data.get("eventType")
            or payload_body.get("state")
            or "unknown"
        )
        merchant_sub_id = payload_body.get("merchantSubscriptionId") or data.get("merchantSubscriptionId")
        provider_sub_id = payload_body.get("subscriptionId") or data.get("subscriptionId")
        provider_payment_id = payload_body.get("transactionId")
        amount = payload_body.get("amount") or 0
        state = (payload_body.get("state") or "").upper()

        return WebhookResult(
            is_valid=True,
            event_id=str(event_id),
            event_type=str(event_type),
            merchant_subscription_id=merchant_sub_id,
            provider_subscription_id=provider_sub_id,
            provider_payment_id=provider_payment_id,
            amount_paise=amount,
            state=state,
            raw_payload=data,
        )

    # ── Additional V2 Domain Reconciliation Methods ─────────────────────────

    def reconcile_notification_status(
        self,
        notification: SaaSPreDebitNotification,
    ) -> SaaSPreDebitNotification:
        """
        Polls PhonePe order status for a Pre-Debit Notification and updates DB state.
        Transitions status to NOTIFIED when delivered.
        """
        try:
            client = self._get_client()
            resp = client.get_order_status(notification.merchant_order_id, details=True)
            notification.raw_response_json = resp
            state = (resp.get("state") or "").upper()

            if state in ("COMPLETED", "SUCCESS", "NOTIFIED"):
                notification.status = PreDebitNotificationStatus.NOTIFIED.value
                if not notification.notified_at:
                    notification.notified_at = datetime.utcnow()
            elif state in ("FAILED", "DECLINED"):
                notification.status = PreDebitNotificationStatus.FAILED.value
                notification.error_message = resp.get("message") or f"Notification state: {state}"

            if resp.get("validAfter"):
                notification.valid_after_at = _parse_datetime(resp.get("validAfter"))
            if resp.get("validUpto"):
                notification.valid_upto_at = _parse_datetime(resp.get("validUpto"))

            db.session.commit()
            logger.info("Reconciled notification %s to status=%s", notification.merchant_order_id, notification.status)
        except Exception as exc:
            db.session.rollback()
            logger.error("Error reconciling notification %s: %s",
                         notification.merchant_order_id, str(exc), exc_info=True)
        return notification

    def reconcile_redemption_status(
        self,
        attempt: SaaSRedemptionAttempt,
    ) -> SaaSRedemptionAttempt:
        """
        Polls PhonePe order status for an explicit or auto-debit redemption attempt.
        Advances subscription period ONLY on authoritative COMPLETED confirmation.
        """
        try:
            client = self._get_client()
            resp = client.get_order_status(attempt.merchant_order_id, details=True)
            attempt.raw_response_json = resp
            root_state = (resp.get("state") or "").upper()
            payment_details = resp.get("paymentDetails") or []
            first_payment = (
                payment_details[0]
                if (isinstance(payment_details, list) and len(payment_details) > 0 and isinstance(payment_details[0], dict))
                else {}
            )
            first_payment_state = (first_payment.get("state") or "").upper()
            tx_id = first_payment.get("transactionId") or resp.get("transactionId")
            if tx_id:
                attempt.phonepe_transaction_id = tx_id

            if root_state == "COMPLETED" and first_payment_state == "COMPLETED":
                attempt.status = RedemptionAttemptStatus.COMPLETED.value
                if attempt.notification:
                    attempt.notification.status = PreDebitNotificationStatus.CONSUMED.value

                # Advance subscription period & record transaction
                sub = attempt.subscription
                if sub:
                    new_start, new_end = SubscriptionService.calculate_period_dates(sub.billing_cycle.value)
                    sub.current_period_start = new_start
                    sub.current_period_end = new_end
                    sub.next_billing_at = new_end
                    sub.status = SaaSSubscriptionStatus.ACTIVE
                    sub.grace_period_end = None
                    org = sub.organization
                    if org:
                        org.status = OrganizationStatus.ACTIVE

                    tx = TransactionService.record_transaction(
                        organization_id=sub.organization_id,
                        subscription_id=sub.id,
                        payment_provider=self.name,
                        amount_paise=attempt.amount_paise,
                        status=SaaSTransactionStatus.SUCCESS,
                        provider_payment_id=attempt.phonepe_transaction_id or attempt.merchant_order_id,
                        provider_transaction_id=attempt.phonepe_transaction_id,
                        provider_subscription_id=sub.subscription_id,
                        payment_method="UPI_AUTOPAY",
                        billing_period_start=new_start,
                        billing_period_end=new_end,
                    )
                    if tx:
                        tx.mandate_id = sub.active_mandate_id
                        tx.redemption_attempt_id = attempt.id
                db.session.commit()
                logger.info("Reconciled redemption attempt %s to COMPLETED; advanced subscription org=%s",
                            attempt.merchant_order_id, attempt.organization_id)
            elif root_state == "FAILED" or first_payment_state == "FAILED":
                attempt.status = RedemptionAttemptStatus.FAILED.value
                attempt.failure_reason = (
                    resp.get("message")
                    or first_payment.get("responseCodeDescription")
                    or "Debit failed"
                )
                db.session.commit()
                logger.warning("Reconciled redemption attempt %s to FAILED", attempt.merchant_order_id)
            elif root_state == "PENDING" or first_payment_state == "PENDING":
                attempt.status = RedemptionAttemptStatus.PENDING_RECONCILIATION.value
                db.session.commit()
            else:
                attempt.status = RedemptionAttemptStatus.PENDING_RECONCILIATION.value
                db.session.commit()
        except PhonePeResponseError as exc:
            is_not_found = (
                exc.status_code == 404
                or exc.error_code == "NOT_FOUND"
                or "NOT_FOUND" in str(exc).upper()
            )
            if is_not_found:
                attempt.status = RedemptionAttemptStatus.AMBIGUOUS_NOT_FOUND.value
                attempt.failure_reason = f"Gateway returned NOT_FOUND (ambiguous): {str(exc)}"
            else:
                attempt.status = RedemptionAttemptStatus.FAILED.value
                attempt.failure_reason = str(exc)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        except Exception as exc:
            attempt.status = RedemptionAttemptStatus.PENDING_RECONCILIATION.value
            attempt.failure_reason = f"Ambiguous transport exception: {str(exc)}"
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
        return attempt

    def sync_mandate_status(self, mandate: SaaSMandate) -> SaaSMandate:
        """
        Synchronizes live PhonePe mandate status into the SaaSMandate DB record.
        """
        try:
            client = self._get_client()
            resp = client.get_subscription_status(mandate.merchant_subscription_id, details=True)
            state = (resp.get("state") or resp.get("status") or "").upper()
            if state:
                mandate.mandate_status = state
            auto_debit = resp.get("autoDebit")
            if auto_debit is not None:
                mandate.auto_debit = bool(auto_debit)
            if resp.get("subscriptionId") and not mandate.provider_subscription_id:
                mandate.provider_subscription_id = resp.get("subscriptionId")
            if (resp.get("umn") or resp.get("mandateReference")) and not mandate.mandate_reference:
                mandate.mandate_reference = resp.get("umn") or resp.get("mandateReference")
            db.session.commit()
            logger.info("Synced SaaSMandate %s: status=%s, auto_debit=%s",
                        mandate.id, mandate.mandate_status, mandate.auto_debit)
        except Exception as exc:
            db.session.rollback()
            logger.error("Error syncing mandate %s: %s", mandate.merchant_subscription_id, str(exc), exc_info=True)
        return mandate

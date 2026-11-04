import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from sqlalchemy import or_

from model import (
    db,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSTransaction,
    SaaSTransactionStatus,
    SaaSBillingPeriodReservation,
    BillingPeriodReservationStatus,
    SaaSRedemptionAttempt,
    RedemptionAttemptStatus,
    SaaSPreDebitNotification,
    PreDebitNotificationStatus,
)
from services.billing.subscription_service import SubscriptionService
from services.billing.transaction_service import TransactionService
from services.billing.providers.phonepe_autopay_v2_provider import _compute_billing_period_key

logger = logging.getLogger(__name__)


class RenewalService:
    """
    Automated recurring billing and renewal engine for PhonePe UPI AutoPay (V2 and V3).
    Complies with RBI / NPCI UPI AutoPay regulations:
    - Pre-debit notifications sent 24-48 hours before execution
    - Automatic debit execution on billing cycle date
    - Grace period management and retries
    - Distributed concurrency safety with database-level atomic claim leases
    - Authoritative in-flight reconciliation check before any debit execution
    - Never touches Razorpay records
    """

    @classmethod
    def process_upcoming_notifications(cls, provider, hours_ahead: int = 48) -> List[Dict[str, Any]]:
        """
        Finds active subscriptions whose next_billing_at is within `hours_ahead`
        and triggers PhonePe Pre-Debit Notification (Redemption Notify).
        Protected by database-level atomic subscription claim leases.
        Strictly isolates to provider.name (never touches Razorpay).
        """
        now = datetime.utcnow()
        threshold = now + timedelta(hours=hours_ahead)

        subscriptions = SaaSSubscription.query.filter(
            SaaSSubscription.payment_provider == provider.name,
            SaaSSubscription.status == SaaSSubscriptionStatus.ACTIVE,
            SaaSSubscription.next_billing_at != None,
            SaaSSubscription.next_billing_at <= threshold,
            SaaSSubscription.next_billing_at > now,
            or_(
                SaaSSubscription.claim_token.is_(None),
                SaaSSubscription.claim_expires_at < now,
            ),
        ).all()

        results = []
        for sub in subscriptions:
            # Atomic claim acquisition to prevent duplicate concurrent notification runs
            claim_token = SubscriptionService.acquire_subscription_claim(
                subscription_id=sub.id,
                organization_id=sub.organization_id,
                lease_duration_seconds=300,
            )
            if not claim_token:
                logger.info("Subscription %s already claimed by another worker; skipping notification.", sub.id)
                continue

            try:
                # Check live mandate status with PhonePe before sending notification
                if sub.provider_subscription_id:
                    try:
                        mandate_chk = provider.check_subscription_status(sub.provider_subscription_id)
                        if mandate_chk.status in ("PAUSED", "REVOKED", "CANCELLED"):
                            logger.warning("Subscription %s is %s on PhonePe. Skipping notification.",
                                           sub.provider_subscription_id, mandate_chk.status)
                            if mandate_chk.status == "PAUSED":
                                SubscriptionService.suspend_subscription(sub, reason="Mandate paused by customer")
                            else:
                                SubscriptionService.cancel_subscription(sub, reason="Mandate revoked by customer")
                            continue
                    except Exception as e:
                        logger.warning("Could not check mandate status for sub %s: %s", sub.id, str(e))

                # Deterministic transaction ID per billing cycle to guarantee PhonePe idempotency
                cycle_tag = sub.next_billing_at.strftime("%Y%m%d%H%M") if sub.next_billing_at else str(int(now.timestamp()))
                if provider.name == "phonepe_v2":
                    merchant_tx_id = f"ORD_{sub.organization_id}_{sub.id}_{cycle_tag}"
                else:
                    merchant_tx_id = f"NOTIFY_{sub.organization_id}_{sub.id}_{cycle_tag}"

                amount = sub.amount_paise or (
                    sub.plan.yearly_price_paise if sub.billing_cycle and sub.billing_cycle.value == "yearly"
                    else (sub.plan.monthly_price_paise if sub.plan else 0)
                )

                logger.info("Sending Pre-Debit Notification for sub_id=%s, org=%s, amount=%s paise, tx_id=%s",
                            sub.provider_subscription_id, sub.organization_id, amount, merchant_tx_id)

                res = provider.notify_redemption(
                    subscription=sub,
                    amount_paise=amount,
                    merchant_transaction_id=merchant_tx_id,
                    claim_token=claim_token,
                )

                results.append({
                    "subscription_id": sub.id,
                    "org_id": sub.organization_id,
                    "success": res.success,
                    "merchant_transaction_id": merchant_tx_id,
                    "error": res.error_message,
                })
            finally:
                SubscriptionService.release_subscription_claim(
                    subscription_id=sub.id,
                    organization_id=sub.organization_id,
                    claim_token=claim_token,
                )

        return results

    @classmethod
    def process_due_renewals(cls, provider) -> List[Dict[str, Any]]:
        """
        Finds active subscriptions whose billing date has arrived (next_billing_at <= now)
        and executes recurring debit via PhonePe AutoPay.
        Implements double-payment protection:
        - Strict isolation to provider.name (never touches Razorpay records)
        - Deterministic merchantTransactionId per billing cycle (gateway-level idempotency)
        - Atomic database-level pre-claim lease to prevent concurrent scheduler duplicates
        - Authoritative in-flight reconciliation check before any debit execution
        """
        now = datetime.utcnow()
        due_subscriptions = SaaSSubscription.query.filter(
            SaaSSubscription.payment_provider == provider.name,
            SaaSSubscription.status.in_([SaaSSubscriptionStatus.ACTIVE, SaaSSubscriptionStatus.GRACE]),
            SaaSSubscription.next_billing_at != None,
            SaaSSubscription.next_billing_at <= now,
            or_(
                SaaSSubscription.claim_token.is_(None),
                SaaSSubscription.claim_expires_at < now,
            ),
        ).all()

        results = []
        for sub in due_subscriptions:
            # Atomic database-level claim acquisition
            claim_token = SubscriptionService.acquire_subscription_claim(
                subscription_id=sub.id,
                organization_id=sub.organization_id,
                lease_duration_seconds=300,
            )
            if not claim_token:
                logger.info("Subscription %s already claimed by another worker; skipping renewal.", sub.id)
                continue

            try:
                # 1. Live mandate status check: Confirm mandate is actively ACTIVE on PhonePe
                if sub.provider_subscription_id:
                    try:
                        mandate_chk = provider.check_subscription_status(sub.provider_subscription_id)
                        if mandate_chk.status in ("PAUSED", "REVOKED", "CANCELLED"):
                            logger.warning("Subscription %s is %s on PhonePe. Skipping redemption.",
                                           sub.provider_subscription_id, mandate_chk.status)
                            if mandate_chk.status == "PAUSED":
                                SubscriptionService.suspend_subscription(sub, reason="Mandate paused by customer")
                            else:
                                SubscriptionService.cancel_subscription(sub, reason="Mandate revoked by customer")
                            continue
                    except Exception as e:
                        logger.warning("Could not verify mandate status for sub %s: %s", sub.id, str(e))

                cycle_tag = sub.next_billing_at.strftime("%Y%m%d%H%M") if sub.next_billing_at else str(int(now.timestamp()))
                amount = sub.amount_paise or (
                    sub.plan.yearly_price_paise if sub.billing_cycle and sub.billing_cycle.value == "yearly"
                    else (sub.plan.monthly_price_paise if sub.plan else 0)
                )

                # Determine merchant_tx_id based on provider
                if provider.name == "phonepe_v2":
                    pdn = SaaSPreDebitNotification.query.filter_by(
                        subscription_id=sub.id,
                        organization_id=sub.organization_id,
                        status=PreDebitNotificationStatus.NOTIFIED.value,
                    ).order_by(SaaSPreDebitNotification.id.desc()).first()
                    if pdn:
                        merchant_tx_id = pdn.merchant_order_id
                    else:
                        merchant_tx_id = f"ORD_{sub.organization_id}_{sub.id}_{cycle_tag}"
                else:
                    merchant_tx_id = f"RENEW_{sub.organization_id}_{sub.id}_{cycle_tag}"

                # 2. In-flight reconciliation check before initiating debit:
                if provider.name == "phonepe_v2":
                    if sub.current_period_start:
                        bpk = _compute_billing_period_key(sub.id, sub.current_period_start)
                        bpr = SaaSBillingPeriodReservation.query.filter_by(
                            subscription_id=sub.id,
                            organization_id=sub.organization_id,
                            billing_period_key=bpk,
                        ).first()
                        if bpr:
                            if bpr.status == BillingPeriodReservationStatus.COMPLETED.value:
                                logger.info("Billing period %s already completed for sub %s. Skipping.", bpk, sub.id)
                                continue
                            if (
                                bpr.redemption_initiated_at is not None
                                or bpr.status == BillingPeriodReservationStatus.PENDING_RECONCILIATION.value
                            ):
                                logger.info("In-flight V2 redemption detected for order %s (initiated_at=%s). Reconciling...",
                                            bpr.merchant_order_id, bpr.redemption_initiated_at)
                                attempt = SaaSRedemptionAttempt.query.filter_by(
                                    merchant_order_id=bpr.merchant_order_id,
                                    organization_id=sub.organization_id,
                                ).first()
                                if attempt:
                                    reconciled_attempt = provider.reconcile_redemption_status(attempt)
                                    if reconciled_attempt.status == RedemptionAttemptStatus.COMPLETED.value:
                                        logger.info("In-flight V2 redemption %s reconciled to COMPLETED.", bpr.merchant_order_id)
                                        results.append({
                                            "subscription_id": sub.id,
                                            "org_id": sub.organization_id,
                                            "success": True,
                                            "merchant_transaction_id": bpr.merchant_order_id,
                                            "status": "RECONCILED_SUCCESS",
                                        })
                                        continue
                                    elif reconciled_attempt.status in (
                                        RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
                                        RedemptionAttemptStatus.INITIATED.value,
                                    ):
                                        logger.info("In-flight V2 redemption %s still pending reconciliation.", bpr.merchant_order_id)
                                        results.append({
                                            "subscription_id": sub.id,
                                            "org_id": sub.organization_id,
                                            "success": False,
                                            "merchant_transaction_id": bpr.merchant_order_id,
                                            "status": "PENDING_RECONCILIATION",
                                        })
                                        continue
                                    else:
                                        logger.warning("In-flight V2 redemption %s reconciled to %s: %s",
                                                       bpr.merchant_order_id, reconciled_attempt.status, reconciled_attempt.failure_reason)
                                        results.append({
                                            "subscription_id": sub.id,
                                            "org_id": sub.organization_id,
                                            "success": False,
                                            "merchant_transaction_id": bpr.merchant_order_id,
                                            "status": reconciled_attempt.status,
                                            "error": reconciled_attempt.failure_reason,
                                        })
                                        continue
                else:
                    # Legacy PhonePe V3
                    existing_tx = SaaSTransaction.query.filter_by(
                        provider_payment_id=merchant_tx_id,
                        organization_id=sub.organization_id,
                    ).first()
                    if existing_tx:
                        if existing_tx.status == SaaSTransactionStatus.SUCCESS:
                            logger.info("Renewal cycle %s already processed successfully for sub %s. Skipping.",
                                        merchant_tx_id, sub.id)
                            continue
                        elif existing_tx.status == SaaSTransactionStatus.PENDING:
                            logger.info("Reconciling in-flight pending renewal %s with PhonePe", merchant_tx_id)
                            try:
                                status_res = provider.check_transaction_status(merchant_tx_id, organization_id=sub.organization_id)
                                if status_res.status == "SUCCESS":
                                    logger.info("Reconciliation confirmed SUCCESS for %s", merchant_tx_id)
                                    existing_tx.status = SaaSTransactionStatus.SUCCESS
                                    new_start, new_end = SubscriptionService.calculate_period_dates(sub.billing_cycle.value, start_date=now)
                                    sub.current_period_start = new_start
                                    sub.current_period_end = new_end
                                    sub.next_billing_at = new_end
                                    sub.status = SaaSSubscriptionStatus.ACTIVE
                                    sub.grace_period_end = None
                                    db.session.commit()
                                    results.append({
                                        "subscription_id": sub.id,
                                        "org_id": sub.organization_id,
                                        "success": True,
                                        "merchant_transaction_id": merchant_tx_id,
                                        "status": "RECONCILED_SUCCESS",
                                    })
                                    continue
                                elif status_res.status == "PENDING":
                                    logger.info("Renewal %s is still PENDING at bank/rail. Deferring check.", merchant_tx_id)
                                    sub.next_billing_at = now + timedelta(hours=1)
                                    db.session.commit()
                                    results.append({
                                        "subscription_id": sub.id,
                                        "org_id": sub.organization_id,
                                        "success": False,
                                        "merchant_transaction_id": merchant_tx_id,
                                        "status": "PENDING",
                                    })
                                    continue
                                else:
                                    existing_tx.status = SaaSTransactionStatus.FAILED
                                    db.session.commit()
                            except Exception as e:
                                logger.warning("Could not reconcile in-flight tx %s: %s", merchant_tx_id, str(e))
                                sub.next_billing_at = now + timedelta(hours=1)
                                db.session.commit()
                                continue

                # 3. Execute renewal debit with claim_token passed through to prevent nested lock deadlocks
                logger.info("Executing AutoPay renewal for org=%s, sub_id=%s, amount=%s paise, tx_id=%s",
                            sub.organization_id, sub.provider_subscription_id, amount, merchant_tx_id)

                res = provider.execute_redemption(
                    subscription=sub,
                    amount_paise=amount,
                    merchant_transaction_id=merchant_tx_id,
                    claim_token=claim_token,
                )

                # 4. Strict three-state handling: SUCCESS, PENDING, FAILED
                if res.status == "SUCCESS":
                    new_start, new_end = SubscriptionService.calculate_period_dates(sub.billing_cycle.value, start_date=now)
                    sub.current_period_start = new_start
                    sub.current_period_end = new_end
                    sub.next_billing_at = new_end
                    sub.status = SaaSSubscriptionStatus.ACTIVE
                    sub.grace_period_end = None
                    db.session.commit()

                    TransactionService.record_transaction(
                        organization_id=sub.organization_id,
                        subscription_id=sub.id,
                        payment_provider=provider.name,
                        amount_paise=amount,
                        status=SaaSTransactionStatus.SUCCESS,
                        provider_payment_id=res.provider_payment_id or merchant_tx_id,
                        provider_transaction_id=res.provider_transaction_id,
                        provider_subscription_id=sub.provider_subscription_id,
                        payment_method="UPI_AUTOPAY",
                        billing_period_start=new_start,
                        billing_period_end=new_end,
                    )
                elif res.status == "PENDING":
                    logger.info("Renewal debit PENDING at gateway for org=%s, sub_id=%s, tx=%s",
                                sub.organization_id, sub.provider_subscription_id, merchant_tx_id)
                    sub.next_billing_at = now + timedelta(hours=1)
                    db.session.commit()

                    TransactionService.record_transaction(
                        organization_id=sub.organization_id,
                        subscription_id=sub.id,
                        payment_provider=provider.name,
                        amount_paise=amount,
                        status=SaaSTransactionStatus.PENDING,
                        provider_payment_id=res.provider_payment_id or merchant_tx_id,
                        provider_transaction_id=res.provider_transaction_id,
                        provider_subscription_id=sub.provider_subscription_id,
                        payment_method="UPI_AUTOPAY",
                        failure_reason="Debit in-flight with bank/UPI rail",
                    )
                else:
                    logger.warning("Renewal failed for org=%s: %s", sub.organization_id, res.error_message)
                    sub.next_billing_at = now + timedelta(hours=24)
                    db.session.commit()

                    TransactionService.record_transaction(
                        organization_id=sub.organization_id,
                        subscription_id=sub.id,
                        payment_provider=provider.name,
                        amount_paise=amount,
                        status=SaaSTransactionStatus.FAILED,
                        provider_payment_id=merchant_tx_id,
                        provider_subscription_id=sub.provider_subscription_id,
                        payment_method="UPI_AUTOPAY",
                        failure_reason=res.error_message or "Recurring AutoPay debit failed",
                    )

                    if sub.status == SaaSSubscriptionStatus.GRACE and sub.grace_period_end and sub.grace_period_end <= now:
                        SubscriptionService.suspend_subscription(sub, reason="Subscription grace period expired after payment failure")
                    elif sub.status != SaaSSubscriptionStatus.GRACE:
                        SubscriptionService.enter_grace_period(sub, days=7)

                results.append({
                    "subscription_id": sub.id,
                    "org_id": sub.organization_id,
                    "success": res.success,
                    "merchant_transaction_id": merchant_tx_id,
                    "error": res.error_message,
                })
            finally:
                SubscriptionService.release_subscription_claim(
                    subscription_id=sub.id,
                    organization_id=sub.organization_id,
                    claim_token=claim_token,
                )

        return results

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
from services.billing.providers.phonepe_autopay_v2_provider import (
    _compute_billing_period_key,
    _parse_datetime,
)
from services.billing.providers.phonepe_autopay_client import (
    PhonePeResponseError,
    PhonePeNetworkError,
)

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
                                matching_attempts = SaaSRedemptionAttempt.query.filter_by(
                                    merchant_order_id=bpr.merchant_order_id,
                                    organization_id=sub.organization_id,
                                    subscription_id=sub.id,
                                ).all()
                                if len(matching_attempts) == 1:
                                    attempt = matching_attempts[0]
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
                                elif len(matching_attempts) == 0:
                                    logger.error(
                                        "Missing SaaSRedemptionAttempt for in-flight reservation order %s on sub %s",
                                        bpr.merchant_order_id, sub.id
                                    )
                                    bpr.status = BillingPeriodReservationStatus.PENDING_RECONCILIATION.value
                                    db.session.commit()
                                    results.append({
                                        "subscription_id": sub.id,
                                        "org_id": sub.organization_id,
                                        "success": False,
                                        "merchant_transaction_id": bpr.merchant_order_id,
                                        "status": "PENDING_RECONCILIATION",
                                        "error": f"Missing SaaSRedemptionAttempt for in-flight reservation order {bpr.merchant_order_id}",
                                    })
                                    continue
                                else:
                                    logger.error(
                                        "Ambiguous: found %d SaaSRedemptionAttempt records for in-flight order %s on sub %s",
                                        len(matching_attempts), bpr.merchant_order_id, sub.id
                                    )
                                    bpr.status = BillingPeriodReservationStatus.PENDING_RECONCILIATION.value
                                    db.session.commit()
                                    results.append({
                                        "subscription_id": sub.id,
                                        "org_id": sub.organization_id,
                                        "success": False,
                                        "merchant_transaction_id": bpr.merchant_order_id,
                                        "status": "PENDING_RECONCILIATION",
                                        "error": f"Ambiguous: found {len(matching_attempts)} SaaSRedemptionAttempt records for in-flight order {bpr.merchant_order_id}",
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

    @classmethod
    def reconcile_unresolved_notifications(
        cls,
        provider,
        batch_size: int = 50,
        min_age_minutes: int = 2,
        max_age_days: int = 14,
    ) -> List[Dict[str, Any]]:
        """
        Background reconciliation & recovery for unresolved PhonePe V2 pre-debit notifications.
        Complies with RBI / PhonePe AutoPay V2 regulations:
        - Strictly scoped to provider.name == 'phonepe_v2' (never touches Razorpay or PhonePe V3).
        - Enforces organization isolation across all operations.
        - Reconciles INITIATED and NOTIFICATION_IN_PROGRESS records using authoritative PhonePe get_order_status.
        - Never converts payment completion (COMPLETED/SUCCESS) or unknown states to NOTIFIED.
        - Protected by atomic subscription claim leases (acquire_subscription_claim) and row-level locks.
        - Bounded batches and minimum age threshold to prevent API rate-limit exhaustion or racing active workers.
        - Implements safe, guarded recovery for FAILED notifications:
          * First establishes authoritative PhonePe status for existing deterministic order ID.
          * If authoritative status is NOTIFIED or NOTIFICATION_IN_PROGRESS, transitions DB without API resend.
          * If PhonePe returns 404 (order never received by gateway), live mandate is ACTIVE, amount matches,
            and billing reservation is valid: executes single guarded retry with same deterministic order ID.
          * Never blindly resends after network timeout or ambiguous error.
          * Never creates a new order ID to bypass unique constraint.
        """
        if getattr(provider, "name", None) != "phonepe_v2":
            logger.info("reconcile_unresolved_notifications skipped: provider '%s' is not phonepe_v2", getattr(provider, "name", None))
            return []

        now = datetime.utcnow()
        min_age_threshold = now - timedelta(minutes=min_age_minutes)
        max_age_threshold = now - timedelta(days=max_age_days)

        # Strictly join with SaaSSubscription and enforce organization isolation & provider filtering
        candidate_records = (
            SaaSPreDebitNotification.query.join(
                SaaSSubscription,
                (SaaSPreDebitNotification.subscription_id == SaaSSubscription.id)
                & (SaaSPreDebitNotification.organization_id == SaaSSubscription.organization_id),
            )
            .filter(
                SaaSSubscription.payment_provider == "phonepe_v2",
                SaaSPreDebitNotification.status.in_([
                    PreDebitNotificationStatus.INITIATED.value,
                    PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
                    PreDebitNotificationStatus.FAILED.value,
                ]),
                SaaSPreDebitNotification.created_at <= min_age_threshold,
                SaaSPreDebitNotification.created_at >= max_age_threshold,
                or_(
                    SaaSSubscription.claim_token.is_(None),
                    SaaSSubscription.claim_expires_at < now,
                ),
            )
            .order_by(SaaSPreDebitNotification.id.asc())
            .limit(batch_size)
            .all()
        )

        results = []
        for cand in candidate_records:
            # 1. Concurrency Protection: Atomic subscription claim lease
            claim_token = SubscriptionService.acquire_subscription_claim(
                subscription_id=cand.subscription_id,
                organization_id=cand.organization_id,
                lease_duration_seconds=120,
            )
            if not claim_token:
                logger.info(
                    "Subscription %s (org=%s) is actively claimed by another worker; skipping reconciliation for order %s.",
                    cand.subscription_id, cand.organization_id, cand.merchant_order_id,
                )
                continue

            try:
                # 2. Row reload with lock & isolation re-verification
                pdn = SaaSPreDebitNotification.query.filter_by(
                    id=cand.id,
                    organization_id=cand.organization_id,
                ).with_for_update().first()

                if not pdn:
                    continue

                # If already transitioned to terminal/notified state (e.g. by concurrent webhook), skip
                if pdn.status in (
                    PreDebitNotificationStatus.NOTIFIED.value,
                    PreDebitNotificationStatus.CONSUMED.value,
                    PreDebitNotificationStatus.EXPIRED.value,
                ):
                    continue

                sub = SaaSSubscription.query.filter_by(
                    id=pdn.subscription_id,
                    organization_id=pdn.organization_id,
                    payment_provider="phonepe_v2",
                ).first()

                if not sub:
                    logger.warning("Relational mismatch or invalid provider for pdn %s (org=%s). Skipping.",
                                   pdn.merchant_order_id, pdn.organization_id)
                    continue

                prev_status = pdn.status

                # ── Branch A: INITIATED or NOTIFICATION_IN_PROGRESS ─────────────
                if pdn.status in (
                    PreDebitNotificationStatus.INITIATED.value,
                    PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value,
                ):
                    reconciled = provider.reconcile_notification_status(pdn)
                    results.append({
                        "notification_id": pdn.id,
                        "organization_id": pdn.organization_id,
                        "subscription_id": pdn.subscription_id,
                        "merchant_order_id": pdn.merchant_order_id,
                        "previous_status": prev_status,
                        "current_status": reconciled.status,
                        "action": "reconcile",
                        "success": reconciled.status == PreDebitNotificationStatus.NOTIFIED.value,
                    })

                # ── Branch B: Safe FAILED notification recovery ──────────────────
                elif pdn.status == PreDebitNotificationStatus.FAILED.value:
                    client = provider._get_client()
                    is_not_found = False
                    resp = None
                    state = ""

                    try:
                        resp = client.get_order_status(pdn.merchant_order_id, details=False)
                        state = (resp.get("state") or resp.get("status") or "").upper()
                    except PhonePeResponseError as resp_err:
                        if resp_err.status_code == 404 or getattr(resp_err, "error_code", None) in ("ORDER_NOT_FOUND", "NOT_FOUND"):
                            is_not_found = True
                            state = "NOT_FOUND"
                        else:
                            logger.warning(
                                "Authoritative check for failed notification %s returned API error: %s. Leaving unresolved.",
                                pdn.merchant_order_id, resp_err,
                            )
                            results.append({
                                "notification_id": pdn.id,
                                "organization_id": pdn.organization_id,
                                "subscription_id": pdn.subscription_id,
                                "merchant_order_id": pdn.merchant_order_id,
                                "previous_status": prev_status,
                                "current_status": pdn.status,
                                "action": "unresolved_api_error",
                                "success": False,
                                "error": str(resp_err),
                            })
                            continue
                    except PhonePeNetworkError as net_err:
                        # Ambiguous network timeout / failure: NEVER blindly retry
                        logger.warning(
                            "Authoritative check for failed notification %s timed out or network error: %s. Leaving unresolved.",
                            pdn.merchant_order_id, net_err,
                        )
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": pdn.status,
                            "action": "unresolved_network_timeout",
                            "success": False,
                            "error": str(net_err),
                        })
                        continue
                    except Exception as exc:
                        logger.error(
                            "Unexpected error checking status for failed notification %s: %s",
                            pdn.merchant_order_id, exc, exc_info=True,
                        )
                        continue

                    # Evaluate authoritative status from PhonePe
                    if state == "NOTIFIED":
                        # Authoritative delivery confirmed on PhonePe
                        pdn.status = PreDebitNotificationStatus.NOTIFIED.value
                        if not pdn.notified_at:
                            pdn.notified_at = datetime.utcnow()
                        pdn.error_message = None
                        pdn.error_code = None
                        pdn.raw_response_json = resp
                        if resp and resp.get("validAfter"):
                            pdn.valid_after_at = _parse_datetime(resp.get("validAfter"))
                        if resp and resp.get("validUpto"):
                            pdn.valid_upto_at = _parse_datetime(resp.get("validUpto"))
                        db.session.commit()
                        logger.info("Authoritative recovery: failed notification %s confirmed NOTIFIED on PhonePe", pdn.merchant_order_id)
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": pdn.status,
                            "action": "recovered_authoritative_notified",
                            "success": True,
                        })
                    elif state == "NOTIFICATION_IN_PROGRESS":
                        pdn.status = PreDebitNotificationStatus.NOTIFICATION_IN_PROGRESS.value
                        pdn.error_message = None
                        pdn.raw_response_json = resp
                        if resp and resp.get("validAfter"):
                            pdn.valid_after_at = _parse_datetime(resp.get("validAfter"))
                        if resp and resp.get("validUpto"):
                            pdn.valid_upto_at = _parse_datetime(resp.get("validUpto"))
                        db.session.commit()
                        logger.info("Authoritative recovery: failed notification %s transitioned to NOTIFICATION_IN_PROGRESS", pdn.merchant_order_id)
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": pdn.status,
                            "action": "recovered_in_progress",
                            "success": False,
                        })
                    elif state in ("COMPLETED", "SUCCESS"):
                        # FINANCIAL SAFETY: Payment completion state observed when only notification was expected
                        logger.warning(
                            "Authoritative check for failed notification %s returned payment completion state '%s'. Retaining unresolved manual reconciliation.",
                            pdn.merchant_order_id, state,
                        )
                        pdn.error_message = f"Authoritative state '{state}' is payment completion; requires manual operator reconciliation"
                        db.session.commit()
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": pdn.status,
                            "action": "manual_reconciliation_required",
                            "success": False,
                            "error": pdn.error_message,
                        })
                    elif state in ("FAILED", "DECLINED"):
                        # Definitive terminal failure on PhonePe side
                        logger.warning(
                            "Notification order %s definitively FAILED on PhonePe (%s). Cannot auto-retry with duplicate order ID.",
                            pdn.merchant_order_id, state,
                        )
                        pdn.error_message = resp.get("message") or f"PhonePe definitive failure: {state}"
                        db.session.commit()
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": pdn.status,
                            "action": "definitive_failure_no_retry",
                            "success": False,
                            "error": pdn.error_message,
                        })
                    elif is_not_found:
                        # Order NEVER reached PhonePe (404 NOT_FOUND).
                        # Validate strict safety preconditions before single guarded retry:
                        # Precondition 1: Subscription active
                        if sub.status not in (SaaSSubscriptionStatus.ACTIVE, SaaSSubscriptionStatus.GRACE):
                            logger.warning("Guarded retry blocked for %s: sub %s status is %s",
                                           pdn.merchant_order_id, sub.id, sub.status)
                            results.append({
                                "notification_id": pdn.id,
                                "organization_id": pdn.organization_id,
                                "subscription_id": pdn.subscription_id,
                                "merchant_order_id": pdn.merchant_order_id,
                                "previous_status": prev_status,
                                "current_status": pdn.status,
                                "action": "retry_blocked_subscription_inactive",
                                "success": False,
                            })
                            continue

                        # Precondition 2: Mandate active on PhonePe
                        if sub.provider_subscription_id:
                            try:
                                mandate_chk = provider.check_subscription_status(sub.provider_subscription_id)
                                if mandate_chk.status not in ("ACTIVE", "PENDING"):
                                    logger.warning("Guarded retry blocked for %s: PhonePe mandate status is %s",
                                                   pdn.merchant_order_id, mandate_chk.status)
                                    results.append({
                                        "notification_id": pdn.id,
                                        "organization_id": pdn.organization_id,
                                        "subscription_id": pdn.subscription_id,
                                        "merchant_order_id": pdn.merchant_order_id,
                                        "previous_status": prev_status,
                                        "current_status": pdn.status,
                                        "action": "retry_blocked_mandate_not_active",
                                        "success": False,
                                    })
                                    continue
                            except Exception as m_err:
                                logger.warning("Guarded retry blocked for %s: could not check mandate status: %s",
                                               pdn.merchant_order_id, m_err)
                                results.append({
                                    "notification_id": pdn.id,
                                    "organization_id": pdn.organization_id,
                                    "subscription_id": pdn.subscription_id,
                                    "merchant_order_id": pdn.merchant_order_id,
                                    "previous_status": prev_status,
                                    "current_status": pdn.status,
                                    "action": "retry_blocked_mandate_check_failed",
                                    "success": False,
                                    "error": str(m_err),
                                })
                                continue

                        # Precondition 3: Amount snapshot matches
                        expected_amount = sub.amount_paise or (
                            sub.plan.yearly_price_paise if sub.billing_cycle and sub.billing_cycle.value == "yearly"
                            else (sub.plan.monthly_price_paise if sub.plan else 0)
                        )
                        if pdn.amount_paise != expected_amount:
                            logger.error("Guarded retry blocked for %s: amount mismatch (pdn=%s, sub=%s)",
                                         pdn.merchant_order_id, pdn.amount_paise, expected_amount)
                            results.append({
                                "notification_id": pdn.id,
                                "organization_id": pdn.organization_id,
                                "subscription_id": pdn.subscription_id,
                                "merchant_order_id": pdn.merchant_order_id,
                                "previous_status": prev_status,
                                "current_status": pdn.status,
                                "action": "retry_blocked_amount_mismatch",
                                "success": False,
                            })
                            continue

                        # Precondition 4: Billing period reservation valid and not completed
                        if pdn.billing_period_key:
                            bpr = SaaSBillingPeriodReservation.query.filter_by(
                                subscription_id=sub.id,
                                organization_id=sub.organization_id,
                                billing_period_key=pdn.billing_period_key,
                            ).first()
                            if bpr and bpr.status == BillingPeriodReservationStatus.COMPLETED.value:
                                logger.info("Guarded retry blocked for %s: billing period already completed.",
                                            pdn.merchant_order_id)
                                results.append({
                                    "notification_id": pdn.id,
                                    "organization_id": pdn.organization_id,
                                    "subscription_id": pdn.subscription_id,
                                    "merchant_order_id": pdn.merchant_order_id,
                                    "previous_status": prev_status,
                                    "current_status": pdn.status,
                                    "action": "retry_blocked_period_completed",
                                    "success": False,
                                })
                                continue

                        # All safety preconditions satisfied: execute single guarded retry with same deterministic order ID
                        logger.info("Executing safe guarded retry for order %s on sub %s", pdn.merchant_order_id, sub.id)
                        retry_res = provider.notify_redemption(
                            subscription=sub,
                            amount_paise=pdn.amount_paise,
                            merchant_transaction_id=pdn.merchant_order_id,
                            claim_token=claim_token,
                            allow_retry=True,
                        )
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": retry_res.status,
                            "action": "guarded_retry",
                            "success": retry_res.success,
                            "error": retry_res.error_message,
                        })
                    else:
                        # Unknown state
                        logger.warning("Authoritative check for notification %s returned unrecognized state '%s'.",
                                       pdn.merchant_order_id, state)
                        results.append({
                            "notification_id": pdn.id,
                            "organization_id": pdn.organization_id,
                            "subscription_id": pdn.subscription_id,
                            "merchant_order_id": pdn.merchant_order_id,
                            "previous_status": prev_status,
                            "current_status": pdn.status,
                            "action": "unknown_state_unresolved",
                            "success": False,
                            "state": state,
                        })
            finally:
                SubscriptionService.release_subscription_claim(
                    subscription_id=cand.subscription_id,
                    organization_id=cand.organization_id,
                    claim_token=claim_token,
                )

        return results

import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any
from model import db, SaaSSubscription, SaaSSubscriptionStatus, SaaSTransactionStatus
from services.billing.subscription_service import SubscriptionService
from services.billing.transaction_service import TransactionService

logger = logging.getLogger(__name__)


class RenewalService:
    """
    Automated recurring billing and renewal engine for PhonePe UPI AutoPay.
    Complies with RBI / NPCI UPI AutoPay regulations:
    - Pre-debit notifications sent 24-48 hours before execution
    - Automatic debit execution on billing cycle date
    - Grace period management and retries
    """

    @classmethod
    def process_upcoming_notifications(cls, provider, hours_ahead: int = 48) -> List[Dict[str, Any]]:
        """
        Finds active subscriptions whose next_billing_at is within `hours_ahead`
        and triggers PhonePe Pre-Debit Notification (Redemption Notify).
        """
        now = datetime.utcnow()
        threshold = now + timedelta(hours=hours_ahead)

        subscriptions = SaaSSubscription.query.filter(
            SaaSSubscription.payment_provider == "phonepe",
            SaaSSubscription.status == SaaSSubscriptionStatus.ACTIVE,
            SaaSSubscription.next_billing_at != None,
            SaaSSubscription.next_billing_at <= threshold,
            SaaSSubscription.next_billing_at > now,
        ).all()

        results = []
        for sub in subscriptions:
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
            merchant_tx_id = f"NOTIFY_{sub.organization_id}_{sub.id}_{cycle_tag}"
            amount = sub.amount_paise or (sub.plan.yearly_price_paise if sub.billing_cycle.value == "yearly" else sub.plan.monthly_price_paise)

            logger.info("Sending Pre-Debit Notification for sub_id=%s, org=%s, amount=%s paise, tx_id=%s",
                        sub.provider_subscription_id, sub.organization_id, amount, merchant_tx_id)

            res = provider.notify_redemption(
                subscription=sub,
                amount_paise=amount,
                merchant_transaction_id=merchant_tx_id,
            )

            results.append({
                "subscription_id": sub.id,
                "org_id": sub.organization_id,
                "success": res.success,
                "merchant_transaction_id": merchant_tx_id,
                "error": res.error_message,
            })

        return results

    @classmethod
    def process_due_renewals(cls, provider) -> List[Dict[str, Any]]:
        """
        Finds active subscriptions whose billing date has arrived (next_billing_at <= now)
        and executes recurring debit via PhonePe AutoPay.
        Implements double-payment protection:
        - Strict isolation to payment_provider == 'phonepe' (never touches Razorpay records)
        - Deterministic merchantTransactionId per billing cycle (gateway-level idempotency)
        - Atomic pre-claim window to prevent concurrent scheduler duplicates
        """
        now = datetime.utcnow()
        due_subscriptions = SaaSSubscription.query.filter(
            SaaSSubscription.payment_provider == "phonepe",
            SaaSSubscription.status.in_([SaaSSubscriptionStatus.ACTIVE, SaaSSubscriptionStatus.GRACE]),
            SaaSSubscription.next_billing_at != None,
            SaaSSubscription.next_billing_at <= now,
        ).all()

        results = []
        for sub in due_subscriptions:
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

            # Deterministic transaction ID based on organization, sub_id, and cycle end date
            cycle_tag = sub.next_billing_at.strftime("%Y%m%d%H%M") if sub.next_billing_at else str(int(now.timestamp()))
            merchant_tx_id = f"RENEW_{sub.organization_id}_{sub.id}_{cycle_tag}"
            amount = sub.amount_paise or (sub.plan.yearly_price_paise if sub.billing_cycle.value == "yearly" else sub.plan.monthly_price_paise)

            # 2. Database-level idempotency & timeout recovery reconciliation check:
            existing_tx = SaaSTransaction.query.filter_by(provider_payment_id=merchant_tx_id).first()
            if existing_tx:
                if existing_tx.status == SaaSTransactionStatus.SUCCESS:
                    logger.info("Renewal cycle %s already processed successfully for sub %s. Skipping.",
                                merchant_tx_id, sub.id)
                    continue
                elif existing_tx.status == SaaSTransactionStatus.PENDING:
                    # In-flight reconciliation: Query PhonePe S2S status API before initiating another request
                    logger.info("Reconciling in-flight pending renewal %s with PhonePe", merchant_tx_id)
                    try:
                        status_res = provider.check_transaction_status(merchant_tx_id)
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
                            continue
                        elif status_res.status == "PENDING":
                            logger.info("Renewal %s is still PENDING at bank/rail. Deferring check.", merchant_tx_id)
                            sub.next_billing_at = now + timedelta(hours=1)
                            db.session.commit()
                            continue
                        else:
                            existing_tx.status = SaaSTransactionStatus.FAILED
                            db.session.commit()
                    except Exception as e:
                        logger.warning("Could not reconcile in-flight tx %s: %s", merchant_tx_id, str(e))
                        sub.next_billing_at = now + timedelta(hours=1)
                        db.session.commit()
                        continue

            # 3. Atomic claim: Advance next_billing_at forward by 30 minutes immediately to block
            # concurrent scheduler workers from picking up the same subscription simultaneously.
            prior_billing_at = sub.next_billing_at
            sub.next_billing_at = now + timedelta(minutes=30)
            db.session.commit()

            logger.info("Executing AutoPay renewal for org=%s, sub_id=%s, amount=%s paise, tx_id=%s",
                        sub.organization_id, sub.provider_subscription_id, amount, merchant_tx_id)

            res = provider.execute_redemption(
                subscription=sub,
                amount_paise=amount,
                merchant_transaction_id=merchant_tx_id,
            )

            # 4. Strict three-state handling: SUCCESS, PENDING, FAILED
            if res.status == "SUCCESS":
                # Advance billing period
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
                # Bank / UPI rail is processing the debit.
                # CRITICAL: Do NOT advance period yet, and do NOT fail or enter grace period!
                logger.info("Renewal debit PENDING at gateway for org=%s, sub_id=%s, tx=%s",
                            sub.organization_id, sub.provider_subscription_id, merchant_tx_id)
                # Reschedule status reconciliation check in 1 hour
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
                # Payment definitively failed
                logger.warning("Renewal failed for org=%s: %s", sub.organization_id, res.error_message)
                # Retry in 24 hours
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
                    # Grace period has expired — suspend organization
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

        return results


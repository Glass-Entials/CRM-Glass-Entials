import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dateutil.relativedelta import relativedelta
from sqlalchemy import or_
from sqlalchemy.orm import Query
from model import (
    db,
    Plan,
    Organization,
    OrganizationStatus,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSTransactionStatus,
)
from services.billing.payment_provider import MandateSetupResult, VerificationResult
from services.billing.transaction_service import TransactionService

logger = logging.getLogger(__name__)


class SubscriptionService:
    """Core domain service for SaaS subscriptions, decoupled from any single payment gateway."""

    @staticmethod
    def calculate_period_dates(billing_cycle: str, start_date: Optional[datetime] = None) -> Tuple[datetime, datetime]:
        """Calculates current period start and end dates based on cycle."""
        start = start_date or datetime.utcnow()
        cycle_str = billing_cycle.lower() if isinstance(billing_cycle, str) else billing_cycle.value
        if cycle_str == "yearly":
            end = start + relativedelta(years=1)
        else:
            end = start + relativedelta(months=1)
        return start, end

    @staticmethod
    def get_or_create_subscription(org: Organization, plan: Plan) -> SaaSSubscription:
        """Finds or creates a SaaSSubscription for an organization."""
        sub = SaaSSubscription.query.filter_by(organization_id=org.id).first()
        if not sub:
            sub = SaaSSubscription(
                organization_id=org.id,
                plan_id=plan.id,
                status=SaaSSubscriptionStatus.PENDING,
                billing_cycle=SaaSBillingCycle.MONTHLY,
                payment_provider="phonepe",
            )
            db.session.add(sub)
            db.session.commit()
        return sub

    @classmethod
    def initiate_subscription(
        cls,
        org: Organization,
        plan: Plan,
        billing_cycle: str,
        user,
        provider,
        return_url: str,
        callback_url: str
    ) -> MandateSetupResult:
        """
        Coordinates mandate setup with the configured payment provider.
        """
        sub = cls.get_or_create_subscription(org, plan)
        cycle_enum = SaaSBillingCycle.YEARLY if billing_cycle.lower() == "yearly" else SaaSBillingCycle.MONTHLY
        sub.billing_cycle = cycle_enum
        sub.plan_id = plan.id
        sub.payment_provider = provider.name
        db.session.commit()

        # Delegate mandate creation to provider
        setup_result = provider.create_subscription_mandate(
            subscription=sub,
            plan=plan,
            billing_cycle=billing_cycle,
            user=user,
            return_url=return_url,
            callback_url=callback_url,
        )

        if setup_result.success:
            sub.provider_subscription_id = setup_result.provider_subscription_id or setup_result.merchant_subscription_id
            sub.provider_order_id = setup_result.merchant_transaction_id
            sub.provider_customer_reference = f"USR_{user.id}"
            db.session.commit()
            logger.info("Initiated subscription mandate for org=%s with sub_id=%s",
                        org.id, sub.provider_subscription_id)

        return setup_result

    @classmethod
    def activate_subscription(
        cls,
        subscription: SaaSSubscription,
        provider_subscription_id: Optional[str] = None,
        provider_transaction_id: Optional[str] = None,
        provider_payment_id: Optional[str] = None,
        amount_paise: int = 0,
        mandate_reference: Optional[str] = None,
        payment_method: str = "UPI_AUTOPAY"
    ) -> bool:
        """
        Activates an organization's subscription upon verified mandate & initial payment.
        Calculates billing period dates and records transaction.
        """
        try:
            start_date, end_date = cls.calculate_period_dates(subscription.billing_cycle.value)
            
            subscription.status = SaaSSubscriptionStatus.ACTIVE
            subscription.current_period_start = start_date
            subscription.current_period_end = end_date
            subscription.next_billing_at = end_date
            subscription.grace_period_end = None
            subscription.cancelled_at = None
            subscription.cancellation_reason = None

            if provider_subscription_id:
                subscription.provider_subscription_id = provider_subscription_id
            if provider_transaction_id:
                subscription.provider_transaction_id = provider_transaction_id
            if mandate_reference:
                subscription.mandate_reference = mandate_reference
            if amount_paise > 0:
                subscription.amount_paise = amount_paise

            # Organization activation
            org = subscription.organization
            if org:
                org.status = OrganizationStatus.ACTIVE

            db.session.commit()

            # Record success transaction
            payment_id = provider_payment_id or provider_transaction_id or f"pay_{subscription.id}_{int(datetime.utcnow().timestamp())}"
            TransactionService.record_transaction(
                organization_id=subscription.organization_id,
                subscription_id=subscription.id,
                payment_provider=subscription.payment_provider,
                amount_paise=amount_paise or subscription.amount_paise,
                status=SaaSTransactionStatus.SUCCESS,
                provider_payment_id=payment_id,
                provider_transaction_id=provider_transaction_id,
                provider_subscription_id=subscription.provider_subscription_id,
                payment_method=payment_method,
                billing_period_start=start_date,
                billing_period_end=end_date,
            )

            logger.info("Successfully activated SaaS subscription org=%s, next_billing=%s",
                        subscription.organization_id, end_date)
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Error activating SaaS subscription: %s", str(e), exc_info=True)
            return False

    @staticmethod
    def suspend_subscription(subscription: SaaSSubscription, reason: str = "") -> bool:
        """Suspends subscription due to mandate pause or payment failure."""
        try:
            subscription.status = SaaSSubscriptionStatus.SUSPENDED
            subscription.cancellation_reason = reason
            org = subscription.organization
            if org:
                org.status = OrganizationStatus.SUSPENDED
                org.suspension_reason = reason
            db.session.commit()
            logger.info("Suspended subscription org=%s reason=%s", subscription.organization_id, reason)
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Error suspending subscription: %s", str(e), exc_info=True)
            return False

    @staticmethod
    def cancel_subscription(subscription: SaaSSubscription, reason: str = "") -> bool:
        """Cancels subscription and marks organization cancelled."""
        try:
            subscription.status = SaaSSubscriptionStatus.CANCELLED
            subscription.cancelled_at = datetime.utcnow()
            subscription.cancellation_reason = reason
            org = subscription.organization
            if org:
                org.status = OrganizationStatus.CANCELLED
            db.session.commit()
            logger.info("Cancelled subscription org=%s reason=%s", subscription.organization_id, reason)
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Error cancelling subscription: %s", str(e), exc_info=True)
            return False

    @staticmethod
    def enter_grace_period(subscription: SaaSSubscription, days: int = 7) -> bool:
        """Enters grace period upon failed recurring charge."""
        try:
            subscription.status = SaaSSubscriptionStatus.GRACE
            subscription.grace_period_end = datetime.utcnow() + timedelta(days=days)
            org = subscription.organization
            if org:
                org.status = OrganizationStatus.GRACE
            db.session.commit()
            logger.info("Org=%s entered grace period until %s", subscription.organization_id, subscription.grace_period_end)
            return True
        except Exception as e:
            db.session.rollback()
            logger.error("Error entering grace period: %s", str(e), exc_info=True)
            return False

    @classmethod
    def acquire_subscription_claim(
        cls,
        subscription_id: int,
        organization_id: int,
        lease_duration_seconds: int = 300,
        existing_claim_token: Optional[str] = None,
    ) -> Optional[str]:
        """
        Database-level atomic lease claim for recurring subscription operations.
        Prevents concurrent workers or competing processes from running operations
        simultaneously on the same subscription.

        If existing_claim_token is provided (e.g., from an enclosing scheduler job),
        validates active ownership and safely extends the lease without nested conflict.

        Executes an atomic conditional UPDATE:
        UPDATE saas_subscription
        SET claim_token = :token, claim_expires_at = :expires_at
        WHERE id = :sub_id AND organization_id = :org_id
          AND (claim_token IS NULL OR claim_expires_at < :now OR claim_token = :existing_claim_token)
        """
        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=lease_duration_seconds)
        claim_token = existing_claim_token or f"claim_{uuid.uuid4().hex}"

        try:
            filters = [
                SaaSSubscription.id == subscription_id,
                SaaSSubscription.organization_id == organization_id,
            ]
            if existing_claim_token:
                filters.append(
                    or_(
                        SaaSSubscription.claim_token.is_(None),
                        SaaSSubscription.claim_expires_at < now,
                        SaaSSubscription.claim_token == existing_claim_token,
                    )
                )
            else:
                filters.append(
                    or_(
                        SaaSSubscription.claim_token.is_(None),
                        SaaSSubscription.claim_expires_at < now,
                    )
                )

            query = SaaSSubscription.query.filter(*filters)
            if isinstance(query, Query):
                rows = query.update(
                    {
                        SaaSSubscription.claim_token: claim_token,
                        SaaSSubscription.claim_expires_at: expires_at,
                    },
                    synchronize_session=False,
                )
                db.session.commit()
                if rows > 0:
                    logger.info("Acquired atomic subscription claim org=%s sub=%s token=%s (expires=%s)",
                                organization_id, subscription_id, claim_token, expires_at)
                    return claim_token
                else:
                    logger.warning("Failed to acquire atomic subscription claim org=%s sub=%s: lease actively held",
                                   organization_id, subscription_id)
                    return None
            else:
                sub = query.first()
                if sub and (not getattr(sub, "claim_token", None) or getattr(sub, "claim_expires_at", None) < now or getattr(sub, "claim_token", None) == existing_claim_token):
                    sub.claim_token = claim_token
                    sub.claim_expires_at = expires_at
                    try:
                        db.session.commit()
                    except Exception:
                        pass
                    return claim_token
                return None
        except Exception as exc:
            db.session.rollback()
            logger.error("Error acquiring subscription claim org=%s sub=%s: %s",
                         organization_id, subscription_id, str(exc), exc_info=True)
            raise

    @classmethod
    def release_subscription_claim(
        cls,
        subscription_id: int,
        organization_id: int,
        claim_token: Optional[str],
    ) -> bool:
        """Releases atomic lease claim if token matches."""
        if not claim_token:
            return False
        try:
            query = SaaSSubscription.query.filter(
                SaaSSubscription.id == subscription_id,
                SaaSSubscription.organization_id == organization_id,
                SaaSSubscription.claim_token == claim_token,
            )
            if isinstance(query, Query):
                rows = query.update(
                    {
                        SaaSSubscription.claim_token: None,
                        SaaSSubscription.claim_expires_at: None,
                    },
                    synchronize_session=False,
                )
                db.session.commit()
                logger.info("Released atomic subscription claim org=%s sub=%s token=%s",
                            organization_id, subscription_id, claim_token)
                return rows > 0
            else:
                sub = query.first()
                if sub and getattr(sub, "claim_token", None) == claim_token:
                    sub.claim_token = None
                    sub.claim_expires_at = None
                    try:
                        db.session.commit()
                    except Exception:
                        pass
                    return True
                return False
        except Exception as exc:
            db.session.rollback()
            logger.error("Error releasing subscription claim org=%s sub=%s token=%s: %s",
                         organization_id, subscription_id, claim_token, str(exc), exc_info=True)
            return False

    @classmethod
    def renew_subscription_claim(
        cls,
        subscription_id: int,
        organization_id: int,
        claim_token: str,
        extend_seconds: int = 120,
    ) -> bool:
        """Safely extends an actively held lease if caller still owns the claim."""
        if not claim_token:
            return False
        now = datetime.utcnow()
        new_expires_at = now + timedelta(seconds=extend_seconds)
        try:
            query = SaaSSubscription.query.filter(
                SaaSSubscription.id == subscription_id,
                SaaSSubscription.organization_id == organization_id,
                SaaSSubscription.claim_token == claim_token,
                SaaSSubscription.claim_expires_at >= now,
            )
            if isinstance(query, Query):
                rows = query.update(
                    {SaaSSubscription.claim_expires_at: new_expires_at},
                    synchronize_session=False,
                )
                db.session.commit()
                return rows > 0
            else:
                sub = query.first()
                if sub and getattr(sub, "claim_token", None) == claim_token:
                    sub.claim_expires_at = new_expires_at
                    try:
                        db.session.commit()
                    except Exception:
                        pass
                    return True
                return False
        except Exception as exc:
            db.session.rollback()
            logger.error("Error extending subscription claim org=%s sub=%s token=%s: %s",
                         organization_id, subscription_id, claim_token, str(exc), exc_info=True)
            return False


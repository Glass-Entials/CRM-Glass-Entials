import logging
from typing import Optional
from datetime import datetime
from model import db, SaaSTransaction, SaaSTransactionStatus

logger = logging.getLogger(__name__)


class TransactionService:
    """Service to record and query SaaS subscription payment transactions."""

    @staticmethod
    def record_transaction(
        organization_id: int,
        subscription_id: Optional[int],
        payment_provider: str,
        amount_paise: int,
        status: SaaSTransactionStatus = SaaSTransactionStatus.PENDING,
        provider_payment_id: Optional[str] = None,
        provider_order_id: Optional[str] = None,
        provider_transaction_id: Optional[str] = None,
        provider_subscription_id: Optional[str] = None,
        payment_method: Optional[str] = "UPI_AUTOPAY",
        failure_reason: Optional[str] = None,
        billing_period_start: Optional[datetime] = None,
        billing_period_end: Optional[datetime] = None,
    ) -> SaaSTransaction:
        """Creates and stores a transaction record."""
        # Check for existing transaction by provider_payment_id to ensure idempotency
        if provider_payment_id:
            existing = SaaSTransaction.query.filter_by(provider_payment_id=provider_payment_id).first()
            if existing:
                existing.status = status
                if failure_reason:
                    existing.failure_reason = failure_reason
                db.session.commit()
                return existing

        tx = SaaSTransaction(
            organization_id=organization_id,
            subscription_id=subscription_id,
            payment_provider=payment_provider,
            provider_payment_id=provider_payment_id,
            provider_order_id=provider_order_id,
            provider_transaction_id=provider_transaction_id,
            provider_subscription_id=provider_subscription_id,
            # For backward compatibility with legacy fields:
            razorpay_payment_id=provider_payment_id if payment_provider == "razorpay" else None,
            razorpay_subscription_id=provider_subscription_id if payment_provider == "razorpay" else None,
            amount_paise=amount_paise,
            currency="INR",
            status=status,
            payment_method=payment_method,
            failure_reason=failure_reason,
            billing_period_start=billing_period_start,
            billing_period_end=billing_period_end,
            created_at=datetime.utcnow(),
        )

        db.session.add(tx)
        try:
            db.session.commit()
            logger.info("Recorded SaaS transaction id=%s for org=%s amount=%s paise status=%s",
                        tx.id, organization_id, amount_paise, status.value)
        except Exception as e:
            db.session.rollback()
            logger.error("Error committing SaaS transaction: %s", str(e), exc_info=True)
            raise

        return tx

    @staticmethod
    def get_by_provider_payment_id(provider_payment_id: str) -> Optional[SaaSTransaction]:
        """Finds a transaction by provider payment ID."""
        return SaaSTransaction.query.filter(
            (SaaSTransaction.provider_payment_id == provider_payment_id) |
            (SaaSTransaction.razorpay_payment_id == provider_payment_id)
        ).first()

    @staticmethod
    def get_org_transactions(organization_id: int):
        """Returns all SaaS transactions for an organization."""
        return (
            SaaSTransaction.query.filter_by(organization_id=organization_id)
            .order_by(SaaSTransaction.created_at.desc())
            .all()
        )


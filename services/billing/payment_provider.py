from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class MandateSetupResult:
    """Result of initializing an AutoPay mandate / subscription."""
    success: bool
    provider: str
    merchant_subscription_id: str
    provider_subscription_id: Optional[str] = None
    merchant_transaction_id: Optional[str] = None
    provider_transaction_id: Optional[str] = None
    redirect_url: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


@dataclass
class VerificationResult:
    """Result of verifying a payment / mandate setup."""
    success: bool
    status: str  # ACTIVE, PENDING, FAILED, etc.
    merchant_subscription_id: str
    provider_subscription_id: Optional[str] = None
    provider_transaction_id: Optional[str] = None
    provider_payment_id: Optional[str] = None
    mandate_reference: Optional[str] = None
    amount_paise: int = 0
    raw_response: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


@dataclass
class RedemptionResult:
    """Result of recurring debit notify or execute."""
    success: bool
    status: str  # SUCCESS, PENDING, FAILED
    merchant_transaction_id: str
    provider_transaction_id: Optional[str] = None
    provider_payment_id: Optional[str] = None
    amount_paise: int = 0
    raw_response: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


@dataclass
class WebhookResult:
    """Result of parsing and authenticating a webhook callback."""
    is_valid: bool
    event_id: str
    event_type: str
    merchant_subscription_id: Optional[str] = None
    provider_subscription_id: Optional[str] = None
    provider_payment_id: Optional[str] = None
    amount_paise: int = 0
    state: Optional[str] = None
    raw_payload: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class PaymentProvider(ABC):
    """
    Abstract Base Class for SaaS billing payment providers.
    Decouples CRM SaaS subscription logic from the underlying gateway.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider identifier (e.g. 'phonepe', 'razorpay')."""
        pass

    @abstractmethod
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
        Initiates a recurring AutoPay subscription mandate with the provider.
        Returns a MandateSetupResult with a customer redirect_url.
        """
        pass

    @abstractmethod
    def verify_payment(
        self,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        organization_id: Optional[int] = None,
    ) -> VerificationResult:
        """Verifies initial mandate payment authorization response."""
        pass

    @abstractmethod
    def check_subscription_status(
        self,
        merchant_subscription_id: str,
        organization_id: Optional[int] = None,
    ) -> VerificationResult:
        """Queries the provider's API for the current live status of a mandate."""
        pass

    @abstractmethod
    def check_transaction_status(
        self,
        merchant_transaction_id: str,
        organization_id: Optional[int] = None,
    ) -> VerificationResult:
        """Queries the provider's API for the status of a specific transaction."""
        pass

    @abstractmethod
    def notify_redemption(
        self,
        subscription,
        amount_paise: int,
        merchant_transaction_id: str,
        claim_token: Optional[str] = None,
    ) -> RedemptionResult:
        """
        Sends a Pre-Debit Notification (PDN) to the customer via PhonePe AutoPay.
        Required by RBI / UPI AutoPay at least 24 hours prior to recurring debit.
        """
        pass

    @abstractmethod
    def execute_redemption(
        self,
        subscription,
        amount_paise: int,
        merchant_transaction_id: str,
        claim_token: Optional[str] = None,
    ) -> RedemptionResult:
        """Executes a recurring debit against an established active AutoPay mandate."""
        pass

    @abstractmethod
    def cancel_subscription(self, subscription, reason: str = "") -> bool:
        """Cancels an active subscription mandate on the provider."""
        pass

    @abstractmethod
    def verify_webhook(self, payload_bytes: bytes, headers: Dict[str, str]) -> WebhookResult:
        """
        Verifies the cryptographic signature of an incoming webhook event,
        validates payload integrity, and extracts standard webhook details.
        """
        pass


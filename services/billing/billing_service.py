import logging
from typing import Optional
from flask import current_app
from services.billing.payment_provider import PaymentProvider
from services.billing.providers.phonepe_provider import PhonePeProvider

logger = logging.getLogger(__name__)

_providers = {}


class BillingService:
    """Factory and registry for billing payment providers."""

    @classmethod
    def get_provider(cls, name: Optional[str] = None) -> PaymentProvider:
        """
        Returns singleton instance of the payment provider.
        Defaults to 'phonepe' or the provider configured in BILLING_DEFAULT_PROVIDER.
        """
        provider_name = (name or current_app.config.get("BILLING_DEFAULT_PROVIDER", "phonepe")).lower()

        if provider_name in _providers:
            return _providers[provider_name]

        if provider_name == "phonepe":
            provider = PhonePeProvider()
            _providers["phonepe"] = provider
            return provider
        else:
            raise ValueError(f"Unsupported payment provider: {provider_name}")

    @classmethod
    def reset_provider_cache(cls):
        """Clears cached provider instances (useful in tests or config reloads)."""
        _providers.clear()


"""
services/oms/exceptions.py
Canonical exception hierarchy for GlassEntials Order Management System (OMS).
Provides single-source-of-truth exception classes across status engine,
order master, procurement, delivery, and financial reconciliation services.
"""

class OMSError(Exception):
    """Base exception for all Order Management System operations."""
    pass


class OMSValidationError(OMSError):
    """Raised when validation fails for an OMS entity, payload, or precondition."""
    pass


class OMSTransitionError(OMSError):
    """Raised when an illegal or terminal status transition is requested."""
    pass


class OMSInvariantError(OMSError):
    """Raised when an internal business invariant (quantity, allocation, finance) is violated."""
    pass


class OMSTenantIsolationError(OMSError):
    """Raised when cross-tenant access or multi-tenant boundaries are violated."""
    pass

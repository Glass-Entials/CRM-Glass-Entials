"""
Order Management System (OMS) Package.
Provides centralized services for complete order lifecycle management,
multi-vendor procurement, dispatch & delivery, installation visits, and financial reconciliation.
"""

class OMSError(Exception):
    """Base exception for Order Management System operations."""
    pass


class OMSValidationError(OMSError):
    """Raised when validation fails for an OMS entity or payload."""
    pass


class OMSTransitionError(OMSError):
    """Raised when an illegal status transition is requested."""
    pass


class OMSInvariantError(OMSError):
    """Raised when a business invariant (quantity, allocation, finance) is violated."""
    pass


class OMSTenantIsolationError(OMSError):
    """Raised when an entity from another organization/tenant is referenced."""
    pass


from .order_master_service import OrderMasterService
from .status_engine import OMSStatusEngine, STATUS_METADATA, VALID_MASTER_TRANSITIONS
from .procurement_service import ProcurementService
from .delivery_service import DeliveryService
from .financial_reconciliation_service import FinancialReconciliationService
from .document_service import OMSDocumentService
from .notification_service import OMSNotificationService
from .reporting_service import OMSReportingService
from .export_service import OMSExportService

__all__ = [
    "OMSError",
    "OMSValidationError",
    "OMSTransitionError",
    "OMSInvariantError",
    "OMSTenantIsolationError",
    "OrderMasterService",
    "OMSStatusEngine",
    "STATUS_METADATA",
    "VALID_MASTER_TRANSITIONS",
    "ProcurementService",
    "DeliveryService",
    "FinancialReconciliationService",
    "OMSDocumentService",
    "OMSNotificationService",
    "OMSReportingService",
    "OMSExportService",
]

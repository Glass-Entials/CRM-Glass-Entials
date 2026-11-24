"""
Order Management System (OMS) Package.
Provides centralized services for complete order lifecycle management,
multi-vendor procurement, dispatch & delivery, installation visits, and financial reconciliation.
"""

from .exceptions import (
    OMSError,
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)


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

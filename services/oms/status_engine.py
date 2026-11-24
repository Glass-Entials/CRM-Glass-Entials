"""
OMS Status Engine — Centralized, Authoritative Status Architecture.
Enforces all state machine transitions, child-to-parent aggregations,
and immutable status history auditing across the GlassEntials CRM Order Management System.
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, List, Optional, Set

from model import (
    db,
    OrderMaster,
    OrderItem,
    OrderItemVendorAllocation,
    OrderVendorProcurement,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    DeliveryItem,
    OrderFittingVisit,
    FittingVisitItem,
    OrderPaymentAllocation,
    OrderStatusHistory,
    OrderExceptionalClosure,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    OrderItemAllocationStatus,
    OrderConsignmentStatus,
    OrderFittingVisitStatus,
    VendorBillStatus,
    OrderClosureType,
)


class OMSTransitionError(Exception):
    """Raised when an illegal status transition is requested."""
    pass


class OMSValidationError(Exception):
    """Raised when status transition preconditions or inputs fail validation."""
    pass


class OMSTenantIsolationError(Exception):
    """Raised when cross-tenant access is attempted during a status operation."""
    pass


class OMSInvariantError(Exception):
    """Raised when an internal OMS invariant is violated."""
    pass


# ============================================================================
# STATE TRANSITION MATRICES
# ============================================================================

VALID_MASTER_TRANSITIONS: Dict[str, Set[str]] = {
    MasterOrderStatus.DRAFT.value: {
        MasterOrderStatus.CONFIRMED.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.CONFIRMED.value: {
        MasterOrderStatus.IN_PROCUREMENT.value,
        MasterOrderStatus.IN_DELIVERY.value,
        MasterOrderStatus.ON_HOLD.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.IN_PROCUREMENT.value: {
        MasterOrderStatus.PARTIALLY_RECEIVED.value,
        MasterOrderStatus.RECEIVED.value,
        MasterOrderStatus.IN_DELIVERY.value,
        MasterOrderStatus.ON_HOLD.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.PARTIALLY_RECEIVED.value: {
        MasterOrderStatus.RECEIVED.value,
        MasterOrderStatus.IN_DELIVERY.value,
        MasterOrderStatus.ON_HOLD.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.RECEIVED.value: {
        MasterOrderStatus.IN_DELIVERY.value,
        MasterOrderStatus.DELIVERED.value,
        MasterOrderStatus.ON_HOLD.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.IN_DELIVERY.value: {
        MasterOrderStatus.PARTIALLY_DELIVERED.value,
        MasterOrderStatus.DELIVERED.value,
        MasterOrderStatus.ON_HOLD.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.PARTIALLY_DELIVERED.value: {
        MasterOrderStatus.DELIVERED.value,
        MasterOrderStatus.IN_FITTING.value,
        MasterOrderStatus.ON_HOLD.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.DELIVERED.value: {
        MasterOrderStatus.IN_FITTING.value,
        MasterOrderStatus.COMPLETED.value,
        MasterOrderStatus.CLOSED.value,
        MasterOrderStatus.ON_HOLD.value,
    },
    MasterOrderStatus.IN_FITTING.value: {
        MasterOrderStatus.COMPLETED.value,
        MasterOrderStatus.CLOSED.value,
        MasterOrderStatus.ON_HOLD.value,
    },
    MasterOrderStatus.COMPLETED.value: {
        MasterOrderStatus.CLOSED.value,
    },
    MasterOrderStatus.ON_HOLD.value: {
        MasterOrderStatus.CONFIRMED.value,
        MasterOrderStatus.IN_PROCUREMENT.value,
        MasterOrderStatus.PARTIALLY_RECEIVED.value,
        MasterOrderStatus.RECEIVED.value,
        MasterOrderStatus.IN_DELIVERY.value,
        MasterOrderStatus.PARTIALLY_DELIVERED.value,
        MasterOrderStatus.DELIVERED.value,
        MasterOrderStatus.IN_FITTING.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.CLOSED.value: set(),  # Terminal state
    MasterOrderStatus.CANCELLED.value: set(),  # Terminal state
}

TERMINAL_MASTER_STATUSES = {
    MasterOrderStatus.COMPLETED.value,
    MasterOrderStatus.CLOSED.value,
    MasterOrderStatus.CANCELLED.value,
}

ACTIVE_MASTER_STATUSES = {
    MasterOrderStatus.CONFIRMED.value,
    MasterOrderStatus.IN_PROCUREMENT.value,
    MasterOrderStatus.PARTIALLY_RECEIVED.value,
    MasterOrderStatus.RECEIVED.value,
    MasterOrderStatus.IN_DELIVERY.value,
    MasterOrderStatus.PARTIALLY_DELIVERED.value,
    MasterOrderStatus.DELIVERED.value,
    MasterOrderStatus.IN_FITTING.value,
    MasterOrderStatus.ON_HOLD.value,
}


# ============================================================================
# CANONICAL STATUS METADATA (LABELS, BADGES, SEMANTICS)
# ============================================================================

STATUS_METADATA: Dict[str, Dict[str, Any]] = {
    # Master Order Lifecycle
    MasterOrderStatus.DRAFT.value: {
        "label": "Draft",
        "stage_label": "Stage 04: Order Drafted (Pending Confirmation)",
        "badge_class": "pb-gray",
        "color": "#64748b",
        "stage_num": 4,
        "is_terminal": False,
        "description": "Initial order draft created in OMS; pending confirmation.",
    },
    MasterOrderStatus.CONFIRMED.value: {
        "label": "Confirmed",
        "stage_label": "Stage 04: Order Confirmed (Ready for Procurement)",
        "badge_class": "pb-blue",
        "color": "#0284c7",
        "stage_num": 4,
        "is_terminal": False,
        "description": "Client PO / Quotation confirmed; ready for vendor procurement.",
    },
    MasterOrderStatus.IN_PROCUREMENT.value: {
        "label": "In Procurement",
        "stage_label": "Stage 05: Vendor & QC (In Procurement / PO Issued)",
        "badge_class": "pb-amber",
        "color": "#b45309",
        "stage_num": 5,
        "is_terminal": False,
        "description": "Purchase orders issued to glass/hardware suppliers; fabrication active.",
    },
    MasterOrderStatus.PARTIALLY_RECEIVED.value: {
        "label": "Partially Received",
        "stage_label": "Stage 05: Vendor & QC (Partially Received from Fabricator)",
        "badge_class": "pb-amber",
        "color": "#d97706",
        "stage_num": 5,
        "is_terminal": False,
        "description": "Partial materials received from suppliers; balance in fabrication.",
    },
    MasterOrderStatus.RECEIVED.value: {
        "label": "Received & QC Passed",
        "stage_label": "Stage 05: Vendor & QC (Fabrication Complete / QC Passed)",
        "badge_class": "pb-green",
        "color": "#16a34a",
        "stage_num": 5,
        "is_terminal": False,
        "description": "All raw materials and glass lots received and verified via Pre-Dispatch QC.",
    },
    MasterOrderStatus.IN_DELIVERY.value: {
        "label": "In Delivery",
        "stage_label": "Stage 06: Transport (Out for Delivery / In Transit)",
        "badge_class": "pb-blue",
        "color": "#4338ca",
        "stage_num": 6,
        "is_terminal": False,
        "description": "Consignments dispatched via transporter; in transit to customer site.",
    },
    MasterOrderStatus.PARTIALLY_DELIVERED.value: {
        "label": "Partially Delivered",
        "stage_label": "Stage 06: Transport (Partially Delivered to Site)",
        "badge_class": "pb-amber",
        "color": "#ca8a04",
        "stage_num": 6,
        "is_terminal": False,
        "description": "Initial consignments delivered to site; remaining lots scheduled or in transit.",
    },
    MasterOrderStatus.DELIVERED.value: {
        "label": "Delivered",
        "stage_label": "Stage 07: Unloading & Fitting (Site Delivered / POD Signed)",
        "badge_class": "pb-green",
        "color": "#059669",
        "stage_num": 7,
        "is_terminal": False,
        "description": "100% of order line items delivered to site; proof of delivery signed.",
    },
    MasterOrderStatus.IN_FITTING.value: {
        "label": "In Installation",
        "stage_label": "Stage 07: Unloading & Fitting (Installation Active)",
        "badge_class": "pb-purple",
        "color": "#6d28d9",
        "stage_num": 7,
        "is_terminal": False,
        "description": "Installation technicians mobilized on site; glazing & fitting in progress.",
    },
    MasterOrderStatus.COMPLETED.value: {
        "label": "Completed",
        "stage_label": "Stage 07: Project Completed & Handover",
        "badge_class": "pb-green",
        "color": "#15803d",
        "stage_num": 7,
        "is_terminal": True,
        "description": "All items delivered and fully installed; client handover signed off.",
    },
    MasterOrderStatus.CLOSED.value: {
        "label": "Closed",
        "stage_label": "Stage 07: Closed & Reconciled",
        "badge_class": "pb-gray",
        "color": "#334155",
        "stage_num": 7,
        "is_terminal": True,
        "description": "Administrative closure: order commercially settled and archived.",
    },
    MasterOrderStatus.ON_HOLD.value: {
        "label": "On Hold",
        "stage_label": "⏸ Put On Hold (Site / Client Delay)",
        "badge_class": "pb-amber",
        "color": "#ea580c",
        "stage_num": 4,
        "is_terminal": False,
        "description": "Temporarily paused due to site readiness, client request, or commercial review.",
    },
    MasterOrderStatus.CANCELLED.value: {
        "label": "Cancelled",
        "stage_label": "⛔ Cancel Order",
        "badge_class": "pb-red",
        "color": "#dc2626",
        "stage_num": 4,
        "is_terminal": True,
        "description": "Order cancelled; operational work terminated.",
    },

    # Customer Payment Statuses
    OrderCustomerPaymentStatus.UNPAID.value: {
        "label": "Advance Pending",
        "badge_class": "pay-unpaid",
        "color": "#dc2626",
        "description": "No customer payment received yet; advance pending.",
    },
    OrderCustomerPaymentStatus.PARTIALLY_PAID.value: {
        "label": "Advance Paid - Balance Due",
        "badge_class": "pay-partial",
        "color": "#d97706",
        "description": "Partial payment received; remaining balance outstanding.",
    },
    OrderCustomerPaymentStatus.PAID.value: {
        "label": "Fully Paid",
        "badge_class": "pay-paid",
        "color": "#16a34a",
        "description": "100% of order amount received and verified.",
    },
    OrderCustomerPaymentStatus.OVERPAID.value: {
        "label": "Overpaid",
        "badge_class": "pay-paid",
        "color": "#2563eb",
        "description": "Customer paid excess amount; credit available.",
    },

    # Delivery Operational Statuses
    OrderDeliveryStatus.SCHEDULED.value: {
        "label": "Scheduled",
        "badge_class": "pb-gray",
        "color": "#64748b",
        "description": "Delivery challan scheduled; vehicle assignment pending.",
    },
    OrderDeliveryStatus.OUT_FOR_DELIVERY.value: {
        "label": "Out for Delivery",
        "badge_class": "pb-blue",
        "color": "#0284c7",
        "description": "Consignment dispatched; in transit to customer site.",
    },
    OrderDeliveryStatus.PARTIALLY_DELIVERED.value: {
        "label": "Partially Delivered",
        "badge_class": "pb-amber",
        "color": "#ca8a04",
        "description": "Partial delivery completed; balance lots pending.",
    },
    OrderDeliveryStatus.DELIVERED.value: {
        "label": "Delivered",
        "badge_class": "pb-green",
        "color": "#16a34a",
        "description": "Shipment delivered to site; POD signed.",
    },
    OrderDeliveryStatus.FAILED.value: {
        "label": "Delivery Failed",
        "badge_class": "pb-red",
        "color": "#dc2626",
        "description": "Delivery attempt failed (site not ready, access denied).",
    },
    OrderDeliveryStatus.RETURNED.value: {
        "label": "Returned",
        "badge_class": "pb-red",
        "color": "#dc2626",
        "description": "Goods returned to warehouse / fabricator.",
    },

    # Fitting Operational Statuses
    OrderFittingStatus.NOT_REQUIRED.value: {
        "label": "Fitting Not Required",
        "badge_class": "pb-gray",
        "color": "#94a3b8",
        "description": "Material supply only; installation excluded from contract.",
    },
    OrderFittingStatus.PENDING_SCHEDULE.value: {
        "label": "Pending Schedule",
        "badge_class": "pb-amber",
        "color": "#d97706",
        "description": "Materials on site; installation team assignment pending.",
    },
    OrderFittingStatus.SCHEDULED.value: {
        "label": "Fitting Scheduled",
        "badge_class": "pb-blue",
        "color": "#0284c7",
        "description": "Technicians assigned; installation visit scheduled.",
    },
    OrderFittingStatus.IN_PROGRESS.value: {
        "label": "Installation Active",
        "badge_class": "pb-purple",
        "color": "#7c3aed",
        "description": "On-site installation and glazing actively underway.",
    },
    OrderFittingStatus.PARTIALLY_FITTED.value: {
        "label": "Partially Installed",
        "badge_class": "pb-amber",
        "color": "#b45309",
        "description": "Partial items installed; follow-up visit required.",
    },
    OrderFittingStatus.COMPLETED.value: {
        "label": "Fitting Completed",
        "badge_class": "pb-green",
        "color": "#15803d",
        "description": "All items installed and client sign-off obtained.",
    },
    OrderFittingStatus.FAILED.value: {
        "label": "Installation Issue",
        "badge_class": "pb-red",
        "color": "#dc2626",
        "description": "Installation halted due to site or measurement issues.",
    },
}


# ============================================================================
# CENTRAL STATUS ENGINE
# ============================================================================

class OMSStatusEngine:
    """
    Centralized, authoritative service for OMS status transitions,
    child-to-parent aggregations, and immutable history logging.
    """

    @staticmethod
    def get_valid_master_transitions(current_status: str) -> List[str]:
        """Return valid forward transitions for a master order status."""
        cur = str(current_status or "").upper()
        return sorted(list(VALID_MASTER_TRANSITIONS.get(cur, set())))

    @staticmethod
    def get_status_metadata(status: str) -> Dict[str, Any]:
        """Return canonical display metadata for a status string."""
        cur = str(status or "").upper()
        if cur in STATUS_METADATA:
            return STATUS_METADATA[cur]
        return {
            "label": cur.replace("_", " ").title(),
            "stage_label": cur.replace("_", " ").title(),
            "badge_class": "pb-gray",
            "color": "#64748b",
            "stage_num": 4,
            "is_terminal": False,
            "description": "",
        }

    @staticmethod
    def get_all_status_labels_map() -> Dict[str, str]:
        """Return mapping of status code -> friendly stage label for UI dropdowns."""
        return {
            k: v.get("stage_label", v.get("label", k))
            for k, v in STATUS_METADATA.items()
        }

    # ------------------------------------------------------------------------
    # 1. Authoritative Master Order State Transition
    # ------------------------------------------------------------------------
    @classmethod
    def transition_master_status(
        cls,
        org_id: int,
        order_id: int,
        to_status: str,
        remarks: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderMaster:
        """
        Validate and execute an authoritative state transition for a Master Order.
        Locks the order row, verifies tenant isolation, validates transition matrix,
        updates status & terminal timestamps, and appends an immutable history log.
        """
        order = (
            db.session.query(OrderMaster)
            .filter(
                OrderMaster.id == order_id,
                OrderMaster.organization_id == org_id,
                OrderMaster.is_deleted == False,
            )
            .with_for_update()
            .first()
        )
        if not order:
            raise OMSValidationError(f"Order #{order_id} not found in organization #{org_id}.")

        current_status = str(order.status or "").upper()
        actual_target = str(to_status or "").upper()

        if current_status == actual_target:
            return order

        # Terminal state protection
        if current_status in TERMINAL_MASTER_STATUSES:
            raise OMSTransitionError(
                f"Order #{order_id} is in terminal status '{current_status}' and cannot transition to '{actual_target}'."
            )

        allowed = VALID_MASTER_TRANSITIONS.get(current_status, set())
        if actual_target not in allowed:
            raise OMSTransitionError(
                f"Illegal order transition from '{current_status}' to '{actual_target}'. "
                f"Allowed transitions: {sorted(list(allowed))}"
            )

        # Transition business prerequisites validation
        if actual_target == MasterOrderStatus.COMPLETED.value:
            # Cannot complete if items are not 100% delivered
            items = [it for it in order.items if not it.is_deleted]
            if items:
                undelivered = [it for it in items if (it.delivered_quantity or Decimal("0")) < (it.quantity or Decimal("0"))]
                if undelivered:
                    raise OMSValidationError(
                        f"Cannot mark order #{order_id} COMPLETED: {len(undelivered)} line item(s) are not fully delivered."
                    )
                # If fitting is required, verify fitting is complete
                if order.fitting_status != OrderFittingStatus.NOT_REQUIRED.value:
                    unfitted = [it for it in items if (it.installed_quantity or Decimal("0")) < (it.quantity or Decimal("0"))]
                    if unfitted:
                        raise OMSValidationError(
                            f"Cannot mark order #{order_id} COMPLETED: {len(unfitted)} line item(s) have pending installation."
                        )

        # Apply state transition
        order.status = actual_target
        now = datetime.utcnow()

        if actual_target == MasterOrderStatus.COMPLETED.value:
            order.completed_at = now
        elif actual_target == MasterOrderStatus.CANCELLED.value:
            order.cancelled_at = now
        elif actual_target == MasterOrderStatus.CLOSED.value:
            order.completed_at = order.completed_at or now

        # Append immutable audit history
        effective_actor = actor_id or order.created_by_id
        hist = OrderStatusHistory(
            order_id=order.id,
            organization_id=org_id,
            status_type="MASTER",
            from_status=current_status,
            to_status=actual_target,
            remarks=remarks or f"Transitioned from {current_status} to {actual_target}.",
            changed_by_id=effective_actor,
        )
        db.session.add(hist)
        db.session.flush()

        return order

    # ------------------------------------------------------------------------
    # 2. Child-to-Parent Aggregation: Procurement
    # ------------------------------------------------------------------------
    @classmethod
    def recalculate_procurement_status(
        cls, order: OrderMaster, actor_id: Optional[int] = None
    ) -> str:
        """
        True multi-vendor procurement aggregation based on line item quantities.
        Prevents premature completion when only some items have POs issued.
        """
        items = [it for it in order.items if not it.is_deleted]
        if not items:
            return order.procurement_status

        total_ordered_qty = sum((it.quantity for it in items), Decimal("0.000"))
        total_allocated_qty = sum((it.allocated_quantity for it in items), Decimal("0.000"))
        total_procured_qty = sum((it.procured_quantity for it in items), Decimal("0.000"))

        prev_status = order.procurement_status

        if total_procured_qty >= total_ordered_qty and total_ordered_qty > Decimal("0.000"):
            new_status = OrderProcurementStatus.RECEIVED.value
        elif total_procured_qty > Decimal("0.000"):
            new_status = OrderProcurementStatus.PARTIALLY_RECEIVED.value
        elif total_allocated_qty > Decimal("0.000") or bool(order.procurements):
            # Check if any PO is acknowledged or in transit
            any_transit = any(
                p.status in [OrderProcurementStatus.IN_TRANSIT.value, OrderProcurementStatus.ACKNOWLEDGED.value]
                for p in order.procurements
                if not getattr(p, "is_deleted", False)
            )
            new_status = OrderProcurementStatus.IN_TRANSIT.value if any_transit else OrderProcurementStatus.PO_ISSUED.value
        else:
            new_status = OrderProcurementStatus.DRAFT.value

        if new_status != prev_status:
            order.procurement_status = new_status
            effective_actor = actor_id or order.created_by_id
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=order.organization_id,
                status_type="PROCUREMENT",
                from_status=prev_status,
                to_status=new_status,
                remarks=f"Procurement aggregated: {total_procured_qty}/{total_ordered_qty} units received.",
                changed_by_id=effective_actor,
            )
            db.session.add(hist)

        return order.procurement_status

    # ------------------------------------------------------------------------
    # 3. Child-to-Parent Aggregation: Delivery
    # ------------------------------------------------------------------------
    @classmethod
    def recalculate_delivery_status(
        cls, order: OrderMaster, actor_id: Optional[int] = None
    ) -> str:
        """
        True delivery aggregation based on line item delivered quantities.
        Distinguishes full delivery from partial delivery.
        """
        items = [it for it in order.items if not it.is_deleted]
        if not items:
            return order.delivery_status

        total_ordered_qty = sum((it.quantity for it in items), Decimal("0.000"))
        total_delivered_qty = sum((it.delivered_quantity for it in items), Decimal("0.000"))

        prev_status = order.delivery_status

        if total_delivered_qty >= total_ordered_qty and total_ordered_qty > Decimal("0.000"):
            new_status = OrderDeliveryStatus.DELIVERED.value
        elif total_delivered_qty > Decimal("0.000"):
            new_status = OrderDeliveryStatus.PARTIALLY_DELIVERED.value
        elif any(
            d.status == OrderDeliveryStatus.OUT_FOR_DELIVERY.value
            for d in order.deliveries
            if not getattr(d, "is_deleted", False)
        ) or any(
            c.status in [OrderConsignmentStatus.DISPATCHED.value, OrderConsignmentStatus.IN_TRANSIT.value]
            for c in order.consignments
            if not getattr(c, "is_deleted", False)
        ):
            new_status = OrderDeliveryStatus.OUT_FOR_DELIVERY.value
        else:
            new_status = OrderDeliveryStatus.SCHEDULED.value

        if new_status != prev_status:
            order.delivery_status = new_status
            effective_actor = actor_id or order.created_by_id
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=order.organization_id,
                status_type="DELIVERY",
                from_status=prev_status,
                to_status=new_status,
                remarks=f"Delivery aggregated: {total_delivered_qty}/{total_ordered_qty} units delivered.",
                changed_by_id=effective_actor,
            )
            db.session.add(hist)

        return order.delivery_status

    # ------------------------------------------------------------------------
    # 4. Child-to-Parent Aggregation: Fitting / Installation
    # ------------------------------------------------------------------------
    @classmethod
    def recalculate_fitting_status(
        cls, order: OrderMaster, actor_id: Optional[int] = None
    ) -> str:
        """
        Authoritative fitting aggregation based on line item installed quantities.
        Preserves NOT_REQUIRED state when installation is excluded.
        """
        if order.fitting_status == OrderFittingStatus.NOT_REQUIRED.value:
            return order.fitting_status

        items = [it for it in order.items if not it.is_deleted]
        if not items:
            return order.fitting_status

        total_ordered_qty = sum((it.quantity for it in items), Decimal("0.000"))
        total_installed_qty = sum((it.installed_quantity for it in items), Decimal("0.000"))

        prev_status = order.fitting_status

        if total_installed_qty >= total_ordered_qty and total_ordered_qty > Decimal("0.000"):
            new_status = OrderFittingStatus.COMPLETED.value
        elif total_installed_qty > Decimal("0.000"):
            new_status = OrderFittingStatus.PARTIALLY_FITTED.value
        elif any(
            v.status == OrderFittingVisitStatus.IN_PROGRESS.value
            for v in order.fitting_visits
            if not getattr(v, "is_deleted", False)
        ):
            new_status = OrderFittingStatus.IN_PROGRESS.value
        elif any(
            v.status == OrderFittingVisitStatus.SCHEDULED.value
            for v in order.fitting_visits
            if not getattr(v, "is_deleted", False)
        ):
            new_status = OrderFittingStatus.SCHEDULED.value
        else:
            new_status = OrderFittingStatus.PENDING_SCHEDULE.value

        if new_status != prev_status:
            order.fitting_status = new_status
            effective_actor = actor_id or order.created_by_id
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=order.organization_id,
                status_type="FITTING",
                from_status=prev_status,
                to_status=new_status,
                remarks=f"Installation aggregated: {total_installed_qty}/{total_ordered_qty} units installed.",
                changed_by_id=effective_actor,
            )
            db.session.add(hist)

        return order.fitting_status

    # ------------------------------------------------------------------------
    # 5. Full Lifecycle Synchronization (Child to Parent Flow)
    # ------------------------------------------------------------------------
    @classmethod
    def synchronize_order_master_status(
        cls, order: OrderMaster, actor_id: Optional[int] = None
    ) -> OrderMaster:
        """
        Harmonize all sub-workflows into the primary Master Order lifecycle state.
        Ensures no false completion, handles NOT_REQUIRED fitting auto-completion,
        and logs state history whenever the master status advances.
        """
        # First recalculate child sub-workflow states
        cls.recalculate_procurement_status(order, actor_id=actor_id)
        cls.recalculate_delivery_status(order, actor_id=actor_id)
        cls.recalculate_fitting_status(order, actor_id=actor_id)

        # Do not override terminal or on-hold states
        if order.status in [
            MasterOrderStatus.COMPLETED.value,
            MasterOrderStatus.CLOSED.value,
            MasterOrderStatus.CANCELLED.value,
            MasterOrderStatus.ON_HOLD.value,
        ]:
            return order

        prev_master = order.status
        target_master = prev_master

        # Check for completion condition:
        # 1. 100% Delivered
        # 2. 100% Fitted OR Fitting is NOT_REQUIRED
        is_fully_delivered = order.delivery_status == OrderDeliveryStatus.DELIVERED.value
        is_fitting_complete = (
            order.fitting_status == OrderFittingStatus.COMPLETED.value
            or order.fitting_status == OrderFittingStatus.NOT_REQUIRED.value
        )

        if is_fully_delivered and is_fitting_complete:
            target_master = MasterOrderStatus.COMPLETED.value
            order.completed_at = datetime.utcnow()
        elif is_fully_delivered and order.fitting_status != OrderFittingStatus.NOT_REQUIRED.value:
            if order.fitting_status in [OrderFittingStatus.SCHEDULED.value, OrderFittingStatus.IN_PROGRESS.value, OrderFittingStatus.PARTIALLY_FITTED.value]:
                target_master = MasterOrderStatus.IN_FITTING.value
            else:
                target_master = MasterOrderStatus.DELIVERED.value
        elif order.delivery_status == OrderDeliveryStatus.PARTIALLY_DELIVERED.value:
            target_master = MasterOrderStatus.PARTIALLY_DELIVERED.value
        elif order.delivery_status == OrderDeliveryStatus.OUT_FOR_DELIVERY.value:
            target_master = MasterOrderStatus.IN_DELIVERY.value
        elif order.procurement_status == OrderProcurementStatus.RECEIVED.value:
            if prev_master in [MasterOrderStatus.CONFIRMED.value, MasterOrderStatus.IN_PROCUREMENT.value, MasterOrderStatus.PARTIALLY_RECEIVED.value]:
                target_master = MasterOrderStatus.RECEIVED.value
        elif order.procurement_status in [OrderProcurementStatus.PO_ISSUED.value, OrderProcurementStatus.IN_TRANSIT.value]:
            if prev_master == MasterOrderStatus.CONFIRMED.value:
                target_master = MasterOrderStatus.IN_PROCUREMENT.value
        elif order.procurement_status == OrderProcurementStatus.PARTIALLY_RECEIVED.value:
            if prev_master in [MasterOrderStatus.CONFIRMED.value, MasterOrderStatus.IN_PROCUREMENT.value]:
                target_master = MasterOrderStatus.PARTIALLY_RECEIVED.value

        # If master status changed, log history and update
        if target_master != prev_master:
            order.status = target_master
            effective_actor = actor_id or order.created_by_id
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=order.organization_id,
                status_type="MASTER",
                from_status=prev_master,
                to_status=target_master,
                remarks=f"Master Order lifecycle synchronized: {prev_master} -> {target_master}.",
                changed_by_id=effective_actor,
            )
            db.session.add(hist)

        return order

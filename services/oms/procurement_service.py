"""
services/oms/procurement_service.py
Service managing vendor Purchase Orders, procurement line items,
transporter consignments, and arrival quality inspection (QC).
"""
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Any, Optional

from model import (
    db,
    OrderMaster,
    OrderItem,
    OrderItemVendorAllocation,
    OrderVendorProcurement,
    OrderProcurementItem,
    OrderConsignment,
    ConsignmentItem,
    Vendor,
    OrderStatusHistory,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderItemAllocationStatus,
    OrderConsignmentStatus,
)
from . import (
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)
from .order_master_service import OrderMasterService


VALID_PO_TRANSITIONS = {
    OrderProcurementStatus.DRAFT.value: {
        OrderProcurementStatus.PO_ISSUED.value,
        OrderProcurementStatus.CANCELLED.value,
    },
    OrderProcurementStatus.PO_ISSUED.value: {
        OrderProcurementStatus.ACKNOWLEDGED.value,
        OrderProcurementStatus.IN_TRANSIT.value,
        OrderProcurementStatus.CANCELLED.value,
    },
    OrderProcurementStatus.ACKNOWLEDGED.value: {
        OrderProcurementStatus.IN_TRANSIT.value,
        OrderProcurementStatus.PARTIALLY_RECEIVED.value,
        OrderProcurementStatus.RECEIVED.value,
        OrderProcurementStatus.CANCELLED.value,
    },
    OrderProcurementStatus.IN_TRANSIT.value: {
        OrderProcurementStatus.PARTIALLY_RECEIVED.value,
        OrderProcurementStatus.RECEIVED.value,
        OrderProcurementStatus.CANCELLED.value,
    },
    OrderProcurementStatus.PARTIALLY_RECEIVED.value: {
        OrderProcurementStatus.RECEIVED.value,
        OrderProcurementStatus.CANCELLED.value,
    },
    OrderProcurementStatus.RECEIVED.value: set(),
    OrderProcurementStatus.CANCELLED.value: set(),
}


class ProcurementService:
    """Service managing vendor procurement, PO line generation, and consignments."""

    @staticmethod
    def generate_po_number(org_id: int) -> str:
        """Generate unique Purchase Order number per tenant."""
        year = datetime.utcnow().year
        prefix = f"PO-{year}-"
        last = (
            OrderVendorProcurement.query
            .filter(
                OrderVendorProcurement.organization_id == org_id,
                OrderVendorProcurement.po_number.like(f"{prefix}%")
            )
            .order_by(OrderVendorProcurement.id.desc())
            .first()
        )
        seq = 1
        if last and last.po_number:
            try:
                seq = int(last.po_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = last.id + 1
        return f"{prefix}{seq:05d}"

    @staticmethod
    def generate_consignment_number(org_id: int) -> str:
        """Generate unique Consignment number per tenant."""
        year = datetime.utcnow().year
        prefix = f"CON-{year}-"
        last = (
            OrderConsignment.query
            .filter(
                OrderConsignment.organization_id == org_id,
                OrderConsignment.consignment_number.like(f"{prefix}%")
            )
            .order_by(OrderConsignment.id.desc())
            .first()
        )
        seq = 1
        if last and last.consignment_number:
            try:
                seq = int(last.consignment_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = last.id + 1
        return f"{prefix}{seq:05d}"

    @classmethod
    def create_purchase_order(
        cls,
        org_id: int,
        order_id: int,
        vendor_id: int,
        allocation_ids: Optional[List[int]] = None,
        items_data: Optional[List[Dict[str, Any]]] = None,
        tax_rate: Decimal = Decimal("18.00"),
        expected_delivery_date: Optional[datetime] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderVendorProcurement:
        """
        Issue a Purchase Order for specific vendor allocations.
        Guarantees that each allocation strictly matches the PO vendor, order, and tenant.
        """
        if not allocation_ids and items_data:
            allocation_ids = [it.get("allocation_id") for it in items_data if it.get("allocation_id")]

        if not allocation_ids:
            raise OMSValidationError("At least one allocation must be selected to generate a Purchase Order.")

        # Parent-first locking
        order = OrderMasterService.lock_order(org_id, order_id)
        if order.status in (MasterOrderStatus.CANCELLED.value, MasterOrderStatus.CLOSED.value):
            raise OMSValidationError(f"Cannot issue PO on order in status {order.status}.")

        vendor = Vendor.query.filter_by(id=vendor_id, organization_id=org_id).first()
        if not vendor:
            raise OMSTenantIsolationError(f"Vendor #{vendor_id} does not belong to organization {org_id}.")

        # Pre-validate all allocations before any entity creation (Fail-Fast atomic integrity)
        validated_allocs = []
        for alloc_id in allocation_ids:
            alloc = OrderItemVendorAllocation.query.filter_by(
                id=alloc_id, order_id=order_id, organization_id=org_id
            ).first()

            if not alloc:
                raise OMSValidationError(f"Allocation #{alloc_id} not found on Order #{order_id}.")

            if alloc.vendor_id != vendor_id:
                raise OMSInvariantError(
                    f"Allocation #{alloc_id} belongs to Vendor #{alloc.vendor_id}, cannot attach to PO for Vendor #{vendor_id}."
                )

            if alloc.procurement_id is not None:
                raise OMSInvariantError(
                    f"Allocation #{alloc_id} is already linked to Purchase Order #{alloc.procurement_id}."
                )
            validated_allocs.append(alloc)

        po_num = cls.generate_po_number(org_id)
        actor = actor_id or order.created_by_id

        po = OrderVendorProcurement(
            po_number=po_num,
            order_id=order_id,
            vendor_id=vendor_id,
            organization_id=org_id,
            status=OrderProcurementStatus.PO_ISSUED.value,
            po_date=datetime.utcnow(),
            expected_delivery_date=expected_delivery_date,
            created_by_id=actor,
            notes=notes,
        )
        db.session.add(po)
        db.session.flush()

        total_amount = Decimal("0.00")
        total_tax = Decimal("0.00")

        for alloc in validated_allocs:
            qty = alloc.allocated_quantity
            unit_rate = alloc.expected_rate
            line_base = qty * unit_rate
            line_tax = (line_base * tax_rate) / Decimal("100.00")
            line_total = line_base + line_tax

            po_item = OrderProcurementItem(
                procurement_id=po.id,
                allocation_id=alloc.id,
                vendor_id=vendor_id,
                order_id=order_id,
                organization_id=org_id,
                quantity=qty,
                unit_rate=unit_rate,
                total_rate=line_base.quantize(Decimal("0.01")),
                tax_amount=line_tax.quantize(Decimal("0.01")),
                line_total=line_total.quantize(Decimal("0.01")),
            )
            db.session.add(po_item)

            # Link allocation to this PO
            alloc.procurement_id = po.id
            alloc.allocation_status = OrderItemAllocationStatus.PO_CREATED.value

            # Update item procured counter
            item = OrderItem.query.filter_by(id=alloc.order_item_id).first()
            if item:
                item.procured_quantity += qty

            total_amount += line_base
            total_tax += line_tax

        po.total_procurement_amount = total_amount.quantize(Decimal("0.01"))
        po.tax_amount = total_tax.quantize(Decimal("0.01"))
        po.net_procurement_amount = (total_amount + total_tax).quantize(Decimal("0.01"))

        # Update order procurement financial rollup
        all_pos = OrderVendorProcurement.query.filter_by(order_id=order_id, organization_id=org_id).all()
        order.total_procurement_cost = sum(p.net_procurement_amount for p in all_pos if not p.is_deleted)

        from services.oms.status_engine import OMSStatusEngine
        OMSStatusEngine.recalculate_procurement_status(order, actor_id=actor)
        OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor)

        db.session.commit()
        return po

    @classmethod
    def record_consignment(
        cls,
        org_id: int,
        order_id: int,
        items_data: Optional[List[Dict[str, Any]]] = None,
        procurement_id: Optional[int] = None,
        transporter_name: Optional[str] = None,
        lr_number: Optional[str] = None,
        tracking_number: Optional[str] = None,
        vehicle_number: Optional[str] = None,
        driver_contact: Optional[str] = None,
        eway_bill_number: Optional[str] = None,
        dispatch_date: Optional[datetime] = None,
        shipping_cost: Decimal = Decimal("0.00"),
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
        **kwargs
    ) -> OrderConsignment:
        """Record dispatch of materials from vendor via transporter consignment."""
        items_data = items_data or []

        order = OrderMasterService.lock_order(org_id, order_id)
        cons_number = cls.generate_consignment_number(org_id)
        actor = actor_id or order.created_by_id

        actual_lr = lr_number or tracking_number
        extra_parts = []
        if driver_contact:
            extra_parts.append(f"[Driver: {driver_contact}]")
        if eway_bill_number:
            extra_parts.append(f"[E-Way: {eway_bill_number}]")
        extra_info = " ".join(extra_parts)
        effective_notes = f"{notes} {extra_info}".strip() if notes and extra_info else (notes or extra_info or None)

        cons = OrderConsignment(
            consignment_number=cons_number,
            order_id=order_id,
            procurement_id=procurement_id,
            organization_id=org_id,
            transporter_name=transporter_name,
            lr_number=actual_lr,
            vehicle_number=vehicle_number,
            dispatch_date=dispatch_date or datetime.utcnow(),
            status=OrderConsignmentStatus.DISPATCHED.value,
            shipping_cost=shipping_cost,
            notes=effective_notes,
            received_by_id=actor,
        )
        db.session.add(cons)
        db.session.flush()

        if items_data:
            for it in items_data:
                po_item_id = it.get("procurement_item_id")
                shipped_qty = Decimal(str(it.get("shipped_quantity", "0.000")))

                po_item = OrderProcurementItem.query.filter_by(
                    id=po_item_id, order_id=order_id, organization_id=org_id
                ).first()
                if not po_item:
                    raise OMSValidationError(f"Procurement item #{po_item_id} not found on Order #{order_id}.")

                remaining_to_ship = po_item.quantity - po_item.received_quantity
                if shipped_qty <= Decimal("0"):
                    raise OMSInvariantError("Shipped quantity must be greater than zero.")
                if shipped_qty > remaining_to_ship:
                    raise OMSInvariantError(
                        f"Shipped quantity ({shipped_qty}) exceeds pending procurement line quantity ({remaining_to_ship})."
                    )

                cons_item = ConsignmentItem(
                    consignment_id=cons.id,
                    procurement_item_id=po_item.id,
                    order_id=order_id,
                    organization_id=org_id,
                    shipped_quantity=shipped_qty,
                    qc_status="PENDING",
                )
                db.session.add(cons_item)

        if procurement_id:
            po = OrderVendorProcurement.query.filter_by(id=procurement_id, organization_id=org_id).first()
            if po and po.status in (OrderProcurementStatus.PO_ISSUED.value, OrderProcurementStatus.ACKNOWLEDGED.value):
                po.status = OrderProcurementStatus.IN_TRANSIT.value
        elif order.procurement_status == OrderProcurementStatus.DRAFT.value:
            order.procurement_status = OrderProcurementStatus.IN_TRANSIT.value

        db.session.commit()
        return cons

    @classmethod
    def record_consignment_qc_arrival(
        cls,
        org_id: int,
        consignment_id: int,
        items_qc: List[Dict[str, Any]],
        actor_id: Optional[int] = None,
    ) -> OrderConsignment:
        """
        Record site/warehouse arrival and Quality Control results.
        Updates accepted and rejected counters on procurement lines.
        """
        cons = OrderConsignment.query.filter_by(id=consignment_id, organization_id=org_id).first()
        if not cons:
            raise OMSValidationError(f"Consignment #{consignment_id} not found.")

        # Parent-first lock
        order = OrderMasterService.lock_order(org_id, cons.order_id)
        cons.actual_arrival_date = datetime.utcnow()

        all_passed = True
        any_damaged = False

        for qc in items_qc:
            c_item_id = qc.get("consignment_item_id")
            c_item = ConsignmentItem.query.filter_by(
                id=c_item_id, consignment_id=cons.id, organization_id=org_id
            ).first()
            if not c_item:
                raise OMSValidationError(f"Consignment item #{c_item_id} not found on consignment #{cons.id}.")

            rec_qty = Decimal(str(qc.get("received_quantity", c_item.shipped_quantity)))
            acc_qty = Decimal(str(qc.get("accepted_quantity", rec_qty)))
            dmg_qty = Decimal(str(qc.get("damaged_quantity", "0.000")))
            rej_qty = Decimal(str(qc.get("rejected_quantity", "0.000")))

            if acc_qty + dmg_qty + rej_qty > c_item.shipped_quantity:
                raise OMSInvariantError(
                    f"Sum of accepted ({acc_qty}), damaged ({dmg_qty}), and rejected ({rej_qty}) "
                    f"exceeds shipped quantity ({c_item.shipped_quantity})."
                )

            c_item.received_quantity = rec_qty
            c_item.accepted_quantity = acc_qty
            c_item.damaged_quantity = dmg_qty
            c_item.rejected_quantity = rej_qty
            c_item.qc_status = "PASSED" if dmg_qty == Decimal("0") and rej_qty == Decimal("0") else "PARTIAL"
            c_item.qc_remarks = qc.get("qc_remarks")

            if c_item.qc_status != "PASSED":
                all_passed = False
            if dmg_qty > Decimal("0"):
                any_damaged = True

            # Update procurement line counters
            po_item = OrderProcurementItem.query.filter_by(id=c_item.procurement_item_id).first()
            if po_item:
                po_item.received_quantity += rec_qty
                po_item.accepted_quantity += acc_qty
                po_item.rejected_quantity += rej_qty

            # Update allocation counter
            if po_item:
                alloc = OrderItemVendorAllocation.query.filter_by(id=po_item.allocation_id).first()
                if alloc:
                    alloc.received_quantity += acc_qty
                    if alloc.received_quantity >= alloc.allocated_quantity:
                        alloc.allocation_status = OrderItemAllocationStatus.RECEIVED.value
                    else:
                        alloc.allocation_status = OrderItemAllocationStatus.PARTIALLY_RECEIVED.value

        cons.status = OrderConsignmentStatus.DELIVERED.value if all_passed else (
            OrderConsignmentStatus.DAMAGED.value if any_damaged else OrderConsignmentStatus.PARTIALLY_RECEIVED.value
        )

        # Check if all items across PO are fully received
        if cons.procurement_id:
            po = OrderVendorProcurement.query.filter_by(id=cons.procurement_id).first()
            if po:
                po_items = OrderProcurementItem.query.filter_by(procurement_id=po.id).all()
                if all(pi.accepted_quantity >= pi.quantity for pi in po_items):
                    po.status = OrderProcurementStatus.RECEIVED.value
                else:
                    po.status = OrderProcurementStatus.PARTIALLY_RECEIVED.value

        # Authoritative multi-vendor procurement status recalculation & lifecycle sync
        from services.oms.status_engine import OMSStatusEngine
        OMSStatusEngine.recalculate_procurement_status(order, actor_id=actor_id)
        OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor_id)

        db.session.commit()
        return cons

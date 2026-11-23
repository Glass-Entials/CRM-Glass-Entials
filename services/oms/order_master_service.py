"""
services/oms/order_master_service.py
Core OrderMaster service implementing transactional lifecycle management,
parent-first concurrency locking, multi-vendor allocation invariants,
immutable status history, revisions, and cockpit data aggregation.
"""
import json
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Any, Optional

from sqlalchemy import or_, desc, asc
from model import (
    db,
    OrderMaster,
    OrderItem,
    OrderItemVendorAllocation,
    OrderVendorProcurement,
    OrderConsignment,
    OrderDelivery,
    OrderFittingVisit,
    OrderPaymentAllocation,
    VendorBill,
    VendorPayment,
    OrderDocument,
    OrderRevision,
    OrderStatusHistory,
    OrderExceptionalClosure,
    Customer,
    Vendor,
    Product,
    Quotation,
    Project,
    Employee,
    MasterOrderStatus,
    OrderItemAllocationStatus,
    OrderClosureType,
)
from . import (
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)


VALID_MASTER_TRANSITIONS = {
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
        MasterOrderStatus.IN_DELIVERY.value,
        MasterOrderStatus.IN_FITTING.value,
        MasterOrderStatus.CANCELLED.value,
    },
    MasterOrderStatus.CANCELLED.value: set(),
    MasterOrderStatus.CLOSED.value: set(),
}


class OrderMasterService:
    """Service handling high-level customer orders and allocation state."""

    @staticmethod
    def lock_order(org_id: int, order_id: int) -> OrderMaster:
        """Acquire pessimistic row-level lock on the parent OrderMaster within transaction."""
        order = (
            db.session.query(OrderMaster)
            .filter_by(id=order_id, organization_id=org_id)
            .with_for_update()
            .first()
        )
        if not order:
            raise OMSTenantIsolationError(f"Order #{order_id} not found in organization {org_id}.")
        return order

    @staticmethod
    def generate_order_number(org_id: int) -> str:
        """Generate next sequential Master Order ID per organization."""
        year = datetime.utcnow().year
        prefix = f"ORD-{year}-"
        last = (
            OrderMaster.query
            .filter(
                OrderMaster.organization_id == org_id,
                OrderMaster.master_order_number.like(f"{prefix}%")
            )
            .order_by(OrderMaster.id.desc())
            .first()
        )
        seq = 1
        if last and last.master_order_number:
            try:
                parts = last.master_order_number.split("-")
                seq = int(parts[-1]) + 1
            except (ValueError, IndexError):
                seq = last.id + 1
        return f"{prefix}{seq:05d}"

    @classmethod
    def create_order(
        cls,
        org_id: int,
        customer_id: int,
        items_data: List[Dict[str, Any]],
        order_title: Optional[str] = None,
        quotation_id: Optional[int] = None,
        project_id: Optional[int] = None,
        expected_delivery_date: Optional[datetime] = None,
        shipping_address: Optional[str] = None,
        billing_address: Optional[str] = None,
        site_address: Optional[str] = None,
        notes: Optional[str] = None,
        assigned_to_id: Optional[int] = None,
        actor_id: Optional[int] = None,
        # Extended master order creation parameters
        selected_vendor_ids: Optional[List[int]] = None,
        commercial_details: Optional[Dict[str, Any]] = None,
        delivery_details: Optional[Dict[str, Any]] = None,
        installation_details: Optional[Dict[str, Any]] = None,
        expected_fitting_date: Optional[datetime] = None,
        action_type: str = "save_draft",
        order_date: Optional[datetime] = None,
        create_initial_revision: bool = False,
    ) -> OrderMaster:
        """Create a new Master Order with initial items and audit snapshot."""
        if not items_data:
            raise OMSValidationError("An order must contain at least one order item.")

        effective_notes = f"[{order_title}] {notes}" if order_title and notes else (order_title or notes)

        # 1. Tenant validation
        customer = Customer.query.filter_by(id=customer_id, organization_id=org_id).first()
        if not customer:
            raise OMSTenantIsolationError(f"Customer #{customer_id} not found in organization {org_id}.")

        if quotation_id:
            quot = Quotation.query.filter_by(id=quotation_id, organization_id=org_id).first()
            if not quot:
                raise OMSTenantIsolationError(f"Quotation #{quotation_id} not found in organization {org_id}.")

        if project_id:
            proj = Project.query.filter_by(id=project_id, organization_id=org_id).first()
            if not proj:
                raise OMSTenantIsolationError(f"Project #{project_id} not found in organization {org_id}.")

        # Validate selected vendors tenant isolation
        valid_vendors = []
        if selected_vendor_ids:
            for v_id in selected_vendor_ids:
                try:
                    v_int = int(v_id)
                except (ValueError, TypeError):
                    continue
                v = Vendor.query.filter_by(id=v_int, organization_id=org_id, is_deleted=False).first()
                if not v:
                    raise OMSTenantIsolationError(f"Vendor #{v_int} does not belong to organization #{org_id}.")
                valid_vendors.append(v)

        if not actor_id:
            creator = Employee.query.filter_by(organization_id=org_id).first()
            actor_id = creator.id if creator else 1

        master_num = cls.generate_order_number(org_id)
        is_confirmed = (action_type == "confirm_order")
        initial_status = MasterOrderStatus.CONFIRMED.value if is_confirmed else MasterOrderStatus.DRAFT.value

        order = OrderMaster(
            master_order_number=master_num,
            organization_id=org_id,
            customer_id=customer_id,
            quotation_id=quotation_id,
            project_id=project_id,
            status=initial_status,
            order_date=order_date or datetime.utcnow(),
            expected_delivery_date=expected_delivery_date,
            expected_fitting_date=expected_fitting_date,
            shipping_address=shipping_address or customer.address,
            billing_address=billing_address or customer.address,
            site_address=site_address or shipping_address or customer.address,
            notes=effective_notes,
            created_by_id=actor_id,
            assigned_to_id=assigned_to_id,
        )
        db.session.add(order)
        db.session.flush()

        # 2. Add line items and compute totals
        total_ordered = Decimal("0.00")
        total_tax = Decimal("0.00")
        total_discount = Decimal("0.00")
        created_items = []

        for idx, item_dict in enumerate(items_data):
            qty = Decimal(str(item_dict.get("quantity", "0.000")))
            unit_price = Decimal(str(item_dict.get("unit_price", "0.00")))
            tax_rate = Decimal(str(item_dict.get("tax_rate", "0.00")))
            discount = Decimal(str(item_dict.get("discount_amount", "0.00")))

            if qty <= Decimal("0"):
                raise OMSInvariantError(f"Item #{idx+1}: Quantity must be greater than zero.")
            if unit_price < Decimal("0"):
                raise OMSInvariantError(f"Item #{idx+1}: Unit price cannot be negative.")

            base_amount = qty * unit_price
            tax_amount = (base_amount * tax_rate) / Decimal("100.00")
            item_total = (base_amount - discount) + tax_amount

            prod_id = item_dict.get("product_id")
            if prod_id:
                prod = Product.query.filter_by(id=prod_id, organization_id=org_id).first()
                if not prod:
                    raise OMSTenantIsolationError(f"Product #{prod_id} does not belong to organization {org_id}.")

            order_item = OrderItem(
                order_id=order.id,
                organization_id=org_id,
                product_id=prod_id,
                item_description=item_dict.get("item_description") or item_dict.get("description") or f"Item #{idx+1}",
                specifications=item_dict.get("specifications"),
                quantity=qty,
                unit_price=unit_price,
                tax_rate=tax_rate,
                tax_amount=tax_amount.quantize(Decimal("0.01")),
                discount_amount=discount.quantize(Decimal("0.01")),
                total_price=item_total.quantize(Decimal("0.01")),
            )
            db.session.add(order_item)
            db.session.flush()

            # Optional item-level vendor allocation at order creation
            alloc_vend_id = item_dict.get("allocated_vendor_id") or item_dict.get("vendor_id")
            if alloc_vend_id:
                try:
                    alloc_vend_id = int(alloc_vend_id)
                    v_match = Vendor.query.filter_by(id=alloc_vend_id, organization_id=org_id).first()
                    if v_match:
                        alloc = OrderItemVendorAllocation(
                            order_id=order.id,
                            order_item_id=order_item.id,
                            vendor_id=alloc_vend_id,
                            organization_id=org_id,
                            allocated_quantity=qty,
                            expected_rate=unit_price,
                            estimated_cost=base_amount.quantize(Decimal("0.01")),
                            allocation_status=OrderItemAllocationStatus.PENDING.value,
                            created_by_id=actor_id,
                        )
                        db.session.add(alloc)
                        order_item.allocated_quantity = qty
                except (ValueError, TypeError):
                    pass

            created_items.append(order_item)
            total_ordered += base_amount
            total_tax += tax_amount
            total_discount += discount

        order.total_ordered_amount = total_ordered.quantize(Decimal("0.01"))
        order.total_tax_amount = total_tax.quantize(Decimal("0.01"))
        order.total_discount_amount = total_discount.quantize(Decimal("0.01"))
        order.net_order_amount = (total_ordered - total_discount + total_tax).quantize(Decimal("0.01"))

        # 3. Create initial immutable OrderRevision (Revision 1) snapshot if requested
        if create_initial_revision:
            vendor_snapshots = [
                {
                    "id": v.id,
                    "code": v.code,
                    "company_name": v.company_name,
                    "contact_person": v.contact_person,
                    "phone": v.phone,
                    "email": v.email,
                    "gstin": v.gstin,
                    "city": v.city,
                    "category": v.category.name if v.category else None,
                }
                for v in valid_vendors
            ]

            customer_snapshot = {
                "id": customer.id,
                "name": customer.name,
                "company": customer.company,
                "email": customer.email,
                "phone_number": customer.phone_number,
                "gst_number": customer.gst_number,
                "billing_address": order.billing_address,
                "shipping_address": order.shipping_address,
                "site_address": order.site_address,
                "city": customer.city,
                "state": customer.state,
                "pincode": customer.pincode,
            }

            full_snapshot = {
                "order_number": order.master_order_number,
                "order_title": order_title,
                "order_date": order.order_date.isoformat() if order.order_date else None,
                "status": order.status,
                "customer_snapshot": customer_snapshot,
                "selected_vendors": vendor_snapshots,
                "commercial_details": commercial_details or {},
                "delivery_details": delivery_details or {},
                "installation_details": installation_details or {},
                "financial_summary": {
                    "total_ordered_amount": str(order.total_ordered_amount),
                    "total_discount_amount": str(order.total_discount_amount),
                    "total_tax_amount": str(order.total_tax_amount),
                    "net_order_amount": str(order.net_order_amount),
                },
                "items": [
                    {
                        "id": item.id,
                        "description": item.item_description,
                        "quantity": str(item.quantity),
                        "unit_price": str(item.unit_price),
                        "tax_rate": str(item.tax_rate),
                        "total_price": str(item.total_price),
                    }
                    for item in created_items
                ],
            }

            initial_rev = OrderRevision(
                order_id=order.id,
                organization_id=org_id,
                revision_number=1,
                snapshot_json=json.dumps(full_snapshot),
                change_reason="Initial master order creation snapshot.",
                created_by_id=actor_id,
            )
            db.session.add(initial_rev)

        # 4. Status history records
        hist_draft = OrderStatusHistory(
            order_id=order.id,
            organization_id=org_id,
            status_type="MASTER",
            from_status=None,
            to_status=MasterOrderStatus.DRAFT.value,
            remarks="Master Order created in DRAFT.",
            changed_by_id=actor_id,
        )
        db.session.add(hist_draft)

        if is_confirmed:
            hist_confirm = OrderStatusHistory(
                order_id=order.id,
                organization_id=org_id,
                status_type="MASTER",
                from_status=MasterOrderStatus.DRAFT.value,
                to_status=MasterOrderStatus.CONFIRMED.value,
                remarks="Master Order confirmed on creation.",
                changed_by_id=actor_id,
            )
            db.session.add(hist_confirm)

        db.session.commit()
        return order

    @classmethod
    def allocate_item_vendors(
        cls,
        org_id: int,
        order_id: int,
        order_item_id: int,
        allocations_data: List[Dict[str, Any]],
        actor_id: int,
    ) -> List[OrderItemVendorAllocation]:
        """
        Allocate an OrderItem's quantity across one or multiple vendors.
        Enforces invariant: SUM(allocated_quantity) <= item.quantity.
        """
        # Parent-first locking
        order = cls.lock_order(org_id, order_id)
        if order.status in (MasterOrderStatus.CANCELLED.value, MasterOrderStatus.CLOSED.value):
            raise OMSValidationError(f"Cannot modify allocations on an order in status {order.status}.")

        item = OrderItem.query.filter_by(
            id=order_item_id, order_id=order_id, organization_id=org_id
        ).first()
        if not item:
            raise OMSValidationError(f"OrderItem #{order_item_id} not found on Order #{order_id}.")

        # Existing allocations for this item
        existing_allocs = OrderItemVendorAllocation.query.filter_by(
            order_item_id=order_item_id, order_id=order_id, organization_id=org_id, is_deleted=False
        ).all()
        current_allocated = sum((ea.allocated_quantity for ea in existing_allocs), Decimal("0.000"))

        total_requested = Decimal("0.000")
        for a in allocations_data:
            vend_id = a.get("vendor_id")
            vendor = Vendor.query.filter_by(id=vend_id, organization_id=org_id).first()
            if not vendor:
                raise OMSTenantIsolationError(f"Vendor #{vend_id} not found in organization {org_id}.")
            qty = Decimal(str(a.get("allocated_quantity", "0.000")))
            if qty <= Decimal("0"):
                raise OMSInvariantError("Allocated quantity must be strictly positive.")
            total_requested += qty

        if (current_allocated + total_requested) > item.quantity:
            raise OMSInvariantError(
                f"Cumulative allocation ({current_allocated + total_requested}) exceeds item ordered quantity ({item.quantity})."
            )

        created_allocations = []
        for a in allocations_data:
            vend_id = a.get("vendor_id")
            alloc_qty = Decimal(str(a.get("allocated_quantity", "0.000")))
            rate = Decimal(str(a.get("expected_rate") or a.get("purchase_rate") or "0.00"))
            est_cost = (alloc_qty * rate).quantize(Decimal("0.01"))

            supply_date = a.get("expected_supply_date")
            if isinstance(supply_date, str):
                try:
                    supply_date = datetime.fromisoformat(supply_date)
                except ValueError:
                    supply_date = None

            alloc = OrderItemVendorAllocation(
                order_id=order_id,
                order_item_id=order_item_id,
                vendor_id=vend_id,
                organization_id=org_id,
                allocated_quantity=alloc_qty,
                expected_rate=rate,
                estimated_cost=est_cost,
                expected_supply_date=supply_date,
                allocation_status=OrderItemAllocationStatus.PENDING.value,
                notes=a.get("notes"),
                created_by_id=actor_id,
            )
            db.session.add(alloc)
            created_allocations.append(alloc)

        item.allocated_quantity = current_allocated + total_requested
        db.session.commit()
        return created_allocations

    @classmethod
    def transition_order_status(
        cls,
        org_id: int,
        order_id: int,
        target_status: Optional[str] = None,
        to_status: Optional[str] = None,
        remarks: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderMaster:
        """
        Validate and execute a lifecycle state transition.
        Records an immutable OrderStatusHistory entry.
        """
        actual_target = to_status or target_status
        if not actual_target:
            raise OMSValidationError("Target status must be provided.")

        order = cls.lock_order(org_id, order_id)
        current_status = order.status

        allowed = VALID_MASTER_TRANSITIONS.get(current_status, set())
        if actual_target not in allowed:
            raise OMSTransitionError(
                f"Illegal order transition from '{current_status}' to '{actual_target}'. "
                f"Allowed transitions: {list(allowed)}"
            )

        order.status = actual_target
        now = datetime.utcnow()

        if actual_target == MasterOrderStatus.COMPLETED.value:
            order.completed_at = now
        elif actual_target == MasterOrderStatus.CANCELLED.value:
            order.cancelled_at = now

        hist = OrderStatusHistory(
            order_id=order.id,
            organization_id=org_id,
            status_type="MASTER",
            from_status=current_status,
            to_status=actual_target,
            remarks=remarks or f"Transitioned from {current_status} to {actual_target}.",
            changed_by_id=actor_id or order.created_by_id,
        )
        db.session.add(hist)
        db.session.commit()
        return order

    @classmethod
    def exceptional_close_order(
        cls,
        org_id: int,
        order_id: int,
        closure_type: str,
        closure_reason: str,
        financial_impact_notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderExceptionalClosure:
        """
        Exceptional termination or forced closure of an order with mandatory audit logging.
        """
        if not closure_reason or len(closure_reason.strip()) < 5:
            raise OMSValidationError("A detailed closure reason (minimum 5 characters) is required.")

        valid_types = {OrderClosureType.CANCELLED.value, OrderClosureType.FORCE_COMPLETED.value, OrderClosureType.TERMINATED.value}
        if closure_type not in valid_types:
            raise OMSValidationError(f"Invalid closure type '{closure_type}'. Must be one of {valid_types}.")

        order = cls.lock_order(org_id, order_id)
        if order.status in (MasterOrderStatus.CLOSED.value, MasterOrderStatus.CANCELLED.value):
            raise OMSValidationError(f"Order #{order_id} is already in terminal status '{order.status}'.")

        prev_status = order.status
        target_status = MasterOrderStatus.CANCELLED.value if closure_type == OrderClosureType.CANCELLED.value else MasterOrderStatus.CLOSED.value
        order.status = target_status
        order.cancelled_at = datetime.utcnow() if target_status == MasterOrderStatus.CANCELLED.value else None
        order.completed_at = datetime.utcnow() if target_status == MasterOrderStatus.CLOSED.value else None

        actor = actor_id or order.created_by_id

        closure = OrderExceptionalClosure(
            order_id=order_id,
            organization_id=org_id,
            closure_type=closure_type,
            closure_reason=closure_reason,
            financial_impact_notes=financial_impact_notes,
            approved_by_id=actor,
        )
        db.session.add(closure)

        hist = OrderStatusHistory(
            order_id=order_id,
            organization_id=org_id,
            status_type="MASTER",
            from_status=prev_status,
            to_status=target_status,
            remarks=f"Exceptional Closure ({closure_type}): {closure_reason}",
            changed_by_id=actor,
        )
        db.session.add(hist)
        db.session.commit()
        return closure

    @classmethod
    def create_order_revision(
        cls,
        org_id: int,
        order_id: int,
        change_reason: str,
        actor_id: int,
    ) -> OrderRevision:
        """Capture an immutable snapshot of the order's current state."""
        order = cls.lock_order(org_id, order_id)

        # Count existing revisions
        rev_count = OrderRevision.query.filter_by(order_id=order_id, organization_id=org_id).count()
        next_rev = rev_count + 1

        snapshot = {
            "order_number": order.master_order_number,
            "master_order_number": order.master_order_number,
            "status": order.status,
            "net_order_amount": str(order.net_order_amount),
            "total_ordered_amount": str(order.total_ordered_amount),
            "items": [
                {
                    "id": item.id,
                    "description": item.item_description,
                    "quantity": str(item.quantity),
                    "unit_price": str(item.unit_price),
                    "total_price": str(item.total_price),
                }
                for item in order.items if not item.is_deleted
            ]
        }

        rev = OrderRevision(
            order_id=order_id,
            organization_id=org_id,
            revision_number=next_rev,
            snapshot_json=json.dumps(snapshot),
            change_reason=change_reason,
            created_by_id=actor_id,
        )
        db.session.add(rev)
        db.session.commit()
        return rev

    @classmethod
    def get_order_cockpit_details(cls, org_id: int, order_id: int) -> Dict[str, Any]:
        """Aggregate comprehensive unified cockpit data for an order."""
        order = (
            OrderMaster.query
            .filter_by(id=order_id, organization_id=org_id)
            .first()
        )
        if not order:
            raise OMSValidationError(f"Order #{order_id} not found.")

        # Extract initial snapshot if available
        initial_rev = next((r for r in order.revisions if r.revision_number == 1), None)
        initial_snapshot = {}
        if initial_rev and initial_rev.snapshot_json:
            try:
                initial_snapshot = json.loads(initial_rev.snapshot_json)
            except Exception:
                initial_snapshot = {}

        # Serialize order header and child collections
        return {
            "order": {
                "id": order.id,
                "master_order_number": order.master_order_number,
                "status": order.status,
                "procurement_status": order.procurement_status,
                "delivery_status": order.delivery_status,
                "fitting_status": order.fitting_status,
                "customer_payment_status": order.customer_payment_status,
                "vendor_payment_status": order.vendor_payment_status,
                "order_date": order.order_date.strftime("%Y-%m-%d %H:%M") if order.order_date else None,
                "expected_delivery_date": order.expected_delivery_date.strftime("%Y-%m-%d") if order.expected_delivery_date else None,
                "shipping_address": order.shipping_address,
                "site_address": order.site_address,
                "notes": order.notes,
                "version_id": order.version_id,
                "total_ordered_amount": float(order.total_ordered_amount),
                "total_tax_amount": float(order.total_tax_amount),
                "total_discount_amount": float(order.total_discount_amount),
                "net_order_amount": float(order.net_order_amount),
                "total_procurement_cost": float(order.total_procurement_cost),
                "total_customer_paid": float(order.total_customer_paid),
                "total_vendor_billed": float(order.total_vendor_billed),
                "total_vendor_paid": float(order.total_vendor_paid),
                "customer": {
                    "id": order.customer.id if order.customer else None,
                    "name": order.customer.name if order.customer else "Unknown",
                    "email": order.customer.email if order.customer else "",
                    "phone": order.customer.phone_number if order.customer else "",
                },
                "quotation": {
                    "id": order.quotation.id if order.quotation else None,
                    "quotation_number": order.quotation.quotation_number if order.quotation else None,
                } if order.quotation else None,
            },
            "items": [
                {
                    "id": it.id,
                    "item_description": it.item_description,
                    "description": it.item_description,
                    "specifications": it.specifications,
                    "quantity": float(it.quantity),
                    "unit_price": float(it.unit_price),
                    "tax_rate": float(it.tax_rate),
                    "total_price": float(it.total_price),
                    "allocated_quantity": float(it.allocated_quantity),
                    "procured_quantity": float(it.procured_quantity),
                    "delivered_quantity": float(it.delivered_quantity),
                    "installed_quantity": float(it.installed_quantity),
                    "allocations": [
                        {
                            "id": a.id,
                            "vendor_id": a.vendor_id,
                            "vendor_name": a.vendor.company_name if a.vendor else "Unknown",
                            "vendor": {"name": a.vendor.company_name if a.vendor else f"Vendor #{a.vendor_id}"},
                            "allocated_quantity": float(a.allocated_quantity),
                            "actual_supplied_quantity": float(a.received_quantity or Decimal("0.000")),
                            "received_quantity": float(a.received_quantity or Decimal("0.000")),
                            "expected_rate": float(a.expected_rate),
                            "purchase_rate": float(a.expected_rate),
                            "estimated_cost": float(a.estimated_cost),
                            "status": a.allocation_status,
                            "allocation_status": a.allocation_status,
                            "expected_supply_date": a.expected_supply_date,
                            "procurement_id": a.procurement_id,
                        }
                        for a in it.allocations if not a.is_deleted
                    ],
                }
                for it in order.items if not it.is_deleted
            ],
            "procurements": [
                {
                    "id": p.id,
                    "po_number": p.po_number,
                    "vendor_name": p.vendor.company_name if p.vendor else "Unknown",
                    "vendor": {"name": p.vendor.company_name if p.vendor else "Unknown"},
                    "status": p.status,
                    "po_date": p.po_date.strftime("%Y-%m-%d") if p.po_date else None,
                    "total_amount": float(p.total_procurement_amount),
                    "net_amount": float(p.net_procurement_amount),
                    "net_procurement_amount": float(p.net_procurement_amount),
                    "item_count": len(p.items),
                }
                for p in order.procurements if not p.is_deleted
            ],
            "consignments": [
                {
                    "id": c.id,
                    "consignment_number": c.consignment_number,
                    "transporter_name": c.transporter_name,
                    "vehicle_number": c.vehicle_number,
                    "lr_number": c.lr_number,
                    "tracking_number": c.lr_number or c.tracking_reference,
                    "status": c.status,
                    "dispatch_date": c.dispatch_date.strftime("%Y-%m-%d") if c.dispatch_date else None,
                    "procurement": {"po_number": c.procurement.po_number if c.procurement else None},
                }
                for c in order.consignments if not c.is_deleted
            ],
            "deliveries": [
                {
                    "id": d.id,
                    "delivery_number": d.delivery_number,
                    "challan_number": d.challan_number,
                    "status": d.status,
                    "delivery_date": d.delivery_date.strftime("%Y-%m-%d %H:%M") if d.delivery_date else None,
                    "delivered_by": {"name": d.delivered_by.name if d.delivered_by else "Staff"} if d.delivered_by else None,
                    "recipient_name": d.recipient_name,
                    "recipient_phone": d.recipient_phone,
                }
                for d in order.deliveries if not d.is_deleted
            ],
            "fitting_visits": [
                {
                    "id": f.id,
                    "visit_number": f.visit_number,
                    "technician": {"name": f.technician.name if f.technician else "Unassigned"} if f.technician else None,
                    "scheduled_date": f.scheduled_date.strftime("%Y-%m-%d %H:%M") if f.scheduled_date else None,
                    "status": f.status,
                    "client_signoff": f.client_signoff,
                }
                for f in order.fitting_visits if not f.is_deleted
            ],
            "payments": [
                {
                    "id": pa.id,
                    "payment_id": pa.payment_id,
                    "allocation_type": pa.allocation_type,
                    "amount": float(pa.allocated_amount),
                    "allocated_amount": float(pa.allocated_amount),
                    "allocation_date": pa.allocation_date.strftime("%Y-%m-%d %H:%M"),
                    "payment": {"payment_number": f"PAY-{pa.payment_id}"},
                    "allocated_by": {"name": pa.allocated_by.name if pa.allocated_by else "System"} if pa.allocated_by else None,
                }
                for pa in order.payment_allocations
            ],
            "payment_allocations": [
                {
                    "id": pa.id,
                    "payment_id": pa.payment_id,
                    "allocation_type": pa.allocation_type,
                    "amount": float(pa.allocated_amount),
                    "allocated_amount": float(pa.allocated_amount),
                    "allocation_date": pa.allocation_date.strftime("%Y-%m-%d %H:%M"),
                    "payment": {"payment_number": f"PAY-{pa.payment_id}"},
                    "allocated_by": {"name": pa.allocated_by.name if pa.allocated_by else "System"} if pa.allocated_by else None,
                }
                for pa in order.payment_allocations
            ],
            "vendor_bills": [
                {
                    "id": vb.id,
                    "bill_number": vb.bill_number,
                    "vendor_name": vb.vendor.company_name if vb.vendor else "Unknown",
                    "vendor": {"name": vb.vendor.company_name if vb.vendor else "Unknown"},
                    "status": vb.status,
                    "bill_date": vb.bill_date.strftime("%Y-%m-%d") if vb.bill_date else None,
                    "total_amount": float(vb.total_amount),
                    "paid_amount": float(vb.paid_amount),
                }
                for vb in order.vendor_bills if not vb.is_deleted
            ],
            "history": [
                {
                    "id": h.id,
                    "from_status": h.from_status,
                    "to_status": h.to_status,
                    "status_type": h.status_type,
                    "remarks": h.remarks,
                    "changed_by": {"name": h.changed_by.name if h.changed_by else "System"} if h.changed_by else None,
                    "created_at": h.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                for h in order.status_history
            ],
            "status_history": [
                {
                    "id": h.id,
                    "from_status": h.from_status,
                    "to_status": h.to_status,
                    "status_type": h.status_type,
                    "remarks": h.remarks,
                    "changed_by": {"name": h.changed_by.name if h.changed_by else "System"} if h.changed_by else None,
                    "created_at": h.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                for h in order.status_history
            ],
            "revisions": [
                {
                    "id": r.id,
                    "revision_number": r.revision_number,
                    "change_reason": r.change_reason,
                    "created_by": r.created_by.name if r.created_by else "System",
                    "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                for r in order.revisions
            ],
            "exceptional_closure": {
                "closure_type": order.exceptional_closure.closure_type,
                "closure_reason": order.exceptional_closure.closure_reason,
                "financial_impact_notes": order.exceptional_closure.financial_impact_notes,
                "approved_by": order.exceptional_closure.approved_by.name if order.exceptional_closure.approved_by else "System",
                "created_at": order.exceptional_closure.created_at.strftime("%Y-%m-%d %H:%M"),
            } if order.exceptional_closure else None,
            "documents": [
                {
                    "id": doc.id,
                    "title": doc.title,
                    "document_type": doc.document_type,
                    "file_size": doc.file_size,
                    "file_size_formatted": f"{(doc.file_size or 0) / 1024:.1f} KB" if (doc.file_size or 0) < 1024 * 1024 else f"{(doc.file_size or 0) / (1024 * 1024):.1f} MB",
                    "mime_type": doc.mime_type,
                    "is_customer_visible": doc.is_customer_visible,
                    "uploaded_by": doc.uploaded_by.name if doc.uploaded_by else "System",
                    "created_at": doc.created_at.strftime("%Y-%m-%d %H:%M"),
                }
                for doc in order.documents if not doc.is_deleted
            ],
            "initial_snapshot": initial_snapshot,
            "selected_vendors": initial_snapshot.get("selected_vendors", []),
            "customer_snapshot": initial_snapshot.get("customer_snapshot", {}),
            "commercial_details": initial_snapshot.get("commercial_details", {}),
            "delivery_details": initial_snapshot.get("delivery_details", {}),
            "installation_details": initial_snapshot.get("installation_details", {}),
            "financials": {
                "net_order_amount": float(order.net_order_amount),
                "total_procurement_cost": float(order.total_procurement_cost),
                "gross_profit": float(order.net_order_amount - order.total_procurement_cost),
                "gross_margin_percentage": round(float((order.net_order_amount - order.total_procurement_cost) / order.net_order_amount * 100), 2) if order.net_order_amount > Decimal("0.00") else 0.0,
                "total_customer_paid": float(order.total_customer_paid),
                "customer_payment_status": order.customer_payment_status,
                "total_vendor_billed": float(order.total_vendor_billed),
                "total_vendor_paid": float(order.total_vendor_paid),
                "vendor_payment_status": order.vendor_payment_status,
                "operating_cash_margin": float(order.total_customer_paid - order.total_vendor_paid),
            },
        }

    @classmethod
    def list_orders(
        cls,
        org_id: int,
        status: Optional[str] = None,
        customer_id: Optional[int] = None,
        search: Optional[str] = None,
        search_query: Optional[str] = None,
        status_filter: Optional[str] = None,
        procurement_status_filter: Optional[str] = None,
        delivery_status_filter: Optional[str] = None,
        customer_payment_status_filter: Optional[str] = None,
        page: int = 1,
        per_page: int = 20,
        **kwargs
    ):
        """Query orders for organization with filtering and pagination."""
        q = OrderMaster.query.filter_by(organization_id=org_id, is_deleted=False)

        actual_status = status_filter or status
        if actual_status:
            q = q.filter(OrderMaster.status == actual_status)

        if procurement_status_filter:
            q = q.filter(OrderMaster.procurement_status == procurement_status_filter)

        if delivery_status_filter:
            q = q.filter(OrderMaster.delivery_status == delivery_status_filter)

        if customer_payment_status_filter:
            q = q.filter(OrderMaster.customer_payment_status == customer_payment_status_filter)

        if customer_id:
            q = q.filter(OrderMaster.customer_id == customer_id)

        actual_search = search_query or search
        if actual_search:
            search_term = f"%{actual_search.strip()}%"
            q = q.join(Customer).filter(
                or_(
                    OrderMaster.master_order_number.ilike(search_term),
                    Customer.name.ilike(search_term),
                    Customer.phone_number.ilike(search_term),
                )
            )

        q = q.order_by(OrderMaster.id.desc())
        paginated = q.paginate(page=page, per_page=per_page, error_out=False)

        class PaginationResult:
            def __init__(self, p):
                self._p = p
                self.items = p.items
                self.page = p.page
                self.per_page = p.per_page
                self.total = p.total
                self.pages = p.pages
                self.has_prev = p.has_prev
                self.has_next = p.has_next

            def __getitem__(self, key):
                return getattr(self, key)

        return PaginationResult(paginated)

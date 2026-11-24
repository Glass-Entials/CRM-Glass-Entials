"""
services/oms/order_master_service.py
Core OrderMaster service implementing transactional lifecycle management,
parent-first concurrency locking, multi-vendor allocation invariants,
immutable status history, revisions, and cockpit data aggregation.
"""
import json
import logging
from decimal import Decimal
from datetime import datetime, date
from typing import List, Dict, Any, Optional, Union

from sqlalchemy import or_, desc, asc, func
from sqlalchemy.orm import joinedload, subqueryload
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
    Lead,
    LeadStatus,
    Vendor,
    Product,
    Quotation,
    Project,
    Employee,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderItemAllocationStatus,
    OrderClosureType,
    OrderDeliveryStatus,
    OrderDocumentType,
)

logger = logging.getLogger("oms")
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


def detect_lead_customer(org_id: int, lead: Lead) -> Optional[Customer]:
    """
    Detect whether a Lead is already associated with or converted to an existing Customer.
    Follows strict priority:
    1. Direct relationship / conversion reference:
       Customer.lead_id == lead.id
       or lead.converted_customer
    2. Normalized mobile matching (last 10 digits) within the same organization.
    3. Normalized email matching (case-insensitive stripped) within the same organization.
    NEVER matches by name alone. Never crosses tenant boundaries.
    """
    if not lead:
        return None

    try:
        # Priority 1: Direct conversion relationship
        cust = Customer.query.filter_by(lead_id=lead.id, organization_id=org_id, is_deleted=False).first()
        if cust:
            return cust

        if getattr(lead, "converted_customer", None):
            c = lead.converted_customer
            if c and not getattr(c, "is_deleted", False) and getattr(c, "organization_id", None) == org_id:
                return c

        # Priority 2: Normalized mobile match (last 10 digits)
        lead_phone = getattr(lead, "phone_number", None) or getattr(lead, "phone", None)
        if lead_phone:
            raw_digits = "".join(filter(str.isdigit, str(lead_phone)))
            if len(raw_digits) >= 10:
                target_10 = raw_digits[-10:]
                candidates = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
                for c in candidates:
                    c_phone = getattr(c, "phone_number", None) or getattr(c, "phone", None)
                    if c_phone:
                        c_digits = "".join(filter(str.isdigit, str(c_phone)))
                        if len(c_digits) >= 10 and c_digits[-10:] == target_10:
                            return c

        # Priority 3: Normalized email match
        lead_email = (getattr(lead, "email", None) or "").strip().lower()
        if lead_email and "@" in lead_email:
            candidates = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
            for c in candidates:
                c_email = (getattr(c, "email", None) or "").strip().lower()
                if c_email and c_email == lead_email:
                    return c
    except Exception as e:
        import logging
        logging.getLogger("oms").exception(f"Error detecting customer for lead #{getattr(lead, 'id', None)}: {e}")

    return None


def _get_lead_stage_label(status: str) -> str:
    _map = {
        "NEW": "Lead & Qualify",
        "ACTIVE": "Lead & Qualify",
        "PROSPECT": "Measurement",
        "INACTIVE": "Quotation",
        "CLOSED": "Order Confirmed",
    }
    return _map.get(str(status or "").upper(), "Lead & Qualify")


def _get_lead_stage_number(status: str) -> int:
    _map = {"NEW": 1, "ACTIVE": 1, "PROSPECT": 2, "INACTIVE": 3, "CLOSED": 4}
    return _map.get(str(status or "").upper(), 1)


def _get_order_stage_label(status: str) -> str:
    _map = {
        "DRAFT": "Order Confirmed",
        "CONFIRMED": "Order Confirmed",
        "IN_PROCUREMENT": "Vendor & QC",
        "PROCUREMENT": "Vendor & QC",
        "PARTIALLY_RECEIVED": "Vendor & QC",
        "RECEIVED": "Vendor & QC",
        "IN_DELIVERY": "Transport",
        "DELIVERY": "Transport",
        "PARTIALLY_DELIVERED": "Transport",
        "IN_TRANSIT": "Transport",
        "DELIVERED": "Unloading & Fitting",
        "IN_FITTING": "Unloading & Fitting",
        "FITTING": "Unloading & Fitting",
        "COMPLETED": "Unloading & Fitting",
        "CLOSED": "Unloading & Fitting",
        "CANCELLED": "Order Confirmed",
    }
    return _map.get(str(status or "").upper(), "Order Confirmed")


def _get_order_stage_number(status: str) -> int:
    _map = {
        "DRAFT": 4,
        "CONFIRMED": 4,
        "IN_PROCUREMENT": 5,
        "PROCUREMENT": 5,
        "PARTIALLY_RECEIVED": 5,
        "RECEIVED": 5,
        "IN_DELIVERY": 6,
        "DELIVERY": 6,
        "PARTIALLY_DELIVERED": 6,
        "IN_TRANSIT": 6,
        "DELIVERED": 7,
        "IN_FITTING": 7,
        "FITTING": 7,
        "COMPLETED": 7,
        "CLOSED": 7,
    }
    return _map.get(str(status or "").upper(), 4)


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
        customer_id: Optional[int] = None,
        items_data: List[Dict[str, Any]] = None,
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
        lead_id: Optional[int] = None,
        confirmed_lead_override: bool = False,
        entity_mode: str = "customer",
    ) -> OrderMaster:
        """Create a new Master Order with initial items and audit snapshot."""
        if not items_data:
            raise OMSValidationError("An order must contain at least one order item.")

        effective_notes = f"[{order_title}] {notes}" if order_title and notes else (order_title or notes)

        # 1. Tenant validation for client (Customer or Lead)
        customer = None
        lead = None

        if entity_mode == "lead" or (lead_id and not customer_id):
            if not lead_id:
                raise OMSValidationError("Master order in Lead mode must specify a valid Lead ID.")
            lead = Lead.query.filter_by(id=lead_id, organization_id=org_id, is_deleted=False).first()
            if not lead:
                raise OMSTenantIsolationError(f"Lead #{lead_id} not found in organization {org_id}.")

            detected_cust = detect_lead_customer(org_id, lead)
            if detected_cust:
                if confirmed_lead_override or (customer_id and customer_id == detected_cust.id):
                    customer = detected_cust
                    customer_id = detected_cust.id
                else:
                    raise OMSValidationError(
                        f"This lead is already registered as a customer in the CRM ('{detected_cust.name}'). Confirmation is required to continue with this lead."
                    )
            else:
                customer_id = None
            client_entity = lead
        else:
            if not customer_id:
                raise OMSValidationError("Master order must be associated with either an existing Customer or a Lead.")
            customer = Customer.query.filter_by(id=customer_id, organization_id=org_id, is_deleted=False).first()
            if not customer:
                raise OMSTenantIsolationError(f"Customer #{customer_id} not found in organization {org_id}.")
            lead = None
            client_entity = customer

        default_addr = getattr(client_entity, "address", None)

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
            lead_id=lead.id if lead else None,
            quotation_id=quotation_id,
            project_id=project_id,
            status=initial_status,
            order_date=order_date or datetime.utcnow(),
            expected_delivery_date=expected_delivery_date,
            expected_fitting_date=expected_fitting_date,
            shipping_address=shipping_address or default_addr,
            billing_address=billing_address or default_addr,
            site_address=site_address or shipping_address or default_addr,
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

        # 3. Create initial immutable OrderRevision (Revision 1) snapshot if requested or vendors selected
        if create_initial_revision or selected_vendor_ids:
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
                "id": client_entity.id if client_entity else None,
                "client_type": "LEAD" if (lead and not customer) else ("LEAD_CUSTOMER" if (lead and customer) else "CUSTOMER"),
                "is_lead": bool(lead and not customer),
                "lead_id": lead.id if lead else None,
                "customer_id": customer.id if customer else None,
                "name": client_entity.name if client_entity else "Unknown",
                "company": getattr(client_entity, "company", None),
                "email": getattr(client_entity, "email", None),
                "phone_number": getattr(client_entity, "phone_number", None) or getattr(client_entity, "phone", None),
                "gst_number": getattr(client_entity, "gst_number", None),
                "billing_address": order.billing_address,
                "shipping_address": order.shipping_address,
                "site_address": order.site_address,
                "city": getattr(client_entity, "city", None),
                "state": getattr(client_entity, "state", None),
                "pincode": getattr(client_entity, "pincode", None),
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
    def create_order_from_confirmed_lead(
        cls,
        org_id: int,
        lead_id: int,
        actor_id: Optional[int] = None,
        quotation_id: Optional[int] = None,
        project_id: Optional[int] = None,
        expected_delivery_date: Optional[Union[datetime, date]] = None,
        items_data: Optional[List[Dict[str, Any]]] = None,
        order_title: Optional[str] = None,
        notes: Optional[str] = None,
        selected_vendor_ids: Optional[List[int]] = None,
        is_explicit_confirmation: bool = False,
    ) -> OrderMaster:
        """
        Integration-ready, idempotent service contract to create a Master Order
        when a Lead reaches the 'Order Confirmed' milestone in Lead Management.

        Architectural Lifecycle Rules:
        - Lead Management exclusively owns: Lead & Qualify -> Measurement -> Quotation -> Order Confirmed
        - Order Management owns the operational lifecycle starting after Order Confirmed:
          Vendor & QC -> Transport -> Unloading & Fitting
        - An unconfirmed Lead is NOT an Order. A Master Order is created strictly when
          the Lead reaches or is explicitly transitioning to 'Order Confirmed'.

        Contract Guarantees:
        1. Tenant Isolation: Validates that the Lead belongs to the given org_id.
        2. Stage Validation: Rejects unconfirmed leads unless explicitly confirmed.
        3. Idempotency: If an active Master Order already exists for this lead_id in the organization,
           returns the existing order without creating duplicate records.
        4. Customer Linkage: Checks whether the lead has a converted/associated customer
           and preserves that reference in customer_id alongside lead_id.
        5. Carry-forward: Projects, addresses, notes, and line items carry forward into OMS.
        6. Audited Initialization: Initializes order in CONFIRMED state with an audit trail record.
        """
        if not lead_id or not org_id:
            raise OMSValidationError("Valid organization ID and Lead ID are required.")

        # 1. Tenant validation for the Lead
        lead = Lead.query.filter_by(id=lead_id, organization_id=org_id, is_deleted=False).first()
        if not lead:
            raise OMSTenantIsolationError(f"Lead #{lead_id} does not exist in organization #{org_id}.")

        # 2. Stage Validation: Lead must have reached 'Order Confirmed'
        lead_status_str = str(getattr(lead.status, "value", lead.status) or "").strip()
        is_confirmed_status = (
            lead_status_str.lower() in ["order confirmed", "confirmed", "closed", "won"]
            or getattr(lead, "is_confirmed", False) is True
        )

        if not is_confirmed_status and not is_explicit_confirmation:
            raise OMSValidationError(
                f"Cannot initialize Master Order: Lead #{lead_id} is in status '{lead_status_str}' and has not reached 'Order Confirmed'."
            )

        # 3. Idempotency & Duplicate Prevention: Check whether order already exists
        existing_order = OrderMaster.query.filter_by(
            organization_id=org_id,
            lead_id=lead_id,
            is_deleted=False,
        ).first()

        if existing_order:
            logger.info(
                f"[OMS Lead-to-Order] Order #{existing_order.master_order_number} already exists for confirmed Lead #{lead_id}. Returning existing order (idempotent)."
            )
            return existing_order

        # If lead was not marked confirmed yet but is_explicit_confirmation is True, update lead status
        if not is_confirmed_status and is_explicit_confirmation:
            try:
                if hasattr(lead, "status"):
                    lead.status = LeadStatus.CLOSED
                    db.session.add(lead)
            except Exception as e:
                logger.warning(f"Could not update Lead #{lead_id} status enum: {e}")

        # 4. Detect Customer association (preserve converted/associated customer)
        detected_cust = detect_lead_customer(org_id, lead)
        customer_id = detected_cust.id if detected_cust else None

        # 5. Resolve Actor
        if not actor_id:
            actor_id = getattr(lead, "assigned_to", None) or getattr(lead, "created_by", None)
            if not actor_id:
                first_emp = Employee.query.filter_by(organization_id=org_id).first()
                actor_id = first_emp.id if first_emp else 1

        # 6. Default items if none provided
        if not items_data:
            lead_display_name = getattr(lead, "company", None) or getattr(lead, "name", "Client")
            items_data = [
                {
                    "item_description": f"{lead_display_name} Architectural Glazing Package",
                    "quantity": Decimal("1.000"),
                    "unit_price": Decimal("0.00"),
                    "tax_rate": Decimal("18.00"),
                    "discount_amount": Decimal("0.00"),
                    "notes": "Initial package initialized from confirmed CRM lead",
                }
            ]

        # 7. Carry forward address and titles
        effective_title = order_title or f"{getattr(lead, 'company', None) or getattr(lead, 'name', 'Client')} — Confirmed Glazing Order"
        lead_addr = getattr(lead, "address", None) or getattr(lead, "city", None)

        order = cls.create_order(
            org_id=org_id,
            customer_id=customer_id,
            lead_id=lead.id,
            items_data=items_data,
            order_title=effective_title,
            quotation_id=quotation_id,
            project_id=project_id,
            expected_delivery_date=expected_delivery_date,
            shipping_address=lead_addr,
            billing_address=lead_addr,
            site_address=lead_addr,
            notes=notes or f"Order initialized from confirmed Lead #{lead.id} ({lead.name})",
            assigned_to_id=getattr(lead, "assigned_to", None),
            actor_id=actor_id,
            selected_vendor_ids=selected_vendor_ids,
            action_type="confirm_order",
            create_initial_revision=True,
            confirmed_lead_override=True,
            entity_mode="lead",
        )

        # 8. Record Lifecycle history entry in audit trail
        lifecycle_hist = OrderStatusHistory(
            order_id=order.id,
            organization_id=org_id,
            status_type="LIFECYCLE",
            from_status="LEAD_CONFIRMED",
            to_status=MasterOrderStatus.CONFIRMED.value,
            remarks=f"Master Order initialized via Lead-to-Order lifecycle from confirmed Lead #{lead.id} ({lead.name}).",
            changed_by_id=actor_id,
        )
        db.session.add(lifecycle_hist)
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
        Delegates to centralized OMSStatusEngine and records immutable OrderStatusHistory.
        """
        actual_target = to_status or target_status
        if not actual_target:
            raise OMSValidationError("Target status must be provided.")

        from services.oms.status_engine import OMSStatusEngine
        order = OMSStatusEngine.transition_master_status(
            org_id=org_id,
            order_id=order_id,
            to_status=actual_target,
            remarks=remarks,
            actor_id=actor_id,
        )
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

        # Derive human-friendly order title
        order_title = (
            initial_snapshot.get("order_title")
            or order.order_title
            or f"Order #{order.master_order_number}"
        )

        # Collect vendor proforma invoices
        v_pi_list = []
        for doc in order.documents:
            if not doc.is_deleted and (
                doc.document_type in ("VENDOR_PROFORMA_INVOICE", "PROFORMA_INVOICE")
                or "proforma invoice" in doc.title.lower()
                or "vendor pi" in doc.title.lower()
            ):
                v_name = "Supplier / Vendor"
                ref_num = "—"
                if "Vendor PI:" in doc.title:
                    parts = doc.title.split("Vendor PI:", 1)[-1].strip()
                    if " - " in parts:
                        subparts = parts.split(" - ", 1)
                        v_name = subparts[0].strip()
                        ref_num = subparts[1].strip()
                    else:
                        v_name = parts
                elif "Proforma Invoice —" in doc.title:
                    parts = doc.title.split("Proforma Invoice —", 1)[-1].strip()
                    if " — " in parts:
                        subparts = parts.split(" — ", 1)
                        v_name = subparts[0].strip()
                        ref_num = subparts[1].strip()
                    else:
                        v_name = parts
                elif " - " in doc.title:
                    parts = doc.title.split(" - ")
                    if len(parts) >= 2:
                        v_name = parts[0].strip()
                        ref_num = parts[1].strip()

                v_pi_list.append({
                    "id": doc.id,
                    "title": doc.title,
                    "vendor_name": v_name,
                    "reference_number": ref_num,
                    "document_type": doc.document_type,
                    "file_size": doc.file_size,
                    "file_size_formatted": f"{(doc.file_size or 0) / 1024:.1f} KB" if (doc.file_size or 0) < 1024 * 1024 else f"{(doc.file_size or 0) / (1024 * 1024):.1f} MB",
                    "mime_type": doc.mime_type,
                    "uploaded_by": doc.uploaded_by.name if doc.uploaded_by else "System",
                    "created_at": doc.created_at.strftime("%d %b %Y, %I:%M %p") if doc.created_at else "—",
                })

        # Collect delivery records with attached challan documents
        delv_list = []
        linked_doc_ids = set()
        for d in order.deliveries:
            if d.is_deleted:
                continue
            doc_obj = next((doc for doc in order.documents if doc.id == d.proof_of_delivery_document_id and not doc.is_deleted), None) if d.proof_of_delivery_document_id else None
            if doc_obj:
                linked_doc_ids.add(doc_obj.id)
            delv_list.append({
                "id": d.id,
                "delivery_number": d.delivery_number,
                "challan_number": d.challan_number,
                "status": d.status,
                "delivery_date": d.delivery_date.strftime("%d %b %Y") if d.delivery_date else (d.created_at.strftime("%d %b %Y") if d.created_at else None),
                "delivered_by": {"name": d.delivered_by.name if d.delivered_by else "Staff"} if d.delivered_by else None,
                "recipient_name": d.recipient_name,
                "recipient_phone": d.recipient_phone,
                "document_id": doc_obj.id if doc_obj else None,
                "document_title": doc_obj.title if doc_obj else None,
                "file_size_formatted": (
                    f"{(doc_obj.file_size or 0) / 1024:.1f} KB" if (doc_obj.file_size or 0) < 1024 * 1024 else f"{(doc_obj.file_size or 0) / (1024 * 1024):.1f} MB"
                ) if doc_obj else None,
            })

        for doc in order.documents:
            if not doc.is_deleted and doc.document_type == OrderDocumentType.DELIVERY_CHALLAN.value and doc.id not in linked_doc_ids:
                delv_list.append({
                    "id": 0,
                    "delivery_number": f"DOC-{doc.id}",
                    "challan_number": doc.title,
                    "status": OrderDeliveryStatus.DELIVERED.value,
                    "delivery_date": doc.created_at.strftime("%d %b %Y") if doc.created_at else None,
                    "delivered_by": {"name": doc.uploaded_by.name if doc.uploaded_by else "System"},
                    "recipient_name": order.customer.name if order.customer else "Site Recipient",
                    "recipient_phone": order.customer.phone_number if order.customer else None,
                    "document_id": doc.id,
                    "document_title": doc.title,
                    "file_size_formatted": (
                        f"{(doc.file_size or 0) / 1024:.1f} KB" if (doc.file_size or 0) < 1024 * 1024 else f"{(doc.file_size or 0) / (1024 * 1024):.1f} MB"
                    ),
                })

        # Serialize order header and child collections
        return {
            "vendor_proforma_invoices": v_pi_list,
            "order": {
                "id": order.id,
                "master_order_number": order.master_order_number,
                "order_title": order_title,
                "status": order.status,
                "procurement_status": order.procurement_status,
                "delivery_status": order.delivery_status,
                "fitting_status": order.fitting_status,
                "customer_payment_status": order.customer_payment_status,
                "vendor_payment_status": order.vendor_payment_status,
                "order_date": order.order_date.strftime("%d %b %Y") if order.order_date else "—",
                "order_date_iso": order.order_date.strftime("%Y-%m-%d %H:%M") if order.order_date else None,
                "expected_delivery_date": order.expected_delivery_date.strftime("%d %b %Y") if order.expected_delivery_date else None,
                "expected_delivery_date_iso": order.expected_delivery_date.strftime("%Y-%m-%d") if order.expected_delivery_date else None,
                "actual_delivery_date": order.actual_delivery_date.strftime("%d %b %Y") if order.actual_delivery_date else None,
                "expected_fitting_date": order.expected_fitting_date.strftime("%d %b %Y") if order.expected_fitting_date else None,
                "actual_fitting_date": order.actual_fitting_date.strftime("%d %b %Y") if order.actual_fitting_date else None,
                "shipping_address": order.shipping_address,
                "billing_address": order.billing_address,
                "site_address": order.site_address,
                "notes": order.notes,
                "priority": getattr(order, 'priority', 'MEDIUM') or 'MEDIUM',
                "customer_po_number": getattr(order, 'customer_po_number', None),
                "customer_po_date": order.customer_po_date.strftime("%d %b %Y") if getattr(order, 'customer_po_date', None) else None,
                "customer_po_date_iso": order.customer_po_date.strftime("%Y-%m-%d") if getattr(order, 'customer_po_date', None) else None,
                "version_id": order.version_id,
                "total_ordered_amount": float(order.total_ordered_amount),
                "total_tax_amount": float(order.total_tax_amount),
                "total_discount_amount": float(order.total_discount_amount),
                "net_order_amount": float(order.net_order_amount),
                "total_procurement_cost": float(order.total_procurement_cost),
                "total_customer_paid": float(order.total_customer_paid),
                "total_vendor_billed": float(order.total_vendor_billed),
                "total_vendor_paid": float(order.total_vendor_paid),
                "lead_id": order.lead_id,
                "client_type": order.client_type,
                "client_name": order.client_name,
                "customer": {
                    "id": order.customer.id if order.customer else (order.lead.id if order.lead else None),
                    "name": order.customer.name if order.customer else (order.lead.name if order.lead else "Unknown"),
                    "email": order.customer.email if order.customer else (order.lead.email if order.lead else ""),
                    "phone": (order.customer.phone_number if order.customer else (order.lead.phone_number if order.lead else "")),
                    "mobile": (order.customer.phone_number if order.customer else (order.lead.phone_number if order.lead else "")),
                    "company": order.customer.company if order.customer else (order.lead.company if order.lead else None),
                    "gst_number": getattr(order.customer, 'gst_number', None) if order.customer else None,
                    "address": getattr(order.customer, 'address', None) if order.customer else None,
                    "is_lead": bool(order.lead_id and not order.customer_id),
                    "lead_id": order.lead_id,
                    "customer_id": order.customer_id,
                },
                "lead": {
                    "id": order.lead.id,
                    "name": order.lead.name,
                    "company": order.lead.company,
                    "phone_number": order.lead.phone_number,
                    "email": order.lead.email,
                    "status": order.lead.status.value if hasattr(order.lead.status, 'value') else str(order.lead.status or ''),
                    "source": order.lead.source.value if (order.lead.source and hasattr(order.lead.source, 'value')) else str(order.lead.source or ''),
                    "city": getattr(order.lead, 'city', None),
                    "address": getattr(order.lead, 'address', None),
                    "created_at": order.lead.created_at.strftime("%d %b %Y") if order.lead.created_at else None,
                    "lead_number": getattr(order.lead, 'lead_number', None) or f"GLS-LEAD-{order.lead.id:04d}",
                } if order.lead else None,
                "assigned_to": {
                    "id": order.assigned_to.id,
                    "name": order.assigned_to.name,
                } if order.assigned_to else None,
                "quotation": {
                    "id": order.quotation.id if order.quotation else None,
                    "quotation_number": order.quotation.quotation_number if order.quotation else None,
                    "status": order.quotation.status.value if (order.quotation and hasattr(order.quotation.status, 'value')) else (str(order.quotation.status) if order.quotation else None),
                    "total_amount": float(order.quotation.total_amount) if (order.quotation and hasattr(order.quotation, 'total_amount') and order.quotation.total_amount is not None) else None,
                    "quotation_date": order.quotation.created_at.strftime("%d %b %Y") if (order.quotation and order.quotation.created_at) else None,
                    "customer_decision": getattr(order.quotation, 'customer_decision', None) if order.quotation else None,
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
                    "tracking_number": c.lr_number or getattr(c, 'tracking_reference', None) or '',
                    "status": c.status,
                    "dispatch_date": c.dispatch_date.strftime("%d %b %Y") if c.dispatch_date else None,
                    "dispatch_date_iso": c.dispatch_date.strftime("%Y-%m-%d") if c.dispatch_date else None,
                    "expected_arrival_date": c.expected_arrival_date.strftime("%d %b %Y") if c.expected_arrival_date else None,
                    "expected_arrival_date_iso": c.expected_arrival_date.strftime("%Y-%m-%d") if c.expected_arrival_date else None,
                    "actual_arrival_date": c.actual_arrival_date.strftime("%d %b %Y") if c.actual_arrival_date else None,
                    "shipping_cost": float(c.shipping_cost or Decimal("0.00")),
                    "driver_name": getattr(c, 'driver_name', None),
                    "driver_contact": getattr(c, 'driver_contact', None),
                    "pickup_location": getattr(c, 'pickup_location', None),
                    "packing_verified": getattr(c, 'packing_verified', False),
                    "site_receiver_name": getattr(c, 'site_receiver_name', None),
                    "site_receiver_contact": getattr(c, 'site_receiver_contact', None),
                    "pod_signed": getattr(c, 'pod_signed', False),
                    "pod_signed_at": c.pod_signed_at.strftime("%d %b %Y") if getattr(c, 'pod_signed_at', None) else None,
                    "failure_reason": getattr(c, 'failure_reason', None),
                    "notes": c.notes,
                    "vendor_name": (
                        c.procurement.vendor.name if (c.procurement and c.procurement.vendor)
                        else (
                            c.notes.split("[Vendor: ")[1].split("]")[0]
                            if c.notes and "[Vendor: " in c.notes
                            else None
                        )
                    ),
                    "procurement": {"po_number": c.procurement.po_number if c.procurement else None},
                }
                for c in order.consignments if not c.is_deleted
            ],
            "deliveries": delv_list,
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
                "net_order_amount": float(order.net_order_amount or Decimal("0.00")),
                "total_tax_amount": float(order.total_tax_amount or Decimal("0.00")),
                "total_ordered_amount": float(order.total_ordered_amount or Decimal("0.00")),
                "total_discount_amount": float(order.total_discount_amount or Decimal("0.00")),
                "total_procurement_cost": float(order.total_procurement_cost or Decimal("0.00")),
                "gross_profit": float((order.net_order_amount or Decimal("0.00")) - (order.total_procurement_cost or Decimal("0.00"))),
                "gross_margin_percentage": round(float(((order.net_order_amount or Decimal("0.00")) - (order.total_procurement_cost or Decimal("0.00"))) / order.net_order_amount * 100), 2) if (order.net_order_amount or Decimal("0.00")) > Decimal("0.00") else 0.0,
                "total_customer_paid": float(order.total_customer_paid or Decimal("0.00")),
                "customer_outstanding": float(max(Decimal("0.00"), ((order.net_order_amount or Decimal("0.00")) + (order.total_tax_amount or Decimal("0.00"))) - (order.total_customer_paid or Decimal("0.00")))),
                "customer_payment_status": order.customer_payment_status,
                "total_vendor_billed": float(order.total_vendor_billed or Decimal("0.00")),
                "total_vendor_paid": float(order.total_vendor_paid or Decimal("0.00")),
                "vendor_outstanding": float(max(Decimal("0.00"), (order.total_vendor_billed or Decimal("0.00")) - (order.total_vendor_paid or Decimal("0.00")))),
                "vendor_payment_status": order.vendor_payment_status,
                "operating_cash_margin": float((order.total_customer_paid or Decimal("0.00")) - (order.total_vendor_paid or Decimal("0.00"))),
            },
            "lifecycle": {
                "lead_stage": {
                    "status": (order.lead.status.value if hasattr(order.lead.status, 'value') else str(order.lead.status)) if order.lead else None,
                    "label": _get_lead_stage_label((order.lead.status.value if hasattr(order.lead.status, 'value') else str(order.lead.status)) if order.lead else None),
                    "stage_number": _get_lead_stage_number((order.lead.status.value if hasattr(order.lead.status, 'value') else str(order.lead.status)) if order.lead else None),
                },
                "order_stage": {
                    "status": order.status,
                    "label": _get_order_stage_label(order.status),
                    "stage_number": _get_order_stage_number(order.status),
                },
            },
        }

    @classmethod
    def build_tracking_card_data(cls, order: OrderMaster) -> Dict[str, Any]:
        """
        Build authoritative, production-grade tracking card data for the redesigned Order Dashboard.
        100% derived from persisted order attributes, operational records, and child entities.
        Zero hardcoded fake statuses.
        """
        # 1. Identity & References
        lead_ref = None
        if order.lead_id:
            lead_year = order.lead.created_at.year if (order.lead and getattr(order.lead, "created_at", None)) else 2026
            lead_ref = f"GLS-LEAD-{lead_year}-{order.lead_id:05d}"

        # 2. Project, Client & PM info
        project_title = order.project.name if order.project else order.order_title
        project_location = order.site_address or (order.customer.city if order.customer and getattr(order.customer, "city", None) else (order.lead.city if order.lead and getattr(order.lead, "city", None) else "Site Location"))
        customer_name = order.customer.name if order.customer else (order.lead.name if order.lead else "No Client Associated")
        assigned_pm = order.assigned_to.name if order.assigned_to else (order.created_by.name if order.created_by else "Unassigned")
        
        now = datetime.utcnow()
        is_overdue = bool(order.expected_delivery_date and order.expected_delivery_date < now and order.status not in [MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value, MasterOrderStatus.CANCELLED.value])

        # 3. Financial calculations (Decimal safe)
        net_amount = float(order.net_order_amount or Decimal("0.00"))
        tax_amount = float(order.total_tax_amount or Decimal("0.00"))
        customer_paid = float(order.total_customer_paid or Decimal("0.00"))
        balance_due = max(0.0, net_amount - customer_paid)
        advance_pct = round((customer_paid / net_amount) * 100) if net_amount > 0 else 0

        # Customer Payment Status State
        if balance_due <= 0.01 and customer_paid > 0:
            payment_status_label = "Fully Paid"
            payment_status_class = "pay-paid"
        elif customer_paid <= 0:
            payment_status_label = "Advance Pending"
            payment_status_class = "pay-unpaid"
        elif is_overdue and balance_due > 0:
            payment_status_label = "Payment Overdue"
            payment_status_class = "pay-overdue"
        elif customer_paid > 0 and balance_due > 0:
            payment_status_label = "Advance Paid - Balance Due"
            payment_status_class = "pay-partial"
        else:
            payment_status_label = (order.customer_payment_status or "UNPAID").replace("_", " ").title()
            payment_status_class = "pay-neutral"

        # 4. Workflow Progress (3 Primary Stages: Vendor & QC, Transport, Unloading & Fitting)
        # Stage 1: Vendor & QC
        stage1_completed = (
            order.status in [MasterOrderStatus.IN_DELIVERY.value, MasterOrderStatus.PARTIALLY_DELIVERED.value, MasterOrderStatus.DELIVERED.value, MasterOrderStatus.IN_FITTING.value, MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value]
            or order.procurement_status in [OrderProcurementStatus.RECEIVED.value, "COMPLETED"]
        )
        stage1_active = (
            order.status in [MasterOrderStatus.CONFIRMED.value, MasterOrderStatus.IN_PROCUREMENT.value, MasterOrderStatus.PARTIALLY_RECEIVED.value]
            or order.procurement_status in [OrderProcurementStatus.PO_ISSUED.value, OrderProcurementStatus.IN_TRANSIT.value, OrderProcurementStatus.PARTIALLY_RECEIVED.value]
        ) and not stage1_completed

        # Check for QC issues across consignments
        has_qc_failure = False
        all_qc_passed = False
        qc_item_count = 0
        passed_count = 0
        if order.consignments:
            for c in order.consignments:
                if not getattr(c, "is_deleted", False):
                    for ci in c.items:
                        if not getattr(ci, "is_deleted", False):
                            qc_item_count += 1
                            if str(getattr(ci, "qc_status", "")).upper() in ["REJECTED", "FAILED"]:
                                has_qc_failure = True
                            elif str(getattr(ci, "qc_status", "")).upper() in ["PASSED", "ACCEPTED"]:
                                passed_count += 1
            if qc_item_count > 0 and passed_count == qc_item_count:
                all_qc_passed = True

        if has_qc_failure:
            stage1_state = "warning"
            stage1_detail = "QC Issues / Item Rejected"
            stage1_icon = "⚠"
        elif stage1_completed:
            stage1_state = "completed"
            stage1_detail = "QC Cleared · Fabrication Complete"
            stage1_icon = "✓"
        elif stage1_active:
            stage1_state = "active"
            stage1_detail = "In Production & Procurement"
            stage1_icon = "⚙"
        else:
            stage1_state = "pending"
            stage1_detail = "Pending Allocation"
            stage1_icon = "○"

        # Stage 2: Transport
        stage2_completed = (
            order.delivery_status == OrderDeliveryStatus.DELIVERED.value
            or order.status in [MasterOrderStatus.DELIVERED.value, MasterOrderStatus.IN_FITTING.value, MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value]
        )
        stage2_active = (
            order.delivery_status in [OrderDeliveryStatus.OUT_FOR_DELIVERY.value, "PARTIALLY_DELIVERED"]
            or order.status in [MasterOrderStatus.IN_DELIVERY.value, MasterOrderStatus.PARTIALLY_DELIVERED.value]
        ) and not stage2_completed

        if is_overdue and not stage2_completed:
            stage2_state = "warning"
            stage2_detail = "Transit Schedule Overdue"
            stage2_icon = "⚠"
        elif stage2_completed:
            stage2_state = "completed"
            stage2_detail = "Delivered to Project Site"
            stage2_icon = "✓"
        elif stage2_active:
            stage2_state = "active"
            stage2_detail = "In Transit / Out for Delivery"
            stage2_icon = "🚚"
        else:
            stage2_state = "pending"
            stage2_detail = "Scheduled / Awaiting Dispatch"
            stage2_icon = "○"

        # Stage 3: Unloading & Fitting
        stage3_completed = (
            order.fitting_status == OrderFittingStatus.COMPLETED.value
            or order.status in [MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value]
            or (order.fitting_status == OrderFittingStatus.NOT_REQUIRED.value and stage2_completed)
        )
        stage3_active = (
            order.status == MasterOrderStatus.IN_FITTING.value
            or order.fitting_status in [OrderFittingStatus.SCHEDULED.value, OrderFittingStatus.IN_PROGRESS.value, OrderFittingStatus.PARTIALLY_FITTED.value]
        ) and not stage3_completed

        if stage3_completed:
            stage3_state = "completed"
            stage3_detail = "Fitting Complete & Handover Done" if order.fitting_status != OrderFittingStatus.NOT_REQUIRED.value else "Delivery Complete (Fitting Not Req.)"
            stage3_icon = "✓"
        elif stage3_active:
            stage3_state = "active"
            stage3_detail = "Installation & Glazing Active"
            stage3_icon = "🔧"
        elif order.fitting_status == OrderFittingStatus.NOT_REQUIRED.value:
            stage3_state = "pending"
            stage3_detail = "Fitting Not Required"
            stage3_icon = "—"
        else:
            stage3_state = "pending"
            stage3_detail = "Pending Site Installation"
            stage3_icon = "○"

        # 5. Operational Summary: Vendors
        vendor_names = []
        if order.procurements:
            for p in order.procurements:
                if not getattr(p, "is_deleted", False) and p.vendor:
                    v_name = p.vendor.company_name
                    if v_name and v_name not in vendor_names:
                        vendor_names.append(v_name)
        if not vendor_names and order.vendor_allocations:
            for va in order.vendor_allocations:
                if not getattr(va, "is_deleted", False) and va.vendor:
                    v_name = va.vendor.company_name
                    if v_name and v_name not in vendor_names:
                        vendor_names.append(v_name)
        if not vendor_names and order.revisions:
            for rev in reversed(order.revisions):
                try:
                    snap = json.loads(rev.snapshot_json) if getattr(rev, "snapshot_json", None) else (getattr(rev, "snapshot_data", None) or {})
                except Exception:
                    snap = getattr(rev, "snapshot_data", None) or {}
                sel_v = snap.get("selected_vendors", [])
                for sv in sel_v:
                    v_name = sv.get("company_name") or sv.get("name")
                    if v_name and v_name not in vendor_names:
                        vendor_names.append(v_name)
                if vendor_names:
                    break

        if len(vendor_names) == 1:
            vendor_display = vendor_names[0]
        elif len(vendor_names) == 2:
            vendor_display = f"{vendor_names[0]}, {vendor_names[1]}"
        elif len(vendor_names) > 2:
            vendor_display = f"{vendor_names[0]}, {vendor_names[1]} (+{len(vendor_names)-2} more)"
        else:
            vendor_display = "No Vendors Linked"

        # Operational Summary: Pre-Dispatch QC
        if has_qc_failure:
            qc_status_label = "Failed"
            qc_badge_class = "qc-failed"
        elif all_qc_passed and qc_item_count > 0:
            qc_status_label = "Passed"
            qc_badge_class = "qc-passed"
        elif qc_item_count > 0:
            qc_status_label = "Pending Inspection"
            qc_badge_class = "qc-pending"
        elif stage1_active or order.procurements:
            qc_status_label = "Pending Inspection"
            qc_badge_class = "qc-pending"
        else:
            qc_status_label = "Not Scheduled"
            qc_badge_class = "qc-neutral"

        # Operational Summary: Delivery / Transit
        delivery_status_label = (order.delivery_status or "SCHEDULED").replace("_", " ").title()
        transporter_name = None
        if order.consignments:
            for c in order.consignments:
                if not getattr(c, "is_deleted", False) and c.transporter_name:
                    transporter_name = c.transporter_name
                    break

        # 6. Latest Milestone
        latest_milestone = None
        latest_milestone_time = None
        if order.status_history:
            sorted_hist = sorted(order.status_history, key=lambda h: h.created_at or datetime.min)
            last_h = sorted_hist[-1]
            latest_milestone = last_h.remarks or f"{last_h.status_type}: {last_h.to_status}"
            latest_milestone_time = last_h.created_at.strftime("%d %b %Y, %I:%M %p") if last_h.created_at else None
        elif order.notes:
            latest_milestone = order.notes
            latest_milestone_time = order.created_at.strftime("%d %b %Y") if order.created_at else None
        else:
            latest_milestone = "No milestone updates available."

        return {
            "order_id": order.id,
            "master_order_number": order.master_order_number,
            "lead_ref": lead_ref,
            "lead_id": order.lead_id,
            "customer_id": order.customer_id,
            "customer_name": customer_name,
            "client_type": order.client_type,
            "project_title": project_title,
            "project_location": project_location,
            "assigned_pm": assigned_pm,
            "order_date_formatted": order.order_date.strftime("%d %b %Y") if order.order_date else "—",
            "expected_delivery_formatted": order.expected_delivery_date.strftime("%d %b %Y") if order.expected_delivery_date else "Not Set",
            "is_overdue": is_overdue,
            "status": order.status,
            "net_amount": net_amount,
            "order_value_formatted": f"₹{net_amount:,.2f}",
            "advance_amount": customer_paid,
            "advance_display": f"₹{customer_paid:,.0f} ({advance_pct}%)",
            "balance_due": balance_due,
            "balance_due_display": f"₹{balance_due:,.0f}",
            "payment_status_label": payment_status_label,
            "payment_status_class": payment_status_class,
            "vendor_display": vendor_display,
            "vendor_count": len(vendor_names),
            "qc_status_label": qc_status_label,
            "qc_badge_class": qc_badge_class,
            "delivery_status_label": delivery_status_label,
            "transporter_name": transporter_name,
            "latest_milestone": latest_milestone,
            "latest_milestone_time": latest_milestone_time,
            "workflow": {
                "stage1": {
                    "label": "Vendor & QC",
                    "state": stage1_state,
                    "detail": stage1_detail,
                    "icon": stage1_icon,
                },
                "stage2": {
                    "label": "Transport",
                    "state": stage2_state,
                    "detail": stage2_detail,
                    "icon": stage2_icon,
                },
                "stage3": {
                    "label": "Unloading & Fitting",
                    "state": stage3_state,
                    "detail": stage3_detail,
                    "icon": stage3_icon,
                },
            },
        }

    @classmethod
    def list_orders(
        cls,
        org_id: int,
        status: Optional[str] = None,
        customer_id: Optional[int] = None,
        lead_id: Optional[int] = None,
        vendor_id: Optional[int] = None,
        assigned_to_id: Optional[int] = None,
        search: Optional[str] = None,
        search_query: Optional[str] = None,
        status_filter: Optional[str] = None,
        procurement_status_filter: Optional[str] = None,
        delivery_status_filter: Optional[str] = None,
        customer_payment_status_filter: Optional[str] = None,
        due_date_filter: Optional[str] = None,
        sort_by: Optional[str] = "newest",
        scope: Optional[str] = "active",
        page: int = 1,
        per_page: int = 15,
        **kwargs
    ):
        """Query orders for organization with comprehensive filtering, search, sorting, and eager loading."""
        q = OrderMaster.query.filter_by(organization_id=org_id, is_deleted=False)

        # 1. Scope / Status Filtering
        # Distinction: Active Operational Orders vs Draft Orders
        actual_status = status_filter or status
        if scope == "draft" or actual_status == MasterOrderStatus.DRAFT.value:
            q = q.filter(OrderMaster.status == MasterOrderStatus.DRAFT.value)
        elif actual_status in [MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value]:
            q = q.filter(OrderMaster.status.in_([MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value]))
        elif actual_status:
            q = q.filter(OrderMaster.status == actual_status)
        elif scope == "all":
            pass
        else:
            # Default active operational view: strictly active operational orders (excludes draft, completed, closed, cancelled)
            from services.oms.status_engine import ACTIVE_MASTER_STATUSES
            q = q.filter(OrderMaster.status.in_(ACTIVE_MASTER_STATUSES))

        if procurement_status_filter:
            q = q.filter(OrderMaster.procurement_status == procurement_status_filter)

        if delivery_status_filter:
            q = q.filter(OrderMaster.delivery_status == delivery_status_filter)

        if customer_payment_status_filter:
            q = q.filter(OrderMaster.customer_payment_status == customer_payment_status_filter)

        if customer_id:
            q = q.filter(OrderMaster.customer_id == customer_id)

        if lead_id:
            q = q.filter(OrderMaster.lead_id == lead_id)

        if vendor_id:
            q = q.filter(
                or_(
                    OrderMaster.procurements.any(OrderVendorProcurement.vendor_id == vendor_id),
                    OrderMaster.vendor_allocations.any(OrderItemVendorAllocation.vendor_id == vendor_id),
                )
            )

        if assigned_to_id:
            q = q.filter(OrderMaster.assigned_to_id == assigned_to_id)

        # Due Date Filtering
        if due_date_filter == "overdue":
            now_utc = datetime.utcnow()
            q = q.filter(
                OrderMaster.expected_delivery_date != None,
                OrderMaster.expected_delivery_date < now_utc,
                ~OrderMaster.status.in_([MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value, MasterOrderStatus.CANCELLED.value]),
            )
        elif due_date_filter == "today":
            today_start = datetime.combine(date.today(), datetime.min.time())
            today_end = datetime.combine(date.today(), datetime.max.time())
            q = q.filter(
                OrderMaster.expected_delivery_date >= today_start,
                OrderMaster.expected_delivery_date <= today_end,
            )
        elif due_date_filter == "upcoming":
            today_start = datetime.combine(date.today(), datetime.min.time())
            q = q.filter(OrderMaster.expected_delivery_date >= today_start)

        # 2. Universal Search across Order #, Notes, Customer, Lead, and Project
        actual_search = search_query or search
        if actual_search:
            search_term = f"%{actual_search.strip()}%"
            q = q.outerjoin(Customer, OrderMaster.customer_id == Customer.id)\
                 .outerjoin(Lead, OrderMaster.lead_id == Lead.id)\
                 .outerjoin(Project, OrderMaster.project_id == Project.id)\
                 .filter(
                     or_(
                         OrderMaster.master_order_number.ilike(search_term),
                         OrderMaster.notes.ilike(search_term),
                         Customer.name.ilike(search_term),
                         Customer.phone_number.ilike(search_term),
                         Customer.company.ilike(search_term),
                         Lead.name.ilike(search_term),
                         Lead.phone_number.ilike(search_term),
                         Lead.company.ilike(search_term),
                         Project.name.ilike(search_term),
                     )
                 )

        # 3. Sorting
        if sort_by == "oldest":
            q = q.order_by(OrderMaster.id.asc())
        elif sort_by == "due_date_asc":
            q = q.order_by(OrderMaster.expected_delivery_date.asc().nullslast(), OrderMaster.id.desc())
        elif sort_by == "due_date_desc":
            q = q.order_by(OrderMaster.expected_delivery_date.desc().nullslast(), OrderMaster.id.desc())
        elif sort_by == "value_desc":
            q = q.order_by(OrderMaster.net_order_amount.desc(), OrderMaster.id.desc())
        elif sort_by == "value_asc":
            q = q.order_by(OrderMaster.net_order_amount.asc(), OrderMaster.id.desc())
        else:
            q = q.order_by(OrderMaster.id.desc())

        # 4. Eager loading to completely eliminate N+1 database queries
        q = q.options(
            joinedload(OrderMaster.customer),
            joinedload(OrderMaster.lead),
            joinedload(OrderMaster.project),
            joinedload(OrderMaster.assigned_to),
            joinedload(OrderMaster.created_by),
            subqueryload(OrderMaster.procurements).joinedload(OrderVendorProcurement.vendor),
            subqueryload(OrderMaster.consignments).subqueryload(OrderConsignment.items),
            subqueryload(OrderMaster.deliveries),
            subqueryload(OrderMaster.fitting_visits),
            subqueryload(OrderMaster.status_history),
            subqueryload(OrderMaster.revisions),
        )

        paginated = q.paginate(page=page, per_page=per_page, error_out=False)

        # 5. Attach computed tracking cards to each order
        for ord_item in paginated.items:
            ord_item.tracking_card = cls.build_tracking_card_data(ord_item)

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

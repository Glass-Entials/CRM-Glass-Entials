"""
services/oms/financial_reconciliation_service.py
Service managing zero-double-counting customer payment ledger allocations,
3-way Accounts Payable matching for vendor bills, vendor disbursements,
and full order financial reconciliation.
"""
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Any, Optional

from sqlalchemy import func
from model import (
    db,
    OrderMaster,
    OrderProcurementItem,
    OrderVendorProcurement,
    DeliveryItem,
    Payment,
    Invoice,
    OrderPaymentAllocation,
    VendorBill,
    VendorBillItem,
    VendorPayment,
    OrderStatusHistory,
    OrderPaymentAllocationType,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    VendorBillStatus,
    Employee,
)
from . import (
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)
from .order_master_service import OrderMasterService


class FinancialReconciliationService:
    """Service managing customer cash allocations, vendor bill 3-way matching, and margins."""

    @staticmethod
    def generate_vendor_payment_reference(org_id: int) -> str:
        """Generate unique vendor payment reference per tenant: VPAY-{year}-{seq:05d}."""
        year = datetime.utcnow().year
        prefix = f"VPAY-{year}-"
        last = (
            VendorPayment.query
            .filter(
                VendorPayment.organization_id == org_id,
                VendorPayment.payment_reference.like(f"{prefix}%")
            )
            .order_by(VendorPayment.id.desc())
            .first()
        )
        seq = 1
        if last and last.payment_reference:
            try:
                seq = int(last.payment_reference.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = last.id + 1
        return f"{prefix}{seq:05d}"

    @classmethod
    def allocate_customer_payment(
        cls,
        org_id: int,
        order_id: int,
        payment_id: int,
        allocated_amount: Any,
        allocation_type: str = OrderPaymentAllocationType.DIRECT_PAYMENT.value,
        invoice_id: Optional[int] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderPaymentAllocation:
        """
        Allocate a customer payment to an order, with zero double-counting arithmetic.
        Supports DIRECT_PAYMENT, INVOICE_SETTLEMENT, and ADVANCE_UNLINK (negative balance offset).
        """
        # 1. Acquire parent-first lock
        order = OrderMasterService.lock_order(org_id, order_id)

        # 2. Verify Payment exists in tenant
        payment = Payment.query.filter_by(id=payment_id, organization_id=org_id, is_deleted=False).first()
        if not payment:
            raise OMSTenantIsolationError(f"Payment {payment_id} not found in this organization.")

        # 3. Optional invoice verification
        if invoice_id:
            invoice = Invoice.query.filter_by(id=invoice_id, organization_id=org_id).first()
            if not invoice:
                raise OMSTenantIsolationError(f"Invoice {invoice_id} not found in this organization.")
            if invoice.customer_id != order.customer_id:
                raise OMSValidationError(
                    f"Invoice customer ({invoice.customer_id}) does not match order customer ({order.customer_id})."
                )

        raw_amount = Decimal(str(allocated_amount))

        # Handle ADVANCE_UNLINK as negative adjustment to prevent double-counting when settled against invoice
        is_unlink = allocation_type in (
            OrderPaymentAllocationType.ADVANCE_UNLINK.value,
            "ADVANCE_UNLINK",
            "CREDIT_NOTE_ADJUSTMENT"
        )
        if is_unlink:
            actual_amount = -abs(raw_amount)
        else:
            actual_amount = abs(raw_amount)
            if actual_amount <= Decimal("0.00"):
                raise OMSValidationError("Allocated payment amount must be strictly greater than 0.")

            # Invariant: cumulative positive allocations for a payment across all orders cannot exceed payment.amount
            total_allocated_for_payment = (
                db.session.query(func.coalesce(func.sum(OrderPaymentAllocation.allocated_amount), Decimal("0.00")))
                .filter(
                    OrderPaymentAllocation.payment_id == payment_id,
                    OrderPaymentAllocation.organization_id == org_id,
                )
                .scalar()
            )
            payment_limit = Decimal(str(payment.amount))
            if (total_allocated_for_payment + actual_amount) > payment_limit:
                raise OMSInvariantError(
                    f"Cumulative payment allocations ({total_allocated_for_payment + actual_amount}) "
                    f"exceed total payment amount ({payment_limit}) for payment {payment.payment_number}."
                )

        prev_status = order.customer_payment_status

        allocation = OrderPaymentAllocation(
            order_id=order.id,
            organization_id=org_id,
            payment_id=payment.id,
            invoice_id=invoice_id,
            allocation_type=allocation_type,
            allocated_amount=actual_amount,
            allocation_date=datetime.utcnow(),
            notes=notes,
            allocated_by_id=actor_id or order.created_by_id,
        )
        db.session.add(allocation)
        db.session.flush()

        # Recompute total customer paid from ledger
        total_paid_net = (
            db.session.query(func.coalesce(func.sum(OrderPaymentAllocation.allocated_amount), Decimal("0.00")))
            .filter(
                OrderPaymentAllocation.order_id == order.id,
                OrderPaymentAllocation.organization_id == org_id,
            )
            .scalar()
        )
        order.total_customer_paid = max(Decimal("0.00"), total_paid_net)

        # Update customer payment status
        if order.total_customer_paid <= Decimal("0.00"):
            order.customer_payment_status = OrderCustomerPaymentStatus.UNPAID.value
        elif order.total_customer_paid < order.net_order_amount:
            order.customer_payment_status = OrderCustomerPaymentStatus.PARTIALLY_PAID.value
        elif order.total_customer_paid == order.net_order_amount:
            order.customer_payment_status = OrderCustomerPaymentStatus.PAID.value
        else:
            order.customer_payment_status = OrderCustomerPaymentStatus.OVERPAID.value

        if order.customer_payment_status != prev_status:
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=org_id,
                status_type="CUSTOMER_PAYMENT",
                from_status=prev_status,
                to_status=order.customer_payment_status,
                remarks=f"Payment allocation {allocation.id} recorded ({allocation.allocation_type}: {actual_amount})",
                changed_by_id=actor_id or order.created_by_id
            )
            db.session.add(hist)

        db.session.flush()
        return allocation

    @classmethod
    def create_vendor_bill(
        cls,
        org_id: int,
        order_id: int,
        procurement_id: int,
        bill_number: str,
        bill_date: datetime,
        items_data: List[Dict[str, Any]],
        due_date: Optional[datetime] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> VendorBill:
        """
        Create a vendor bill matched against a Purchase Order and delivery items.
        Enforces 3-way matching: billed_quantity <= (accepted_quantity - billed_quantity).
        """
        if not items_data:
            raise OMSValidationError("Vendor bill must contain at least one line item.")

        order = OrderMasterService.lock_order(org_id, order_id)

        procurement = OrderVendorProcurement.query.filter_by(
            id=procurement_id,
            order_id=order_id,
            organization_id=org_id,
            is_deleted=False
        ).first()
        if not procurement:
            raise OMSTenantIsolationError(
                f"Procurement {procurement_id} not found for order {order_id} in organization {org_id}."
            )

        clean_bill_num = bill_number.strip()
        existing = VendorBill.query.filter_by(
            organization_id=org_id,
            vendor_id=procurement.vendor_id,
            bill_number=clean_bill_num,
            is_deleted=False
        ).first()
        if existing:
            raise OMSValidationError(
                f"Bill number '{clean_bill_num}' already exists for vendor {procurement.vendor_id}."
            )

        # Pre-validate all bill items and 3-way matching invariants before entity creation (Fail-Fast atomic integrity)
        validated_items = []
        subtotal = Decimal("0.00")
        total_tax = Decimal("0.00")

        for item_data in items_data:
            proc_item_id = item_data.get("procurement_item_id")
            billed_qty = Decimal(str(item_data.get("billed_quantity", 0)))
            del_item_id = item_data.get("delivery_item_id")

            if billed_qty <= Decimal("0.000"):
                raise OMSValidationError("Billed quantity must be greater than 0.")

            po_item = OrderProcurementItem.query.filter_by(
                id=proc_item_id,
                procurement_id=procurement.id,
                order_id=order_id,
                organization_id=org_id,
                is_deleted=False
            ).first()
            if not po_item:
                raise OMSTenantIsolationError(
                    f"Procurement item {proc_item_id} not found in PO {procurement.id}."
                )

            # 3-way matching check:
            # If arrival QC has occurred (accepted_quantity > 0), bill cannot exceed accepted_quantity - billed_quantity.
            # If no arrival QC yet, check against PO quantity - billed_quantity.
            if po_item.accepted_quantity > Decimal("0.000"):
                unbilled_accepted = po_item.accepted_quantity - po_item.billed_quantity
                if billed_qty > unbilled_accepted:
                    raise OMSInvariantError(
                        f"3-Way Matching Violation: Billed quantity {billed_qty} exceeds remaining "
                        f"accepted quantity {unbilled_accepted} for PO line {po_item.id}."
                    )
            else:
                unbilled_po = po_item.quantity - po_item.billed_quantity
                if billed_qty > unbilled_po:
                    raise OMSInvariantError(
                        f"Billed quantity {billed_qty} exceeds remaining PO quantity {unbilled_po} for line {po_item.id}."
                    )

            if del_item_id:
                del_item = DeliveryItem.query.filter_by(
                    id=del_item_id,
                    order_id=order_id,
                    organization_id=org_id,
                    is_deleted=False
                ).first()
                if not del_item:
                    raise OMSTenantIsolationError(f"Delivery item {del_item_id} not found in order {order_id}.")

            unit_rate = Decimal(str(item_data.get("unit_rate", po_item.unit_rate)))
            line_tax = Decimal(str(item_data.get("tax_amount", 0)))
            line_total = (billed_qty * unit_rate) + line_tax

            subtotal += (billed_qty * unit_rate)
            total_tax += line_tax
            validated_items.append((po_item, del_item_id, billed_qty, unit_rate, line_tax, line_total))

        bill = VendorBill(
            bill_number=clean_bill_num,
            order_id=order_id,
            procurement_id=procurement.id,
            vendor_id=procurement.vendor_id,
            organization_id=org_id,
            bill_date=bill_date,
            due_date=due_date,
            status=VendorBillStatus.APPROVED.value,
            subtotal_amount=subtotal.quantize(Decimal("0.01")),
            tax_amount=total_tax.quantize(Decimal("0.01")),
            total_amount=(subtotal + total_tax).quantize(Decimal("0.01")),
            paid_amount=Decimal("0.00"),
            approved_by_id=actor_id or order.created_by_id,
            approved_at=datetime.utcnow(),
            notes=notes,
        )
        db.session.add(bill)
        db.session.flush()

        for po_item, del_item_id, billed_qty, unit_rate, line_tax, line_total in validated_items:
            v_item = VendorBillItem(
                bill_id=bill.id,
                procurement_item_id=po_item.id,
                delivery_item_id=del_item_id,
                order_id=order_id,
                organization_id=org_id,
                billed_quantity=billed_qty,
                unit_rate=unit_rate,
                tax_amount=line_tax,
                total_amount=line_total,
            )
            db.session.add(v_item)

            # Increment PO item billed counter
            po_item.billed_quantity += billed_qty

        # Update order cumulative vendor billed amount
        order.total_vendor_billed = (
            db.session.query(func.coalesce(func.sum(VendorBill.total_amount), Decimal("0.00")))
            .filter(
                VendorBill.order_id == order.id,
                VendorBill.organization_id == org_id,
                VendorBill.is_deleted == False
            )
            .scalar()
        )

        db.session.flush()
        return bill

    @classmethod
    def record_vendor_payment(
        cls,
        org_id: int,
        bill_id: int,
        amount: Any,
        payment_mode: str,
        payment_reference: Optional[str] = None,
        transaction_reference: Optional[str] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> VendorPayment:
        """
        Record a payment disbursement to a vendor against an approved vendor bill.
        Enforces remaining bill balance invariant and updates vendor payment status.
        """
        bill = VendorBill.query.filter_by(
            id=bill_id,
            organization_id=org_id,
            is_deleted=False
        ).first()
        if not bill:
            raise OMSValidationError(f"Vendor bill {bill_id} not found in this organization.")

        order = OrderMasterService.lock_order(org_id, bill.order_id)

        pay_amount = Decimal(str(amount))
        if pay_amount <= Decimal("0.00"):
            raise OMSValidationError("Payment amount must be strictly greater than 0.")

        remaining_balance = bill.total_amount - bill.paid_amount
        if pay_amount > remaining_balance:
            raise OMSInvariantError(
                f"Payment amount {pay_amount} exceeds remaining bill balance {remaining_balance} for bill {bill.bill_number}."
            )

        ref = payment_reference.strip() if payment_reference and payment_reference.strip() else cls.generate_vendor_payment_reference(org_id)

        v_pay = VendorPayment(
            payment_reference=ref,
            bill_id=bill.id,
            vendor_id=bill.vendor_id,
            order_id=bill.order_id,
            organization_id=org_id,
            payment_date=datetime.utcnow(),
            amount=pay_amount,
            payment_mode=payment_mode,
            transaction_reference=transaction_reference,
            notes=notes,
            created_by_id=actor_id or order.created_by_id,
        )
        db.session.add(v_pay)

        # Update bill paid amount and status
        bill.paid_amount += pay_amount
        if bill.paid_amount >= bill.total_amount:
            bill.status = VendorBillStatus.PAID.value
        else:
            bill.status = VendorBillStatus.PARTIALLY_PAID.value

        # Update order cumulative vendor paid
        order.total_vendor_paid = (
            db.session.query(func.coalesce(func.sum(VendorPayment.amount), Decimal("0.00")))
            .filter(
                VendorPayment.order_id == order.id,
                VendorPayment.organization_id == org_id,
                VendorPayment.is_deleted == False
            )
            .scalar()
        )

        # Update order vendor payment status
        prev_vendor_status = order.vendor_payment_status
        if order.total_vendor_paid <= Decimal("0.00"):
            order.vendor_payment_status = OrderVendorPaymentStatus.UNPAID.value
        elif order.total_vendor_paid < order.total_vendor_billed:
            order.vendor_payment_status = OrderVendorPaymentStatus.PARTIALLY_PAID.value
        else:
            order.vendor_payment_status = OrderVendorPaymentStatus.PAID.value

        if order.vendor_payment_status != prev_vendor_status:
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=org_id,
                status_type="VENDOR_PAYMENT",
                from_status=prev_vendor_status,
                to_status=order.vendor_payment_status,
                remarks=f"Vendor payment {v_pay.payment_reference} of {pay_amount} recorded",
                changed_by_id=actor_id or order.created_by_id
            )
            db.session.add(hist)

        db.session.flush()
        return v_pay

    @classmethod
    def reconcile_order_financials(cls, org_id: int, order_id: int) -> Dict[str, Any]:
        """
        Compute complete financial reconciliation breakdown for an order.
        Includes gross margin, cash collected, AP liability, and operating cash margin.
        """
        order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id, is_deleted=False).first()
        if not order:
            raise OMSValidationError(f"Order {order_id} not found in this organization.")

        gross_profit = order.net_order_amount - order.total_procurement_cost
        margin_pct = (
            round(float(gross_profit / order.net_order_amount * 100), 2)
            if order.net_order_amount > Decimal("0.00")
            else 0.0
        )
        operating_cash_margin = order.total_customer_paid - order.total_vendor_paid

        customer_outstanding = max(Decimal("0.00"), order.net_order_amount - order.total_customer_paid)
        vendor_outstanding = max(Decimal("0.00"), order.total_vendor_billed - order.total_vendor_paid)

        # Gather payment allocations
        allocations = (
            OrderPaymentAllocation.query
            .filter_by(order_id=order.id, organization_id=org_id)
            .order_by(OrderPaymentAllocation.id.asc())
            .all()
        )
        allocations_data = [
            {
                "id": a.id,
                "payment_id": a.payment_id,
                "payment_number": a.payment.payment_number if a.payment else None,
                "invoice_id": a.invoice_id,
                "allocation_type": a.allocation_type,
                "allocated_amount": float(a.allocated_amount),
                "allocation_date": a.allocation_date.isoformat() if a.allocation_date else None,
                "notes": a.notes,
            }
            for a in allocations
        ]

        # Gather vendor bills
        bills = (
            VendorBill.query
            .filter_by(order_id=order.id, organization_id=org_id, is_deleted=False)
            .order_by(VendorBill.id.asc())
            .all()
        )
        bills_data = [
            {
                "id": b.id,
                "bill_number": b.bill_number,
                "vendor_id": b.vendor_id,
                "vendor_name": b.vendor.name if b.vendor else None,
                "total_amount": float(b.total_amount),
                "paid_amount": float(b.paid_amount),
                "status": b.status,
                "bill_date": b.bill_date.isoformat() if b.bill_date else None,
            }
            for b in bills
        ]

        # Gather vendor payments
        payments = (
            VendorPayment.query
            .filter_by(order_id=order.id, organization_id=org_id, is_deleted=False)
            .order_by(VendorPayment.id.asc())
            .all()
        )
        payments_data = [
            {
                "id": p.id,
                "payment_reference": p.payment_reference,
                "bill_id": p.bill_id,
                "vendor_id": p.vendor_id,
                "amount": float(p.amount),
                "payment_mode": p.payment_mode,
                "transaction_reference": p.transaction_reference,
                "payment_date": p.payment_date.isoformat() if p.payment_date else None,
            }
            for p in payments
        ]

        return {
            "order_id": order.id,
            "master_order_number": order.master_order_number,
            "order_title": getattr(order, "order_title", order.notes or order.master_order_number),
            "net_order_amount": float(order.net_order_amount),
            "total_procurement_cost": float(order.total_procurement_cost),
            "gross_profit": float(gross_profit),
            "gross_margin": float(gross_profit),
            "gross_margin_percentage": margin_pct,
            "total_customer_paid": float(order.total_customer_paid),
            "customer_outstanding": float(customer_outstanding),
            "customer_payment_status": order.customer_payment_status,
            "total_vendor_billed": float(order.total_vendor_billed),
            "total_vendor_paid": float(order.total_vendor_paid),
            "vendor_outstanding": float(vendor_outstanding),
            "vendor_payment_status": order.vendor_payment_status,
            "operating_cash_margin": float(operating_cash_margin),
            "payment_allocations": allocations_data,
            "vendor_bills": bills_data,
            "vendor_payments": payments_data,
        }

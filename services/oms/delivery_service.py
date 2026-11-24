"""
services/oms/delivery_service.py
Service managing customer delivery challans, goods receipt confirmation,
and site installation/fitting visit lifecycles.
"""
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Any, Optional

from model import (
    db,
    OrderMaster,
    OrderItem,
    ConsignmentItem,
    OrderDelivery,
    DeliveryItem,
    OrderFittingVisit,
    FittingVisitItem,
    OrderStatusHistory,
    MasterOrderStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderFittingVisitStatus,
    Employee,
)
from . import (
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)
from .order_master_service import OrderMasterService


class DeliveryService:
    """Service managing customer delivery challans and site installation visits."""

    @staticmethod
    def generate_delivery_number(org_id: int) -> str:
        """Generate sequential unique Delivery Challan number per tenant: DEL-{year}-{seq:05d}."""
        year = datetime.utcnow().year
        prefix = f"DEL-{year}-"
        last = (
            OrderDelivery.query
            .filter(
                OrderDelivery.organization_id == org_id,
                OrderDelivery.delivery_number.like(f"{prefix}%")
            )
            .order_by(OrderDelivery.id.desc())
            .first()
        )
        seq = 1
        if last and last.delivery_number:
            try:
                seq = int(last.delivery_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = last.id + 1
        return f"{prefix}{seq:05d}"

    @staticmethod
    def generate_visit_number(org_id: int) -> str:
        """Generate sequential unique Fitting Visit number per tenant: FIT-{year}-{seq:05d}."""
        year = datetime.utcnow().year
        prefix = f"FIT-{year}-"
        last = (
            OrderFittingVisit.query
            .filter(
                OrderFittingVisit.organization_id == org_id,
                OrderFittingVisit.visit_number.like(f"{prefix}%")
            )
            .order_by(OrderFittingVisit.id.desc())
            .first()
        )
        seq = 1
        if last and last.visit_number:
            try:
                seq = int(last.visit_number.split("-")[-1]) + 1
            except (ValueError, IndexError):
                seq = last.id + 1
        return f"{prefix}{seq:05d}"

    @classmethod
    def create_delivery_challan(
        cls,
        org_id: int,
        order_id: int,
        items_data: List[Dict[str, Any]],
        recipient_name: Optional[str] = None,
        recipient_phone: Optional[str] = None,
        delivery_address: Optional[str] = None,
        challan_number: Optional[str] = None,
        delivered_by_id: Optional[int] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderDelivery:
        """
        Create a new customer delivery challan.
        Enforces parent-first locking and undelivered quantity invariant.
        """
        if not items_data:
            raise OMSValidationError("Delivery challan must contain at least one item.")

        # 1. Acquire parent-first lock
        order = OrderMasterService.lock_order(org_id, order_id)
        if order.status in (MasterOrderStatus.CANCELLED.value, MasterOrderStatus.CLOSED.value):
            raise OMSTransitionError(f"Cannot dispatch delivery for order in {order.status} state.")

        if delivered_by_id:
            emp = Employee.query.filter_by(id=delivered_by_id, organization_id=org_id, is_deleted=False).first()
            if not emp:
                raise OMSTenantIsolationError(f"Delivered-by employee {delivered_by_id} not found in this organization.")

        # Pre-validate all line items and undelivered invariants before entity creation (Fail-Fast atomic integrity)
        validated_items = []
        for item_data in items_data:
            order_item_id = item_data.get("order_item_id")
            delivered_qty = Decimal(str(item_data.get("delivered_quantity", 0)))
            consignment_item_id = item_data.get("consignment_item_id")

            if delivered_qty <= Decimal("0.000"):
                raise OMSValidationError("Delivered quantity must be strictly greater than 0.")

            order_item = OrderItem.query.filter_by(
                id=order_item_id,
                order_id=order_id,
                organization_id=org_id,
                is_deleted=False
            ).first()
            if not order_item:
                raise OMSTenantIsolationError(
                    f"Order item {order_item_id} does not belong to order {order_id} or tenant {org_id}."
                )

            # Invariant: remaining undelivered quantity
            remaining_undelivered = order_item.quantity - order_item.delivered_quantity
            if delivered_qty > remaining_undelivered:
                raise OMSInvariantError(
                    f"Delivered quantity {delivered_qty} exceeds remaining undelivered quantity "
                    f"{remaining_undelivered} for item '{order_item.item_description}'."
                )

            # Optional: verify consignment item if linked
            if consignment_item_id:
                cons_item = ConsignmentItem.query.filter_by(
                    id=consignment_item_id,
                    order_id=order_id,
                    organization_id=org_id,
                    is_deleted=False
                ).first()
                if not cons_item:
                    raise OMSTenantIsolationError(
                        f"Consignment item {consignment_item_id} does not belong to order {order_id}."
                    )

            validated_items.append((order_item, delivered_qty, consignment_item_id, item_data.get("notes")))

        delv_number = cls.generate_delivery_number(org_id)
        actual_challan = challan_number.strip() if challan_number and challan_number.strip() else delv_number

        delivery = OrderDelivery(
            delivery_number=delv_number,
            order_id=order_id,
            organization_id=org_id,
            delivery_date=datetime.utcnow(),
            recipient_name=recipient_name or (order.customer.name if order.customer else "Site Recipient"),
            recipient_phone=recipient_phone or (getattr(order.customer, 'phone_number', None) or getattr(order.customer, 'mobile', None) if order.customer else None),
            delivery_address=delivery_address or order.shipping_address or order.site_address,
            challan_number=actual_challan,
            status=OrderDeliveryStatus.SCHEDULED.value,
            delivered_by_id=delivered_by_id or actor_id,
            notes=notes,
        )
        db.session.add(delivery)
        db.session.flush()

        for order_item, delivered_qty, consignment_item_id, notes_val in validated_items:
            del_item = DeliveryItem(
                delivery_id=delivery.id,
                order_id=order_id,
                order_item_id=order_item.id,
                consignment_item_id=consignment_item_id,
                organization_id=org_id,
                delivered_quantity=delivered_qty,
                accepted_quantity=Decimal("0.000"),
                damaged_quantity=Decimal("0.000"),
                returned_quantity=Decimal("0.000"),
                notes=notes_val,
            )
            db.session.add(del_item)

        # Update order delivery status
        if order.delivery_status == OrderDeliveryStatus.SCHEDULED.value:
            order.delivery_status = OrderDeliveryStatus.SCHEDULED.value

        db.session.flush()
        return delivery

    @classmethod
    def dispatch_delivery(
        cls,
        org_id: int,
        delivery_id: int,
        actor_id: Optional[int] = None
    ) -> OrderDelivery:
        """Mark a delivery challan as OUT_FOR_DELIVERY and update order status."""
        delivery = OrderDelivery.query.filter_by(
            id=delivery_id,
            organization_id=org_id,
            is_deleted=False
        ).first()
        if not delivery:
            raise OMSValidationError(f"Delivery {delivery_id} not found in this organization.")

        order = OrderMasterService.lock_order(org_id, delivery.order_id)
        if delivery.status != OrderDeliveryStatus.SCHEDULED.value:
            raise OMSTransitionError(
                f"Cannot dispatch delivery in status '{delivery.status}' (expected SCHEDULED)."
            )

        delivery.status = OrderDeliveryStatus.OUT_FOR_DELIVERY.value
        order.delivery_status = OrderDeliveryStatus.OUT_FOR_DELIVERY.value

        # Master status progression if applicable
        if order.status in (
            MasterOrderStatus.CONFIRMED.value,
            MasterOrderStatus.RECEIVED.value,
            MasterOrderStatus.PARTIALLY_RECEIVED.value
        ):
            order.status = MasterOrderStatus.IN_DELIVERY.value
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=org_id,
                status_type="MASTER",
                from_status=order.status,
                to_status=MasterOrderStatus.IN_DELIVERY.value,
                remarks=f"Challan {delivery.delivery_number} out for delivery",
                changed_by_id=actor_id or order.created_by_id
            )
            db.session.add(hist)

        db.session.flush()
        return delivery

    @classmethod
    def confirm_delivery(
        cls,
        org_id: int,
        delivery_id: int,
        items_confirmation: Optional[List[Dict[str, Any]]] = None,
        proof_doc_id: Optional[int] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderDelivery:
        """
        Record site receipt confirmation for a delivery challan.
        Updates DeliveryItem accepted/damaged quantities, increments OrderItem.delivered_quantity,
        and recomputes order-level delivery status.
        """
        delivery = OrderDelivery.query.filter_by(
            id=delivery_id,
            organization_id=org_id,
            is_deleted=False
        ).first()
        if not delivery:
            raise OMSValidationError(f"Delivery {delivery_id} not found in this organization.")

        order = OrderMasterService.lock_order(org_id, delivery.order_id)
        if delivery.status == OrderDeliveryStatus.DELIVERED.value:
            raise OMSTransitionError(f"Delivery {delivery.delivery_number} has already been confirmed.")

        prev_order_del_status = order.delivery_status
        delivery_items = DeliveryItem.query.filter_by(
            delivery_id=delivery.id,
            order_id=order.id,
            organization_id=org_id,
            is_deleted=False
        ).all()

        conf_map = {}
        if items_confirmation:
            for conf in items_confirmation:
                conf_map[conf.get("delivery_item_id")] = conf

        for del_item in delivery_items:
            order_item = OrderItem.query.filter_by(
                id=del_item.order_item_id,
                order_id=order.id,
                organization_id=org_id
            ).first()

            if del_item.id in conf_map:
                conf = conf_map[del_item.id]
                accepted = Decimal(str(conf.get("accepted_quantity", del_item.delivered_quantity)))
                damaged = Decimal(str(conf.get("damaged_quantity", 0)))
                returned = Decimal(str(conf.get("returned_quantity", 0)))

                if accepted + damaged + returned != del_item.delivered_quantity:
                    raise OMSInvariantError(
                        f"Item line breakdown (accepted: {accepted}, damaged: {damaged}, returned: {returned}) "
                        f"must equal delivered quantity ({del_item.delivered_quantity})."
                    )
            else:
                accepted = del_item.delivered_quantity
                damaged = Decimal("0.000")
                returned = Decimal("0.000")

            del_item.accepted_quantity = accepted
            del_item.damaged_quantity = damaged
            del_item.returned_quantity = returned

            # Increment OrderItem delivered counter
            order_item.delivered_quantity += accepted
            if order_item.delivered_quantity > order_item.quantity:
                raise OMSInvariantError(
                    f"Cumulative delivered quantity {order_item.delivered_quantity} "
                    f"exceeds ordered quantity {order_item.quantity} for item '{order_item.item_description}'."
                )

        delivery.status = OrderDeliveryStatus.DELIVERED.value
        if proof_doc_id:
            delivery.proof_of_delivery_document_id = proof_doc_id
        if notes:
            delivery.notes = f"{delivery.notes}\nConfirmation: {notes}" if delivery.notes else notes
        order.actual_delivery_date = datetime.utcnow()

        # Recompute OrderMaster delivery status
        all_order_items = OrderItem.query.filter_by(
            order_id=order.id,
            organization_id=org_id,
            is_deleted=False
        ).all()

        # Authoritative delivery status recalculation & lifecycle sync
        from services.oms.status_engine import OMSStatusEngine
        OMSStatusEngine.recalculate_delivery_status(order, actor_id=actor_id)
        OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor_id)

        # Record Delivery Status History
        hist_del = OrderStatusHistory(
            order_id=order.id,
            organization_id=org_id,
            status_type="DELIVERY",
            from_status=prev_order_del_status,
            to_status=order.delivery_status,
            remarks=f"Challan {delivery.delivery_number} confirmed as DELIVERED",
            changed_by_id=actor_id or order.created_by_id
        )
        db.session.add(hist_del)

        db.session.flush()
        return delivery

    @classmethod
    def schedule_fitting_visit(
        cls,
        org_id: int,
        order_id: int,
        scheduled_date: datetime,
        technician_id: Optional[int] = None,
        items_planned: Optional[List[Dict[str, Any]]] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderFittingVisit:
        """
        Schedule a site installation/fitting visit.
        Validates planned quantities against delivered quantities.
        """
        order = OrderMasterService.lock_order(org_id, order_id)
        if order.status in (MasterOrderStatus.CANCELLED.value, MasterOrderStatus.CLOSED.value):
            raise OMSTransitionError(f"Cannot schedule fitting for order in {order.status} state.")

        if technician_id:
            tech = Employee.query.filter_by(id=technician_id, organization_id=org_id, is_deleted=False).first()
            if not tech:
                raise OMSTenantIsolationError(f"Technician {technician_id} not found in this organization.")

        visit_number = cls.generate_visit_number(org_id)
        visit = OrderFittingVisit(
            visit_number=visit_number,
            order_id=order_id,
            organization_id=org_id,
            technician_id=technician_id,
            scheduled_date=scheduled_date,
            status=OrderFittingVisitStatus.SCHEDULED.value,
            client_signoff=False,
            notes=notes,
        )
        db.session.add(visit)
        db.session.flush()

        # If specific items are specified, create visit items
        if items_planned:
            for planned in items_planned:
                order_item_id = planned.get("order_item_id")
                qty = Decimal(str(planned.get("planned_quantity", 0)))

                if qty <= Decimal("0.000"):
                    raise OMSValidationError("Planned quantity must be greater than 0.")

                order_item = OrderItem.query.filter_by(
                    id=order_item_id,
                    order_id=order_id,
                    organization_id=org_id,
                    is_deleted=False
                ).first()
                if not order_item:
                    raise OMSTenantIsolationError(
                        f"Order item {order_item_id} does not belong to order {order_id}."
                    )

                remaining_to_fit = order_item.quantity - order_item.installed_quantity
                if qty > remaining_to_fit:
                    raise OMSInvariantError(
                        f"Planned fitting quantity {qty} exceeds remaining unfitted quantity "
                        f"{remaining_to_fit} for item '{order_item.item_description}'."
                    )

                v_item = FittingVisitItem(
                    visit_id=visit.id,
                    order_id=order_id,
                    order_item_id=order_item.id,
                    organization_id=org_id,
                    planned_quantity=qty,
                    installed_quantity=Decimal("0.000"),
                    remaining_quantity=qty,
                    issues_reported=planned.get("issues_reported"),
                )
                db.session.add(v_item)
        else:
            # Default: plan all delivered but uninstalled items
            order_items = OrderItem.query.filter_by(
                order_id=order_id,
                organization_id=org_id,
                is_deleted=False
            ).all()
            for o_item in order_items:
                remaining = o_item.quantity - o_item.installed_quantity
                if remaining > Decimal("0.000"):
                    v_item = FittingVisitItem(
                        visit_id=visit.id,
                        order_id=order_id,
                        order_item_id=o_item.id,
                        organization_id=org_id,
                        planned_quantity=remaining,
                        installed_quantity=Decimal("0.000"),
                        remaining_quantity=remaining,
                    )
                    db.session.add(v_item)

        order.expected_fitting_date = scheduled_date
        order.fitting_status = OrderFittingStatus.SCHEDULED.value

        if order.status == MasterOrderStatus.DELIVERED.value:
            order.status = MasterOrderStatus.IN_FITTING.value
            hist = OrderStatusHistory(
                order_id=order.id,
                organization_id=org_id,
                status_type="MASTER",
                from_status=MasterOrderStatus.DELIVERED.value,
                to_status=MasterOrderStatus.IN_FITTING.value,
                remarks=f"Fitting visit {visit.visit_number} scheduled",
                changed_by_id=actor_id or order.created_by_id
            )
            db.session.add(hist)

        db.session.flush()
        return visit

    @classmethod
    def complete_fitting_visit(
        cls,
        org_id: int,
        visit_id: int,
        items_installed: Optional[List[Dict[str, Any]]] = None,
        client_signoff: bool = False,
        signoff_document_id: Optional[int] = None,
        notes: Optional[str] = None,
        actor_id: Optional[int] = None,
    ) -> OrderFittingVisit:
        """
        Record site installation results, technician report, and client signoff.
        Updates installed quantities on OrderItem and auto-completes order if all items installed.
        """
        visit = OrderFittingVisit.query.filter_by(
            id=visit_id,
            organization_id=org_id,
            is_deleted=False
        ).first()
        if not visit:
            raise OMSValidationError(f"Fitting visit {visit_id} not found in this organization.")

        order = OrderMasterService.lock_order(org_id, visit.order_id)
        if visit.status == OrderFittingVisitStatus.COMPLETED.value:
            raise OMSTransitionError(f"Visit {visit.visit_number} has already been completed.")

        prev_order_fit_status = order.fitting_status
        visit_items = FittingVisitItem.query.filter_by(
            visit_id=visit.id,
            order_id=order.id,
            organization_id=org_id,
            is_deleted=False
        ).all()

        install_map = {}
        if items_installed:
            for itm in items_installed:
                install_map[itm.get("fitting_visit_item_id")] = itm

        all_visit_lines_fully_installed = True
        for v_item in visit_items:
            order_item = OrderItem.query.filter_by(
                id=v_item.order_item_id,
                order_id=order.id,
                organization_id=org_id
            ).first()

            if v_item.id in install_map:
                data = install_map[v_item.id]
                installed = Decimal(str(data.get("installed_quantity", v_item.planned_quantity)))
                v_item.issues_reported = data.get("issues_reported")
            else:
                installed = v_item.planned_quantity

            if installed > v_item.planned_quantity:
                raise OMSInvariantError(
                    f"Installed quantity {installed} cannot exceed planned quantity "
                    f"{v_item.planned_quantity} for visit line {v_item.id}."
                )

            v_item.installed_quantity = installed
            v_item.remaining_quantity = v_item.planned_quantity - installed
            if v_item.remaining_quantity > Decimal("0.000"):
                all_visit_lines_fully_installed = False

            # Invariant: cannot install more than delivered
            order_item.installed_quantity += installed
            if order_item.installed_quantity > order_item.delivered_quantity:
                raise OMSInvariantError(
                    f"Cumulative installed quantity {order_item.installed_quantity} exceeds "
                    f"delivered quantity {order_item.delivered_quantity} for item '{order_item.item_description}'."
                )

        visit.completed_at = datetime.utcnow()
        visit.client_signoff = client_signoff
        if signoff_document_id:
            visit.signoff_document_id = signoff_document_id
        if notes:
            visit.notes = f"{visit.notes}\nCompletion: {notes}" if visit.notes else notes

        if all_visit_lines_fully_installed:
            visit.status = OrderFittingVisitStatus.COMPLETED.value
        else:
            visit.status = OrderFittingVisitStatus.INCOMPLETE.value

        # Recompute OrderMaster fitting status
        all_order_items = OrderItem.query.filter_by(
            order_id=order.id,
            organization_id=org_id,
            is_deleted=False
        ).all()

        # Authoritative fitting status recalculation & lifecycle sync
        from services.oms.status_engine import OMSStatusEngine
        OMSStatusEngine.recalculate_fitting_status(order, actor_id=actor_id)
        OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor_id)

        # Record Fitting Status History
        hist_fit = OrderStatusHistory(
            order_id=order.id,
            organization_id=org_id,
            status_type="FITTING",
            from_status=prev_order_fit_status,
            to_status=order.fitting_status,
            remarks=f"Fitting visit {visit.visit_number} marked as {visit.status}",
            changed_by_id=actor_id or order.created_by_id
        )
        db.session.add(hist_fit)

        db.session.flush()
        return visit

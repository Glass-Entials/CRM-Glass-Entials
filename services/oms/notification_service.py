from datetime import datetime, timedelta
from typing import Optional, List, Set
from sqlalchemy import or_
from model import (
    db,
    OrderMaster,
    OrderFittingVisit,
    OrderVendorProcurement,
    OrderDelivery,
    Employee,
    User,
    Notification,
)
from utils.notifications import create_notification
from utils.activity_service import ActivityService


class OMSNotificationService:
    """Enterprise notification and audit service for Order Management."""

    @classmethod
    def get_order_stakeholders(cls, org_id: int, order: OrderMaster) -> Set[int]:
        """
        Identify directly involved stakeholder employee IDs for an order:
        - Order creator (`created_by_id`)
        - Assigned employee / PM (`assigned_to_id`)
        - Assigned fitting technician(s) on active fitting visits
        - Procurement PO owner(s) on active POs
        - Assigned delivery owner(s) on active deliveries
        """
        candidate_ids: Set[int] = set()

        if getattr(order, "created_by_id", None):
            candidate_ids.add(order.created_by_id)

        if getattr(order, "assigned_to_id", None):
            candidate_ids.add(order.assigned_to_id)

        order_id = getattr(order, "id", None)
        if order_id:
            for visit in OrderFittingVisit.query.filter_by(
                order_id=order_id, organization_id=org_id, is_deleted=False
            ).all():
                if visit.technician_id:
                    candidate_ids.add(visit.technician_id)

            for po in OrderVendorProcurement.query.filter_by(
                order_id=order_id, organization_id=org_id, is_deleted=False
            ).all():
                if po.created_by_id:
                    candidate_ids.add(po.created_by_id)

            for delv in OrderDelivery.query.filter_by(
                order_id=order_id, organization_id=org_id, is_deleted=False
            ).all():
                if delv.delivered_by_id:
                    candidate_ids.add(delv.delivered_by_id)

        if not candidate_ids:
            return set()

        valid_emps = (
            Employee.query.join(User, Employee.user_id == User.id)
            .filter(
                Employee.id.in_(candidate_ids),
                Employee.organization_id == org_id,
                or_(Employee.is_deleted == False, Employee.is_deleted.is_(None)),
                or_(User.is_active == True, User.is_active.is_(None)),
            )
            .all()
        )
        return {emp.id for emp in valid_emps}

    @classmethod
    def _is_duplicate_notification(
        cls,
        org_id: int,
        recipient_id: int,
        title: str,
        cooldown_minutes: int = 15,
    ) -> bool:
        """Prevent duplicate notifications within the cooldown window."""
        cutoff = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
        existing = (
            Notification.query.filter(
                Notification.organization_id == org_id,
                Notification.recipient_id == recipient_id,
                Notification.title == title,
                Notification.created_at >= cutoff,
            )
            .first()
        )
        return existing is not None

    @classmethod
    def send_order_notification(
        cls,
        org_id: int,
        order: OrderMaster,
        title: str,
        message: str,
        sender_id: Optional[int] = None,
        exclude_sender: bool = True,
    ) -> List[Notification]:
        """Distribute a notification to all relevant order stakeholders with de-duplication."""
        created = []
        stakeholders = cls.get_order_stakeholders(org_id, order)
        link = f"/orders/{order.id}"

        for recipient_id in stakeholders:
            if exclude_sender and sender_id and recipient_id == sender_id:
                continue

            if cls._is_duplicate_notification(org_id, recipient_id, title):
                continue

            notif = create_notification(
                recipient_id=recipient_id,
                title=title,
                message=message,
                link=link,
                sender_id=sender_id,
                organization_id=org_id,
            )
            if notif:
                created.append(notif)

        return created

    @classmethod
    def notify_status_transition(
        cls,
        org_id: int,
        order: OrderMaster,
        from_status: Optional[str],
        to_status: str,
        actor_id: Optional[int] = None,
    ):
        """Notify stakeholders and log activity when master order status changes."""
        title = f"Order #{order.master_order_number}: Status -> {to_status}"
        message = (
            f"Order '{order.order_title}' transitioned from {from_status or 'INIT'} to {to_status}."
        )

        cls.send_order_notification(
            org_id=org_id,
            order=order,
            title=title,
            message=message,
            sender_id=actor_id,
        )

        ActivityService.log(
            action="order_status_updated",
            entity_type="order",
            entity_id=order.id,
            entity_name=order.master_order_number,
            old_value=from_status,
            new_value=to_status,
            description=message,
        )

    @classmethod
    def notify_procurement_po_issued(
        cls,
        org_id: int,
        order: OrderMaster,
        po_number: str,
        vendor_name: str,
        amount: str,
        actor_id: Optional[int] = None,
    ):
        """Notify stakeholders when a Purchase Order is issued to a vendor."""
        title = f"PO Issued: {po_number} ({vendor_name})"
        message = (
            f"Purchase Order {po_number} for ₹{amount} issued to vendor {vendor_name} "
            f"under Order #{order.master_order_number}."
        )

        cls.send_order_notification(
            org_id=org_id,
            order=order,
            title=title,
            message=message,
            sender_id=actor_id,
        )

        ActivityService.log(
            action="procurement_po_issued",
            entity_type="order",
            entity_id=order.id,
            entity_name=order.master_order_number,
            description=message,
        )

    @classmethod
    def notify_consignment_arrival(
        cls,
        org_id: int,
        order: OrderMaster,
        consignment_number: str,
        accepted_qty: float,
        rejected_qty: float,
        actor_id: Optional[int] = None,
    ):
        """Notify stakeholders of consignment arrival QC outcome."""
        has_rejection = rejected_qty > 0
        title = f"Consignment QC: {consignment_number} ({'Defects Found' if has_rejection else 'Accepted'})"
        message = (
            f"Consignment {consignment_number} QC completed. Accepted: {accepted_qty:.3f}, "
            f"Rejected: {rejected_qty:.3f} units for Order #{order.master_order_number}."
        )

        cls.send_order_notification(
            org_id=org_id,
            order=order,
            title=title,
            message=message,
            sender_id=actor_id,
        )

        ActivityService.log(
            action="consignment_qc_completed",
            entity_type="order",
            entity_id=order.id,
            entity_name=order.master_order_number,
            description=message,
        )

    @classmethod
    def notify_delivery_dispatched(
        cls,
        org_id: int,
        order: OrderMaster,
        delivery_number: str,
        actor_id: Optional[int] = None,
    ):
        """Notify stakeholders when goods are delivered to site."""
        title = f"Delivery Completed: {delivery_number}"
        message = (
            f"Delivery challan {delivery_number} confirmed for Order #{order.master_order_number}."
        )

        cls.send_order_notification(
            org_id=org_id,
            order=order,
            title=title,
            message=message,
            sender_id=actor_id,
        )

        ActivityService.log(
            action="order_delivered",
            entity_type="order",
            entity_id=order.id,
            entity_name=order.master_order_number,
            description=message,
        )

    @classmethod
    def notify_fitting_completed(
        cls,
        org_id: int,
        order: OrderMaster,
        visit_number: str,
        actor_id: Optional[int] = None,
    ):
        """Notify stakeholders of site installation signoff."""
        title = f"Site Installation Signed Off: {visit_number}"
        message = (
            f"Fitting visit {visit_number} was signed off for Order #{order.master_order_number}."
        )

        cls.send_order_notification(
            org_id=org_id,
            order=order,
            title=title,
            message=message,
            sender_id=actor_id,
        )

        ActivityService.log(
            action="fitting_signoff_completed",
            entity_type="order",
            entity_id=order.id,
            entity_name=order.master_order_number,
            description=message,
        )

    @classmethod
    def check_and_notify_overdue_orders(cls, org_id: int) -> int:
        """Scan active orders and notify managers about delayed orders."""
        now = datetime.utcnow()
        overdue_orders = (
            OrderMaster.query.filter(
                OrderMaster.organization_id == org_id,
                OrderMaster.status.notin_(["COMPLETED", "CLOSED", "CANCELLED"]),
                OrderMaster.expected_delivery_date != None,
                OrderMaster.expected_delivery_date < now,
            )
            .all()
        )

        notified_count = 0
        for order in overdue_orders:
            title = f"⚠️ Order Overdue: #{order.master_order_number}"
            message = (
                f"Order #{order.master_order_number} ('{order.order_title}') was expected by "
                f"{order.expected_delivery_date.strftime('%Y-%m-%d')} but is still {order.status}."
            )
            notifs = cls.send_order_notification(
                org_id=org_id,
                order=order,
                title=title,
                message=message,
                exclude_sender=False,
            )
            if notifs:
                notified_count += len(notifs)

        return notified_count

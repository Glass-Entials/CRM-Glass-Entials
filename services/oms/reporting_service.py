from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, Optional, List
from sqlalchemy import func, or_, and_

from model import (
    db,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderDelivery,
    OrderFittingVisit,
    VendorBill,
    OrderPaymentAllocation,
    OrderStatusHistory,
    MasterOrderStatus,
    Customer,
    Vendor,
)


class OMSReportingService:
    """Enterprise reporting and business intelligence service for Order Management."""

    @classmethod
    def get_dashboard_metrics(
        cls,
        org_id: int,
        from_date: Optional[datetime] = None,
        to_date: Optional[datetime] = None,
        customer_id: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Aggregate executive operational and financial metrics for an organization
        within an optional date range and customer filter.
        """
        query = OrderMaster.query.filter(
            OrderMaster.organization_id == org_id,
            OrderMaster.is_deleted == False,
        )

        if from_date:
            query = query.filter(OrderMaster.order_date >= from_date)
        if to_date:
            query = query.filter(OrderMaster.order_date <= to_date)
        if customer_id:
            query = query.filter(OrderMaster.customer_id == customer_id)
        if status:
            query = query.filter(OrderMaster.status == status)

        orders: List[OrderMaster] = query.all()
        now = datetime.utcnow()

        total_orders = len(orders)
        active_statuses = {
            MasterOrderStatus.DRAFT.value,
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

        active_orders = sum(1 for o in orders if o.status in active_statuses)
        completed_orders = sum(1 for o in orders if o.status in (MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value))
        cancelled_orders = sum(1 for o in orders if o.status == MasterOrderStatus.CANCELLED.value)

        # Operational bottlenecks
        awaiting_procurement = sum(1 for o in orders if o.status == MasterOrderStatus.CONFIRMED.value)
        in_procurement = sum(1 for o in orders if o.status in (MasterOrderStatus.IN_PROCUREMENT.value, MasterOrderStatus.PARTIALLY_RECEIVED.value))
        awaiting_delivery = sum(1 for o in orders if o.status in (MasterOrderStatus.RECEIVED.value, MasterOrderStatus.IN_DELIVERY.value))
        partially_delivered = sum(1 for o in orders if o.status == MasterOrderStatus.PARTIALLY_DELIVERED.value)
        in_fitting = sum(1 for o in orders if o.status in (MasterOrderStatus.DELIVERED.value, MasterOrderStatus.IN_FITTING.value))

        # Delayed / Overdue orders
        overdue_orders = sum(
            1 for o in orders
            if o.status in active_statuses
            and o.expected_delivery_date
            and o.expected_delivery_date < now
        )

        # Financial aggregations
        zero_dec = Decimal("0.00")
        total_revenue = sum((o.net_order_amount for o in orders), zero_dec)
        total_gross_order_amount = sum((o.total_ordered_amount for o in orders), zero_dec)
        total_procurement_cost = sum((o.total_procurement_cost for o in orders), zero_dec)
        gross_profit = total_revenue - total_procurement_cost
        gross_margin_pct = (
            round(float(gross_profit / total_revenue * 100), 2)
            if total_revenue > zero_dec
            else 0.0
        )

        total_customer_paid = sum((o.total_customer_paid for o in orders), zero_dec)
        customer_outstanding = total_gross_order_amount - total_customer_paid
        if customer_outstanding < zero_dec:
            customer_outstanding = zero_dec

        total_vendor_billed = sum((o.total_vendor_billed for o in orders), zero_dec)
        total_vendor_paid = sum((o.total_vendor_paid for o in orders), zero_dec)
        vendor_outstanding = total_vendor_billed - total_vendor_paid
        if vendor_outstanding < zero_dec:
            vendor_outstanding = zero_dec

        operating_cash_margin = total_customer_paid - total_vendor_paid

        return {
            "summary": {
                "total_orders": total_orders,
                "active_orders": active_orders,
                "completed_orders": completed_orders,
                "cancelled_orders": cancelled_orders,
                "overdue_orders": overdue_orders,
            },
            "pipeline": {
                "awaiting_procurement": awaiting_procurement,
                "in_procurement": in_procurement,
                "awaiting_delivery": awaiting_delivery,
                "partially_delivered": partially_delivered,
                "in_fitting": in_fitting,
                "completed": completed_orders,
            },
            "financials": {
                "total_revenue": float(total_revenue),
                "total_gross_order_amount": float(total_gross_order_amount),
                "total_procurement_cost": float(total_procurement_cost),
                "gross_profit": float(gross_profit),
                "gross_margin_percentage": gross_margin_pct,
                "total_customer_paid": float(total_customer_paid),
                "customer_outstanding": float(customer_outstanding),
                "total_vendor_billed": float(total_vendor_billed),
                "total_vendor_paid": float(total_vendor_paid),
                "vendor_outstanding": float(vendor_outstanding),
                "operating_cash_margin": float(operating_cash_margin),
            },
        }

    @classmethod
    def get_overdue_orders_list(cls, org_id: int) -> List[Dict[str, Any]]:
        """Return detailed list of orders past their expected delivery dates."""
        now = datetime.utcnow()
        overdue = (
            OrderMaster.query.filter(
                OrderMaster.organization_id == org_id,
                OrderMaster.is_deleted == False,
                OrderMaster.status.notin_(["COMPLETED", "CLOSED", "CANCELLED"]),
                OrderMaster.expected_delivery_date != None,
                OrderMaster.expected_delivery_date < now,
            )
            .order_by(OrderMaster.expected_delivery_date.asc())
            .all()
        )

        results = []
        for o in overdue:
            days_overdue = (now.date() - o.expected_delivery_date.date()).days
            results.append({
                "id": o.id,
                "order_number": o.master_order_number,
                "title": o.order_title,
                "status": o.status,
                "customer_name": o.customer.name if o.customer else "Unknown",
                "expected_delivery_date": o.expected_delivery_date.strftime("%Y-%m-%d"),
                "days_overdue": days_overdue,
                "net_amount": float(o.net_order_amount),
            })
        return results

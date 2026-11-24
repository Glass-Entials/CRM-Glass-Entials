import unittest
from decimal import Decimal
from datetime import datetime
from flask import Flask
from model import (
    db,
    Organization,
    Customer,
    Vendor,
    Product,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderItemVendorAllocation,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    DeliveryItem,
    OrderFittingVisit,
    FittingVisitItem,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    OrderConsignmentStatus,
    OrderFittingVisitStatus,
)
from services.oms import (
    OrderMasterService,
    ProcurementService,
    DeliveryService,
    FinancialReconciliationService,
    OMSStatusEngine,
    OMSTransitionError,
    OMSValidationError,
)


class OMSParallelAndPartialWorkflowsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_parallel_workflows_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="parallel-workflows-secret-key",
        )
        db.init_app(cls.app)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()

    @classmethod
    def tearDownClass(cls):
        cls.app_context.pop()

    def setUp(self):
        db.create_all()

        import uuid
        # Seed Organization, Customer, 2 Vendors
        self.org = Organization(name="GlassEntials Test Org", unique_code=f"GTO_{uuid.uuid4().hex[:6]}")
        db.session.add(self.org)
        db.session.flush()

        self.customer = Customer(organization_id=self.org.id, name="Corporate Towers Ltd", email="pm@corptowers.com", phone_number="+919876543210", created_by=1)
        self.vendor_a = Vendor(organization_id=self.org.id, code="VEN001", company_name="Saint-Gobain Glass India")
        self.vendor_b = Vendor(organization_id=self.org.id, code="VEN002", company_name="Ozone Architectural Hardware")
        self.product = Product(organization_id=self.org.id, name="12mm Toughened Glass", unit="sqft", selling_price=120)

        db.session.add_all([self.customer, self.vendor_a, self.vendor_b, self.product])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.drop_all()

    def test_multi_vendor_partial_procurement_aggregation(self):
        """Verify that Master Order status correctly reflects partial procurement across multiple vendors."""
        # Create Master Order with 2 line items: Glass (Saint-Gobain) and Hardware (Ozone)
        order = OrderMasterService.create_order(
            org_id=self.org.id,
            customer_id=self.customer.id,
            order_title="Dual Vendor Facade Project",
            items_data=[
                {"item_description": "12mm Toughened Glass", "quantity": 100, "unit_price": 500},
                {"item_description": "Spider Fittings", "quantity": 50, "unit_price": 200},
            ],
            actor_id=1,
        )
        OrderMasterService.transition_order_status(
            org_id=self.org.id, order_id=order.id, to_status=MasterOrderStatus.CONFIRMED.value, actor_id=1
        )
        db.session.commit()
        self.assertEqual(order.status, MasterOrderStatus.CONFIRMED.value)

        item_glass = order.items[0]
        item_hardware = order.items[1]

        # Allocate only Glass to Vendor A (qty 100), leave Hardware unallocated
        alloc_glass = OrderItemVendorAllocation(
            organization_id=self.org.id,
            order_id=order.id,
            order_item_id=item_glass.id,
            vendor_id=self.vendor_a.id,
            allocated_quantity=Decimal("100.000"),
            expected_rate=Decimal("350.00"),
            estimated_cost=Decimal("35000.00"),
            created_by_id=1,
        )
        item_glass.allocated_quantity = Decimal("100.000")
        db.session.add(alloc_glass)
        db.session.commit()

        # Recalculate procurement: Allocated but not yet procured
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()
        self.assertEqual(order.procurement_status, OrderProcurementStatus.PO_ISSUED.value)
        self.assertEqual(order.status, MasterOrderStatus.IN_PROCUREMENT.value)

        # Receive Glass (100 units procured for Item 1, 0 for Item 2)
        item_glass.procured_quantity = Decimal("100.000")
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()

        # Must be PARTIALLY_RECEIVED because Hardware (50 units) is still unprocured
        self.assertEqual(order.procurement_status, OrderProcurementStatus.PARTIALLY_RECEIVED.value)
        self.assertEqual(order.status, MasterOrderStatus.PARTIALLY_RECEIVED.value)

        # Allocate and receive Hardware from Vendor B
        alloc_hw = OrderItemVendorAllocation(
            organization_id=self.org.id,
            order_id=order.id,
            order_item_id=item_hardware.id,
            vendor_id=self.vendor_b.id,
            allocated_quantity=Decimal("50.000"),
            expected_rate=Decimal("150.00"),
            estimated_cost=Decimal("7500.00"),
            created_by_id=1,
        )
        item_hardware.allocated_quantity = Decimal("50.000")
        item_hardware.procured_quantity = Decimal("50.000")
        db.session.add(alloc_hw)
        db.session.commit()

        # Now all 150 units are procured -> Should be RECEIVED
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()
        self.assertEqual(order.procurement_status, OrderProcurementStatus.RECEIVED.value)
        self.assertEqual(order.status, MasterOrderStatus.RECEIVED.value)

    def test_multi_consignment_partial_delivery_aggregation(self):
        """Verify delivery aggregation when order is shipped via multiple consignments."""
        order = OrderMasterService.create_order(
            org_id=self.org.id,
            customer_id=self.customer.id,
            order_title="Phased Logistics Order",
            items_data=[
                {"item_description": "Curtain Wall Glass", "quantity": 60, "unit_price": 1000},
            ],
            actor_id=1,
        )
        order.status = MasterOrderStatus.RECEIVED.value
        order.fitting_status = OrderFittingStatus.PENDING_SCHEDULE.value
        db.session.commit()
        item = order.items[0]
        item.procured_quantity = Decimal("60.000")

        # Phase 1: Deliver 30 units (Consignment 1)
        item.delivered_quantity = Decimal("30.000")
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()

        # Must transition to PARTIALLY_DELIVERED
        self.assertEqual(order.delivery_status, OrderDeliveryStatus.PARTIALLY_DELIVERED.value)
        self.assertEqual(order.status, MasterOrderStatus.PARTIALLY_DELIVERED.value)

        # Phase 2: Deliver remaining 30 units (Consignment 2)
        item.delivered_quantity = Decimal("60.000")
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()

        # All 60 delivered; fitting is pending schedule -> DELIVERED
        self.assertEqual(order.delivery_status, OrderDeliveryStatus.DELIVERED.value)
        self.assertEqual(order.status, MasterOrderStatus.DELIVERED.value)

    def test_fitting_progress_and_customer_payment_independence(self):
        """Verify that physical completion advances status to COMPLETED while customer payment remains tracked."""
        order = OrderMasterService.create_order(
            org_id=self.org.id,
            customer_id=self.customer.id,
            order_title="Installation & Payment Independence Test",
            items_data=[
                {"item_description": "Shower Enclosure", "quantity": 10, "unit_price": 2000},
            ],
            actor_id=1,
        )
        order.status = MasterOrderStatus.DELIVERED.value
        order.fitting_status = OrderFittingStatus.PENDING_SCHEDULE.value
        db.session.commit()
        item = order.items[0]
        item.procured_quantity = Decimal("10.000")
        item.delivered_quantity = Decimal("10.000")
        order.delivery_status = OrderDeliveryStatus.DELIVERED.value

        # Customer paid advance of 50%, remaining 50% unpaid
        order.net_order_amount = Decimal("20000.00")
        order.total_customer_paid = Decimal("10000.00")
        order.customer_payment_status = OrderCustomerPaymentStatus.PARTIALLY_PAID.value

        # Installation visit in progress
        visit = OrderFittingVisit(
            organization_id=self.org.id,
            order_id=order.id,
            visit_number="VISIT-001",
            scheduled_date=datetime.utcnow(),
            status=OrderFittingVisitStatus.IN_PROGRESS.value,
        )
        db.session.add(visit)
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()

        # Master status must be IN_FITTING
        self.assertEqual(order.fitting_status, OrderFittingStatus.IN_PROGRESS.value)
        self.assertEqual(order.status, MasterOrderStatus.IN_FITTING.value)

        # Installation complete: all 10 units fitted
        item.installed_quantity = Decimal("10.000")
        visit.status = OrderFittingVisitStatus.COMPLETED.value
        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()

        # Physical execution is COMPLETED
        self.assertEqual(order.fitting_status, OrderFittingStatus.COMPLETED.value)
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)
        self.assertIsNotNone(order.completed_at)

        # CRITICAL INVARIANT: Customer payment status remains PARTIALLY_PAID
        self.assertEqual(order.customer_payment_status, OrderCustomerPaymentStatus.PARTIALLY_PAID.value)
        self.assertEqual(order.total_customer_paid, Decimal("10000.00"))

    def test_fitting_not_required_completes_upon_delivery(self):
        """When an order has fitting_status NOT_REQUIRED, full delivery automatically completes the master order."""
        order = OrderMasterService.create_order(
            org_id=self.org.id,
            customer_id=self.customer.id,
            order_title="Supply Only Contract",
            items_data=[
                {"item_description": "Supply Only Glass Sheets", "quantity": 40, "unit_price": 800},
            ],
            actor_id=1,
        )
        OrderMasterService.transition_order_status(
            org_id=self.org.id, order_id=order.id, to_status=MasterOrderStatus.CONFIRMED.value, actor_id=1
        )
        order.fitting_status = OrderFittingStatus.NOT_REQUIRED.value
        db.session.commit()

        item = order.items[0]
        item.procured_quantity = Decimal("40.000")
        item.delivered_quantity = Decimal("40.000")
        order.delivery_status = OrderDeliveryStatus.DELIVERED.value

        OMSStatusEngine.synchronize_order_master_status(order)
        db.session.commit()

        # Fully delivered and fitting NOT_REQUIRED -> Automatically COMPLETED
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)
        self.assertEqual(order.fitting_status, OrderFittingStatus.NOT_REQUIRED.value)


if __name__ == "__main__":
    unittest.main()

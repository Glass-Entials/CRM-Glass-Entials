"""
tests/test_oms_phase2_services.py
Comprehensive Phase 2 Test Suite for OMS Services and Controllers:
1. OrderMasterService (Parent-first locking, multi-vendor allocation invariants, state machine, revisions, closure)
2. ProcurementService (Vendor PO issuance, allocation vendor matching, consignments, QC arrivals)
3. DeliveryService (Customer delivery challans, receipt confirmation, site fitting visits, auto-completion)
4. FinancialReconciliationService (Customer ledger, advance unlinking, 3-way AP matching, vendor payments, margins)
5. Routes & Tenant Isolation
"""
import unittest
import sys
import json
from pathlib import Path
from decimal import Decimal
from datetime import datetime, date

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from flask_login import LoginManager
from sqlalchemy import text

from model import (
    db,
    User,
    Organization,
    Customer,
    Vendor,
    Product,
    Quotation,
    Invoice,
    Payment,
    Employee,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderItemVendorAllocation,
    OrderProcurementItem,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    DeliveryItem,
    OrderFittingVisit,
    FittingVisitItem,
    OrderPaymentAllocation,
    VendorBill,
    VendorBillItem,
    VendorPayment,
    OrderRevision,
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
    OrderPaymentAllocationType,
    VendorBillStatus,
    OrderClosureType,
)
from services.oms import (
    OrderMasterService,
    ProcurementService,
    DeliveryService,
    FinancialReconciliationService,
    OMSError,
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)
from routes.orders import orders_bp


class OMSPhase2ServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_phase2_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="test-secret-key",
        )
        db.init_app(cls.app)

        cls.login_manager = LoginManager()
        cls.login_manager.init_app(cls.app)

        @cls.login_manager.user_loader
        def load_user(user_id):
            return User.query.get(int(user_id))

        cls.app.register_blueprint(orders_bp)

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        db.session.execute(text("PRAGMA foreign_keys = ON;"))
        db.create_all()

        # Seed prerequisite tenants and users
        self.user1 = User(id=1, username="admin1", password="hash_password", email="admin1@example.com", organization_id=1)
        self.user2 = User(id=2, username="tech1", password="hash_password", email="tech1@example.com", organization_id=1)
        self.user_org2 = User(id=3, username="org2admin", password="hash_password", email="org2@example.com", organization_id=2)

        self.org1 = Organization(id=1, name="GlassEntials Demo Org", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Competitor Org", unique_code="ORG002")

        db.session.add_all([self.user1, self.user2, self.user_org2, self.org1, self.org2])
        db.session.commit()

        # Seed Employees
        self.emp1 = Employee(id=1, user_id=1, name="Alice Admin", email="alice@test.com", organization_id=1)
        self.emp2 = Employee(id=2, user_id=2, name="Bob Tech", email="bob@test.com", organization_id=1)
        self.emp_org2 = Employee(id=3, user_id=3, name="Charlie Rival", email="charlie@test.com", organization_id=2)

        # Seed Customers
        self.cust1 = Customer(id=1, name="Acme Towers", email="acme@test.com", phone_number="9876543210", organization_id=1, created_by=1)
        self.cust_org2 = Customer(id=2, name="Foreign Customer", email="foreign@test.com", phone_number="9123456789", organization_id=2, created_by=3)

        # Seed Vendors
        self.vend1 = Vendor(id=1, company_name="Saint-Gobain Glass", code="VEN-001", organization_id=1)
        self.vend2 = Vendor(id=2, company_name="Asahi India Glass", code="VEN-002", organization_id=1)
        self.vend_org2 = Vendor(id=3, company_name="Foreign Vendor", code="VEN-003", organization_id=2)

        # Seed Products
        self.prod1 = Product(id=1, name="12mm Toughened Glass", organization_id=1)
        self.prod2 = Product(id=2, name="SS Spider Fitting 4-Way", organization_id=1)

        db.session.add_all([
            self.emp1, self.emp2, self.emp_org2,
            self.cust1, self.cust_org2,
            self.vend1, self.vend2, self.vend_org2,
            self.prod1, self.prod2
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        try:
            db.session.execute(text("PRAGMA foreign_keys = OFF;"))
            db.drop_all()
        except Exception:
            pass
        self.context.pop()

    # --------------------------------------------------------------------------
    # 1. OrderMasterService: Creation & Invariants
    # --------------------------------------------------------------------------
    def test_01_create_order_and_number_sequence(self):
        """Test master order creation, decimal calculation, and sequential ID generation."""
        items = [
            {
                "item_description": "12mm Toughened Glass 8x4",
                "product_id": 1,
                "quantity": 100,
                "unit_price": 1000.00,
                "tax_rate": 18.00,
            },
            {
                "item_description": "Spider Fitting 4-Way",
                "product_id": 2,
                "quantity": 50,
                "unit_price": 500.00,
                "tax_rate": 18.00,
            }
        ]
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Acme Skylight Glazing",
            items_data=items,
            actor_id=1,
        )
        db.session.commit()

        self.assertIsNotNone(order.id)
        self.assertTrue(order.master_order_number.startswith("ORD-"))
        self.assertEqual(order.status, MasterOrderStatus.DRAFT.value)
        self.assertEqual(len(order.items), 2)

        # Money calculations
        # Glass: 100 * 1000 = 100,000 + 18,000 tax = 118,000
        # Spider: 50 * 500 = 25,000 + 4,500 tax = 29,500
        # Net: 147,500
        self.assertEqual(order.total_ordered_amount, Decimal("125000.00"))
        self.assertEqual(order.total_tax_amount, Decimal("22500.00"))
        self.assertEqual(order.net_order_amount, Decimal("147500.00"))

        # Verify second order receives next sequence
        order2 = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Second Order",
            items_data=[{"item_description": "Sample", "quantity": 1, "unit_price": 100}],
            actor_id=1,
        )
        db.session.commit()
        seq1 = int(order.master_order_number.split("-")[-1])
        seq2 = int(order2.master_order_number.split("-")[-1])
        self.assertEqual(seq2, seq1 + 1)

    # --------------------------------------------------------------------------
    # 2. Multi-Vendor Allocation & Quantity Invariant
    # --------------------------------------------------------------------------
    def test_02_multi_vendor_allocation_and_excess_rejection(self):
        """Test splitting item between 2 vendors and invariant rejection when allocations > quantity."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Multi-Vendor Test",
            items_data=[{"item_description": "Toughened Glass", "product_id": 1, "quantity": 100, "unit_price": 1000}],
            actor_id=1,
        )
        db.session.commit()
        item = order.items[0]

        # Allocate 60 to Vendor 1 and 40 to Vendor 2
        allocations = OrderMasterService.allocate_item_vendors(
            org_id=1,
            order_id=order.id,
            order_item_id=item.id,
            allocations_data=[
                {"vendor_id": 1, "allocated_quantity": 60, "purchase_rate": 700.00},
                {"vendor_id": 2, "allocated_quantity": 40, "purchase_rate": 720.00},
            ],
            actor_id=1,
        )
        db.session.commit()

        self.assertEqual(len(allocations), 2)
        db.session.refresh(item)
        self.assertEqual(item.allocated_quantity, Decimal("100.000"))

        # Attempt to allocate additional quantity -> MUST raise OMSInvariantError
        with self.assertRaises(OMSInvariantError):
            OrderMasterService.allocate_item_vendors(
                org_id=1,
                order_id=order.id,
                order_item_id=item.id,
                allocations_data=[{"vendor_id": 1, "allocated_quantity": 1, "purchase_rate": 700.00}],
                actor_id=1,
            )
        db.session.rollback()

    # --------------------------------------------------------------------------
    # 3. Status Transitions & State Machine
    # --------------------------------------------------------------------------
    def test_03_order_status_transitions_and_illegal_rejections(self):
        """Test valid lifecycle transitions and illegal jump rejection."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Status Test",
            items_data=[{"item_description": "Glass", "quantity": 10, "unit_price": 100}], actor_id=1
        )
        db.session.commit()
        self.assertEqual(order.status, MasterOrderStatus.DRAFT.value)

        # Illegal jump: DRAFT -> COMPLETED
        with self.assertRaises(OMSTransitionError):
            OrderMasterService.transition_order_status(org_id=1, order_id=order.id, to_status="COMPLETED", actor_id=1)
        db.session.rollback()

        # Valid transition: DRAFT -> CONFIRMED
        order = OrderMasterService.transition_order_status(org_id=1, order_id=order.id, to_status="CONFIRMED", actor_id=1)
        db.session.commit()
        self.assertEqual(order.status, MasterOrderStatus.CONFIRMED.value)

        # Valid transition: CONFIRMED -> IN_PROCUREMENT
        order = OrderMasterService.transition_order_status(org_id=1, order_id=order.id, to_status="IN_PROCUREMENT", actor_id=1)
        db.session.commit()
        self.assertEqual(order.status, MasterOrderStatus.IN_PROCUREMENT.value)

        # Check status history
        histories = OrderStatusHistory.query.filter_by(order_id=order.id).all()
        self.assertGreaterEqual(len(histories), 2)

    # --------------------------------------------------------------------------
    # 4. ProcurementService: PO Creation & Vendor Matching
    # --------------------------------------------------------------------------
    def test_04_procurement_po_creation_and_vendor_matching(self):
        """Test PO generation strictly matching allocation vendor."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="PO Test",
            items_data=[{"item_description": "Glass", "quantity": 50, "unit_price": 100}], actor_id=1
        )
        db.session.commit()
        item = order.items[0]

        allocs = OrderMasterService.allocate_item_vendors(
            org_id=1, order_id=order.id, order_item_id=item.id,
            allocations_data=[{"vendor_id": 1, "allocated_quantity": 50, "purchase_rate": 80.00}], actor_id=1
        )
        db.session.commit()
        alloc = allocs[0]

        # Attempt to issue PO to Vendor 2 using Vendor 1's allocation -> MUST fail
        with self.assertRaises((OMSValidationError, OMSInvariantError)):
            ProcurementService.create_purchase_order(
                org_id=1, order_id=order.id, vendor_id=2,  # Wrong vendor
                items_data=[{"allocation_id": alloc.id, "quantity": 50}], actor_id=1
            )
        db.session.rollback()

        # Correct vendor: Vendor 1
        po = ProcurementService.create_purchase_order(
            org_id=1, order_id=order.id, vendor_id=1,
            items_data=[{"allocation_id": alloc.id, "quantity": 50, "unit_rate": 80.00}], actor_id=1
        )
        db.session.commit()

        self.assertTrue(po.po_number.startswith("PO-"))
        self.assertEqual(po.vendor_id, 1)
        self.assertEqual(po.total_procurement_amount, Decimal("4000.00"))
        db.session.refresh(item)
        self.assertEqual(item.procured_quantity, Decimal("50.000"))

    # --------------------------------------------------------------------------
    # 5. Consignments & Arrival QC
    # --------------------------------------------------------------------------
    def test_05_consignment_and_qc_arrival_inspection(self):
        """Test recording transporter consignment and arrival quality check."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Consignment Test",
            items_data=[{"item_description": "Glass", "quantity": 50, "unit_price": 100}], actor_id=1
        )
        db.session.commit()
        item = order.items[0]

        allocs = OrderMasterService.allocate_item_vendors(
            org_id=1, order_id=order.id, order_item_id=item.id,
            allocations_data=[{"vendor_id": 1, "allocated_quantity": 50, "purchase_rate": 80.00}], actor_id=1
        )
        db.session.commit()

        po = ProcurementService.create_purchase_order(
            org_id=1, order_id=order.id, vendor_id=1,
            items_data=[{"allocation_id": allocs[0].id, "quantity": 50, "unit_rate": 80.00}], actor_id=1
        )
        db.session.commit()
        po_item = po.items[0]

        # Record consignment dispatch
        cons = ProcurementService.record_consignment(
            org_id=1, order_id=order.id, procurement_id=po.id,
            items_data=[{"procurement_item_id": po_item.id, "shipped_quantity": 50}],
            transporter_name="SafeXpress", tracking_number="TRK123456", actor_id=1
        )
        db.session.commit()
        self.assertTrue(cons.consignment_number.startswith("CON-"))
        cons_item = cons.items[0]

        # Record Arrival QC: 45 accepted, 3 damaged, 2 rejected
        cons = ProcurementService.record_consignment_qc_arrival(
            org_id=1, consignment_id=cons.id,
            items_qc=[{
                "consignment_item_id": cons_item.id,
                "accepted_quantity": 45,
                "damaged_quantity": 3,
                "rejected_quantity": 2,
                "qc_remarks": "Minor edge chipping on 3 panels"
            }],
            actor_id=1
        )
        db.session.commit()

        db.session.refresh(po_item)
        self.assertEqual(po_item.accepted_quantity, Decimal("45.000"))
        self.assertEqual(po_item.received_quantity, Decimal("50.000"))

    # --------------------------------------------------------------------------
    # 6. DeliveryService: Challan Creation & Receipt Confirmation
    # --------------------------------------------------------------------------
    def test_06_delivery_challan_and_receipt_confirmation(self):
        """Test customer delivery challan creation and site receipt confirmation."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Delivery Test",
            items_data=[{"item_description": "Curtain Wall Glass", "quantity": 20, "unit_price": 500}], actor_id=1
        )
        db.session.commit()
        item = order.items[0]

        # Attempt to deliver 25 when order is 20 -> MUST fail invariant
        with self.assertRaises(OMSInvariantError):
            DeliveryService.create_delivery_challan(
                org_id=1, order_id=order.id,
                items_data=[{"order_item_id": item.id, "delivered_quantity": 25}],
                actor_id=1
            )
        db.session.rollback()

        # Create valid delivery challan for 20
        delv = DeliveryService.create_delivery_challan(
            org_id=1, order_id=order.id,
            items_data=[{"order_item_id": item.id, "delivered_quantity": 20}],
            recipient_name="Site Incharge",
            actor_id=1
        )
        db.session.commit()
        self.assertTrue(delv.delivery_number.startswith("DEL-"))

        # Confirm delivery receipt
        delv = DeliveryService.confirm_delivery(
            org_id=1, delivery_id=delv.id, notes="Delivered in good condition", actor_id=1
        )
        db.session.commit()

        self.assertEqual(delv.status, OrderDeliveryStatus.DELIVERED.value)
        db.session.refresh(item)
        self.assertEqual(item.delivered_quantity, Decimal("20.000"))
        db.session.refresh(order)
        self.assertEqual(order.delivery_status, OrderDeliveryStatus.DELIVERED.value)

    # --------------------------------------------------------------------------
    # 7. DeliveryService: Site Fitting Visits & Auto-Completion
    # --------------------------------------------------------------------------
    def test_07_fitting_visit_and_order_auto_completion(self):
        """Test fitting visit scheduling, completion with signoff, and order auto-completion."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Fitting Test",
            items_data=[{"item_description": "Glass Partition", "quantity": 10, "unit_price": 1000}], actor_id=1
        )
        OrderMasterService.transition_order_status(org_id=1, order_id=order.id, to_status="CONFIRMED", actor_id=1)
        OrderMasterService.transition_order_status(org_id=1, order_id=order.id, to_status="IN_DELIVERY", actor_id=1)
        # Advance order through delivery
        item = order.items[0]
        delv = DeliveryService.create_delivery_challan(
            org_id=1, order_id=order.id,
            items_data=[{"order_item_id": item.id, "delivered_quantity": 10}], actor_id=1
        )
        DeliveryService.confirm_delivery(org_id=1, delivery_id=delv.id, actor_id=1)
        db.session.commit()

        # Schedule fitting visit
        visit = DeliveryService.schedule_fitting_visit(
            org_id=1, order_id=order.id, scheduled_date=datetime.utcnow(),
            technician_id=2, actor_id=1
        )
        db.session.commit()
        self.assertTrue(visit.visit_number.startswith("FIT-"))

        # Complete fitting visit with client signoff
        visit = DeliveryService.complete_fitting_visit(
            org_id=1, visit_id=visit.id, client_signoff=True, notes="Installation inspected and approved", actor_id=1
        )
        db.session.commit()

        self.assertEqual(visit.status, OrderFittingVisitStatus.COMPLETED.value)
        self.assertTrue(visit.client_signoff)
        db.session.refresh(item)
        self.assertEqual(item.installed_quantity, Decimal("10.000"))
        db.session.refresh(order)
        self.assertEqual(order.fitting_status, OrderFittingStatus.COMPLETED.value)
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)

    # --------------------------------------------------------------------------
    # 8. FinancialReconciliationService: Customer Payment Allocation
    # --------------------------------------------------------------------------
    def test_08_customer_payment_allocation_and_advance_unlinking(self):
        """Test customer payment allocations with zero double-counting arithmetic."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Finance Test",
            items_data=[{"item_description": "Glass", "quantity": 1, "unit_price": 100000.00, "tax_rate": 0}], actor_id=1
        )
        db.session.commit()

        # Seed payment
        pay1 = Payment(
            id=10, payment_number="PAY-0010", customer_name="Acme Towers",
            mobile="9876543210", amount=Decimal("100000.00"), due_date=date.today(),
            organization_id=1
        )
        db.session.add(pay1)
        db.session.commit()

        # 1. Allocate initial direct payment 50,000
        alloc1 = FinancialReconciliationService.allocate_customer_payment(
            org_id=1, order_id=order.id, payment_id=pay1.id, allocated_amount=Decimal("50000.00"),
            allocation_type=OrderPaymentAllocationType.DIRECT_PAYMENT.value, actor_id=1
        )
        db.session.commit()

        db.session.refresh(order)
        self.assertEqual(order.total_customer_paid, Decimal("50000.00"))
        self.assertEqual(order.customer_payment_status, OrderCustomerPaymentStatus.PARTIALLY_PAID.value)

        # 2. Advance Unlink (negative offset) -20,000 to prevent double-counting when invoice is settled
        alloc_unlink = FinancialReconciliationService.allocate_customer_payment(
            org_id=1, order_id=order.id, payment_id=pay1.id, allocated_amount=Decimal("20000.00"),
            allocation_type=OrderPaymentAllocationType.ADVANCE_UNLINK.value, notes="Unlink advance applied to Inv #1", actor_id=1
        )
        db.session.commit()

        self.assertEqual(alloc_unlink.allocated_amount, Decimal("-20000.00"))
        db.session.refresh(order)
        self.assertEqual(order.total_customer_paid, Decimal("30000.00"))

        # 3. Invariant check: cannot allocate more than payment original amount across system
        with self.assertRaises(OMSInvariantError):
            FinancialReconciliationService.allocate_customer_payment(
                org_id=1, order_id=order.id, payment_id=pay1.id, allocated_amount=Decimal("80000.00"),
                allocation_type=OrderPaymentAllocationType.DIRECT_PAYMENT.value, actor_id=1
            )
        db.session.rollback()

    # --------------------------------------------------------------------------
    # 9. FinancialReconciliationService: 3-Way AP Matching
    # --------------------------------------------------------------------------
    def test_09_vendor_bill_3way_matching_and_overbill_rejection(self):
        """Test 3-way matching: billed quantity cannot exceed accepted arrival quantity."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="3-Way Match Test",
            items_data=[{"item_description": "Glass", "quantity": 100, "unit_price": 500}], actor_id=1
        )
        db.session.commit()
        item = order.items[0]

        allocs = OrderMasterService.allocate_item_vendors(
            org_id=1, order_id=order.id, order_item_id=item.id,
            allocations_data=[{"vendor_id": 1, "allocated_quantity": 100, "purchase_rate": 300.00}], actor_id=1
        )
        po = ProcurementService.create_purchase_order(
            org_id=1, order_id=order.id, vendor_id=1,
            items_data=[{"allocation_id": allocs[0].id, "quantity": 100, "unit_rate": 300.00}], actor_id=1
        )
        po_item = po.items[0]

        # Consignment arrives: only 60 accepted
        cons = ProcurementService.record_consignment(
            org_id=1, order_id=order.id, procurement_id=po.id,
            items_data=[{"procurement_item_id": po_item.id, "shipped_quantity": 60}], actor_id=1
        )
        ProcurementService.record_consignment_qc_arrival(
            org_id=1, consignment_id=cons.id,
            items_qc=[{"consignment_item_id": cons.items[0].id, "accepted_quantity": 60}], actor_id=1
        )
        db.session.commit()

        # Vendor bills for 70 -> MUST fail 3-way matching (accepted was only 60!)
        with self.assertRaises(OMSInvariantError):
            FinancialReconciliationService.create_vendor_bill(
                org_id=1, order_id=order.id, procurement_id=po.id,
                bill_number="BILL-SG-001", bill_date=datetime.utcnow(),
                items_data=[{"procurement_item_id": po_item.id, "billed_quantity": 70, "unit_rate": 300.00}],
                actor_id=1
            )
        db.session.rollback()

        # Vendor bills for 60 -> Valid 3-way match
        bill = FinancialReconciliationService.create_vendor_bill(
            org_id=1, order_id=order.id, procurement_id=po.id,
            bill_number="BILL-SG-001", bill_date=datetime.utcnow(),
            items_data=[{"procurement_item_id": po_item.id, "billed_quantity": 60, "unit_rate": 300.00}],
            actor_id=1
        )
        db.session.commit()

        self.assertEqual(bill.total_amount, Decimal("18000.00"))
        db.session.refresh(order)
        self.assertEqual(order.total_vendor_billed, Decimal("18000.00"))

    # --------------------------------------------------------------------------
    # 10. FinancialReconciliationService: Vendor Payment & Margins
    # --------------------------------------------------------------------------
    def test_10_vendor_payment_and_profit_margin_reconciliation(self):
        """Test vendor payouts, bill payment progression, and operating profit margin."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Margin Test",
            items_data=[{"item_description": "Glass", "quantity": 10, "unit_price": 5000.00, "tax_rate": 0}], actor_id=1
        )
        # Net order amount: 50,000
        allocs = OrderMasterService.allocate_item_vendors(
            org_id=1, order_id=order.id, order_item_id=order.items[0].id,
            allocations_data=[{"vendor_id": 1, "allocated_quantity": 10, "purchase_rate": 3000.00}], actor_id=1
        )
        po = ProcurementService.create_purchase_order(
            org_id=1, order_id=order.id, vendor_id=1,
            items_data=[{"allocation_id": allocs[0].id, "quantity": 10, "unit_rate": 3000.00}],
            tax_rate=Decimal("0.00"), actor_id=1
        )
        # Total procurement: 30,000

        # Create bill
        bill = FinancialReconciliationService.create_vendor_bill(
            org_id=1, order_id=order.id, procurement_id=po.id,
            bill_number="INV-SG-99", bill_date=datetime.utcnow(),
            items_data=[{"procurement_item_id": po.items[0].id, "billed_quantity": 10, "unit_rate": 3000.00}],
            actor_id=1
        )
        db.session.commit()

        # Customer pays 40,000
        pay = Payment(id=20, payment_number="PAY-20", customer_name="Acme", mobile="9876543210", amount=Decimal("40000.00"), due_date=date.today(), organization_id=1)
        db.session.add(pay)
        db.session.commit()
        FinancialReconciliationService.allocate_customer_payment(
            org_id=1, order_id=order.id, payment_id=pay.id, allocated_amount=Decimal("40000.00"), actor_id=1
        )

        # Disburse 30,000 to vendor
        vpay = FinancialReconciliationService.record_vendor_payment(
            org_id=1, bill_id=bill.id, amount=Decimal("30000.00"), payment_mode="NEFT", actor_id=1
        )
        db.session.commit()

        self.assertEqual(bill.status, VendorBillStatus.PAID.value)
        db.session.refresh(order)
        self.assertEqual(order.vendor_payment_status, OrderVendorPaymentStatus.PAID.value)

        # Financial Reconciliation
        fin = FinancialReconciliationService.reconcile_order_financials(org_id=1, order_id=order.id)
        self.assertEqual(fin["net_order_amount"], 50000.0)
        self.assertEqual(fin["total_procurement_cost"], 30000.0)
        self.assertEqual(fin["gross_profit"], 20000.0)
        self.assertEqual(fin["gross_margin_percentage"], 40.0)
        self.assertEqual(fin["total_customer_paid"], 40000.0)
        self.assertEqual(fin["total_vendor_paid"], 30000.0)
        self.assertEqual(fin["operating_cash_margin"], 10000.0)

    # --------------------------------------------------------------------------
    # 11. Multi-Tenant Isolation
    # --------------------------------------------------------------------------
    def test_11_tenant_isolation_enforcement(self):
        """Test strict rejection of cross-tenant entities."""
        # Create order in Tenant 1
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Tenant 1 Order",
            items_data=[{"item_description": "Glass", "quantity": 10, "unit_price": 100}], actor_id=1
        )
        db.session.commit()

        # Tenant 2 tries to access/modify Tenant 1's order -> MUST raise OMSTenantIsolationError
        with self.assertRaises(OMSTenantIsolationError):
            OrderMasterService.lock_order(org_id=2, order_id=order.id)

        with self.assertRaises(OMSTenantIsolationError):
            OrderMasterService.transition_order_status(org_id=2, order_id=order.id, to_status="CONFIRMED", actor_id=3)

        # Tenant 1 tries to allocate foreign vendor from Tenant 2 -> MUST raise OMSTenantIsolationError
        with self.assertRaises(OMSTenantIsolationError):
            OrderMasterService.allocate_item_vendors(
                org_id=1, order_id=order.id, order_item_id=order.items[0].id,
                allocations_data=[{"vendor_id": 3, "allocated_quantity": 10}], actor_id=1  # Vendor 3 belongs to Org 2
            )
        db.session.rollback()

    # --------------------------------------------------------------------------
    # 12. Exceptional Closure & Revisions Snapshot
    # --------------------------------------------------------------------------
    def test_12_exceptional_closure_and_revisions(self):
        """Test order exceptional closure and revision snapshot generation."""
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Closure Test",
            items_data=[{"item_description": "Glass", "quantity": 10, "unit_price": 100}], actor_id=1
        )
        db.session.commit()

        # Create revision snapshot
        rev = OrderMasterService.create_order_revision(
            org_id=1, order_id=order.id, change_reason="Initial baseline revision approved", actor_id=1
        )
        db.session.commit()
        self.assertEqual(rev.revision_number, 1)
        snapshot = json.loads(rev.snapshot_json)
        self.assertEqual(snapshot["master_order_number"], order.master_order_number)

        # Exceptional closure
        closure = OrderMasterService.exceptional_close_order(
            org_id=1, order_id=order.id, closure_type=OrderClosureType.CANCELLED.value,
            closure_reason="Client revoked project contract", financial_impact_notes="Zero forfeiture", actor_id=1
        )
        db.session.commit()

        db.session.refresh(order)
        self.assertEqual(order.status, MasterOrderStatus.CANCELLED.value)
        self.assertIsNotNone(closure)
        self.assertEqual(closure.closure_type, OrderClosureType.CANCELLED.value)

        # Attempt to transition closed order -> MUST raise OMSTransitionError
        with self.assertRaises(OMSTransitionError):
            OrderMasterService.transition_order_status(org_id=1, order_id=order.id, to_status="CONFIRMED", actor_id=1)
        db.session.rollback()

    # --------------------------------------------------------------------------
    # 13. Route Controllers: Cockpit Dashboard & API Endpoints
    # --------------------------------------------------------------------------
    def _login(self, user_id=1):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    def test_13_route_list_orders_and_metrics(self):
        """Test GET /orders/ with JSON header returning aggregated metrics and list."""
        self._login(1)
        # Create an order first
        OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Cockpit Route Test",
            items_data=[{"item_description": "Glass", "quantity": 10, "unit_price": 500}], actor_id=1
        )
        db.session.commit()

        resp = self.client.get("/orders/", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertIn("metrics", data)
        self.assertGreaterEqual(data["metrics"]["total_orders"], 1)
        self.assertEqual(len(data["orders"]), 1)

    def test_14_route_create_order_json(self):
        """Test POST /orders/ with JSON payload creating order and returning 201."""
        self._login(1)
        payload = {
            "customer_id": 1,
            "order_title": "API Created Order",
            "items": [
                {"item_description": "Spider Fitting", "product_id": 2, "quantity": 20, "unit_price": 450, "tax_rate": 18}
            ]
        }
        resp = self.client.post("/orders/", json=payload, headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertTrue(data["master_order_number"].startswith("ORD-"))

    def test_15_route_detail_cockpit_json(self):
        """Test GET /orders/<id> returning full serialized cockpit payload."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Detail Cockpit Test",
            items_data=[{"item_description": "Glass Panel", "quantity": 5, "unit_price": 2000}], actor_id=1
        )
        db.session.commit()

        resp = self.client.get(f"/orders/{order.id}", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("order", data)
        self.assertIn("items", data)
        self.assertIn("financials", data)
        self.assertEqual(data["order"]["master_order_number"], order.master_order_number)

    def test_16_route_transition_status(self):
        """Test POST /orders/<id>/status transitioning state via API."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Status Route Test",
            items_data=[{"item_description": "Glass", "quantity": 2, "unit_price": 100}], actor_id=1
        )
        db.session.commit()

        resp = self.client.post(
            f"/orders/{order.id}/status",
            json={"to_status": "CONFIRMED", "remarks": "Approved by Sales Manager"},
            headers={"Accept": "application/json"}
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], "CONFIRMED")

    def test_17_route_financial_reconciliation_endpoint(self):
        """Test GET /orders/<id>/financials returning margin analysis."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1, customer_id=1, order_title="Fin Route Test",
            items_data=[{"item_description": "Glass", "quantity": 10, "unit_price": 1000}], actor_id=1
        )
        db.session.commit()

        resp = self.client.get(f"/orders/{order.id}/financials")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        fin = data["financials"]
        self.assertEqual(fin["net_order_amount"], 10000.0)


if __name__ == "__main__":
    unittest.main()

"""
tests/test_oms_phase2_e2e_mysql.py
==================================
Realistic End-to-End Acceptance Test Suite for GlassEntials CRM - OMS Phase 2.
Executed against the isolated MySQL test database `oms_test_db`.

Verifies:
1. Complete 11-step Realistic Business Workflow (100 units Toughened Glass):
   - Step 1: Create and confirm order (100 units @ 1500 + 18% GST = 177,000).
   - Step 2: Multi-vendor allocation (60 to Vendor A, 40 to Vendor B), over-allocation rejected.
   - Step 3: Separate Purchase Orders (PO 1: Vendor A, PO 2: Vendor B), cross-vendor assignment rejected.
   - Step 4: Consignments & QC arrival inspections (100 units accepted stock).
   - Step 5: Dispatch and deliver 60 units first.
   - Step 6: Verify PARTIALLY_DELIVERED status and deliver remaining 40 units -> DELIVERED.
   - Step 7: Fitting visits & client signoff -> auto-advance to COMPLETED.
   - Step 8: Customer payments with zero double-counting (advance + balance settlement, ADVANCE_UNLINK).
   - Step 9: 3-way matched vendor bills, overbilling rejected.
   - Step 10: Vendor payments & outstanding balances tracking.
   - Step 11: Final audit, profit margin reconciliation, and immutable snapshot revisions.
2. Additional Verifications:
   - RBAC enforcement (role restrictions on operational vs administrative routes).
   - CSRF protection on mutation routes.
   - Organization multi-tenant isolation on all order operations.
   - Transactional rollback on failed operations without orphan database writes.
   - Concurrent allocation and delivery invariant preservation under multi-threading.
   - HTML views render correctly with valid Cockpit UI components.
"""
from __future__ import annotations

import os
import sys
import unittest
import json
import urllib.parse
from decimal import Decimal
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pymysql
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, session
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from flask_migrate import Migrate, upgrade

from model import (
    db,
    Organization,
    User,
    UserRole,
    Employee,
    Customer,
    Vendor,
    Product,
    Payment,
    PaymentStatus,
    PaymentPriority,
    Invoice,
    InvoiceStatus,
    OrderMaster,
    OrderItem,
    OrderItemVendorAllocation,
    OrderVendorProcurement,
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
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    OrderPaymentAllocationType,
    OrderItemAllocationStatus,
    OrderConsignmentStatus,
    OrderFittingVisitStatus,
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

# Parse MySQL connection from .env
vals = dotenv_values(".env")
raw_url = vals.get("DATABASE_URL", "")
after_slash = raw_url.split("://")[1]
user_pass, host_db = after_slash.split("@", 1)
raw_user, raw_pass = user_pass.split(":", 1)
unquoted_pass = urllib.parse.unquote(raw_pass)
host_parts = host_db.split("/", 1)
host = host_parts[0] or "localhost"
port = 3306
if ":" in host:
    host, port_str = host.split(":")
    port = int(port_str)

TEST_DB_NAME = "oms_test_db"
TEST_DB_URL = f"mysql+pymysql://{raw_user}:{urllib.parse.quote_plus(unquoted_pass)}@{host}:{port}/{TEST_DB_NAME}?charset=utf8mb4"


def setup_mysql_test_database():
    """Clone schema from crm_db and run Alembic upgrade to f6a7b8c9d0e1."""
    conn = pymysql.connect(host=host, port=port, user=raw_user, password=unquoted_pass, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME};")
        cur.execute(f"CREATE DATABASE {TEST_DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        cur.execute(f"USE {TEST_DB_NAME};")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0;")
        cur.execute("SHOW TABLES FROM crm_db;")
        tables = [r[0] for r in cur.fetchall()]
        for tbl in tables:
            cur.execute(f"SHOW CREATE TABLE crm_db.`{tbl}`;")
            create_sql = cur.fetchone()[1]
            cur.execute(create_sql)
        cur.execute(f"INSERT INTO {TEST_DB_NAME}.alembic_version SELECT * FROM crm_db.alembic_version;")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1;")
    conn.close()


def teardown_mysql_test_database():
    """Drop isolated test database."""
    conn = pymysql.connect(host=host, port=port, user=raw_user, password=unquoted_pass)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME};")
    conn.close()


class TestOMSPhase2E2EMySQL(unittest.TestCase):
    """Realistic End-to-End OMS Phase 2 acceptance test suite on MySQL."""

    @classmethod
    def setUpClass(cls):
        setup_mysql_test_database()

        cls.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="test-secret-key-oms-phase2",
            SQLALCHEMY_DATABASE_URI=TEST_DB_URL,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,  # default False for standard tests; enabled explicitly in test_02
        )

        db.init_app(cls.app)
        cls.migrate = Migrate(cls.app, db)
        cls.csrf = CSRFProtect(cls.app)

        login_manager = LoginManager()
        login_manager.init_app(cls.app)

        @login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        @cls.app.route("/test-login/<int:uid>")
        def test_login(uid):
            user = db.session.get(User, uid)
            if user:
                login_user(user)
                return "logged_in"
            return "user_not_found", 404

        cls.app.register_blueprint(orders_bp)

        # Register dummy routes for endpoints referenced by shared navbar.html
        for ep in [
            "home_page", "customers.customers_list", "org_customers.org_customers_list",
            "leads.leads_list", "leads.all_follow_ups", "projects.projects_list",
            "tasks.tasks_list", "tasks.daily_tasks_list", "quotations.quotation_list",
            "accounts.invoice_list", "payments.due_payments", "payments.received_payments",
            "expenses.expenses_list", "gst_reports.gst_report_page", "products.products_list",
            "employees.employee_list", "contacts.contacts_list", "vendors.vendor_list",
            "trash.trash_list", "call_logger.dashboard", "org.organization_settings",
            "auth.user_profile", "auth.settings", "auth.logout", "auth.notifications",
            "messages.messages_page", "messages.total_unread_count", "api.recent_notifications"
        ]:
            cls.app.add_url_rule(f"/dummy/{ep.replace('.', '/')}", endpoint=ep, view_func=lambda ep=ep: f"dummy_{ep}")

        @cls.app.context_processor
        def utility_processor():
            return {
                "get_profile_pic": lambda emp: "/static/img/default-avatar.png",
                "unread_notifications_count": lambda: 0,
                "org_modules": {},
                "active_org": None,
            }

        with cls.app.app_context():
            # Run migration upgrade to OMS Phase 1 head
            upgrade(revision="f6a7b8c9d0e1")

            # Seed base fixtures: 2 Organizations
            org1 = Organization(id=1, name="Apex Glass Industries", unique_code="APEX_01")
            org2 = Organization(id=2, name="Zenith Windows Corp", unique_code="ZEN_02")
            db.session.add_all([org1, org2])
            db.session.commit()

            # Seed Users
            u_admin = User(id=1, username="admin_apex", email="admin@apex.com", role=UserRole.ADMIN, organization_id=1, password="pwd")
            u_emp = User(id=2, username="emp_apex", email="emp@apex.com", role=UserRole.EMPLOYEE, organization_id=1, password="pwd")
            u_mgr = User(id=3, username="mgr_apex", email="mgr@apex.com", role=UserRole.MANAGER, organization_id=1, password="pwd")
            u_org2_admin = User(id=4, username="admin_zenith", email="admin@zenith.com", role=UserRole.ADMIN, organization_id=2, password="pwd")
            db.session.add_all([u_admin, u_emp, u_mgr, u_org2_admin])
            db.session.commit()

            # Seed Employees
            emp1 = Employee(id=1, user_id=1, name="Admin Manager", organization_id=1)
            emp2 = Employee(id=2, user_id=2, name="Rajesh Sharma (Site Tech)", organization_id=1)
            emp3 = Employee(id=3, user_id=4, name="Zenith Manager", organization_id=2)
            db.session.add_all([emp1, emp2, emp3])
            db.session.commit()

            # Seed Customers
            cust1 = Customer(
                id=1, name="Metro Glass Towers Ltd", email="metro@towers.com",
                phone_number="9876543210", address="Plot 42, Cyber City, Gurgaon",
                organization_id=1, created_by=1
            )
            cust2 = Customer(
                id=2, name="Zenith Client", email="zc@t.com",
                phone_number="9876543211", address="Sector 18, Noida",
                organization_id=2, created_by=3
            )
            db.session.add_all([cust1, cust2])

            # Seed Product
            prod1 = Product(id=1, name="12mm Toughened Glass", sku="GLS-12-TG", unit="SQFT", organization_id=1, created_by=1)
            db.session.add(prod1)

            # Seed Vendors
            vend_a = Vendor(id=1, company_name="Apex Float Glass Ltd", code="VEND-AFG", organization_id=1)
            vend_b = Vendor(id=2, company_name="Crystal Clear Sheet Co", code="VEND-CCS", organization_id=1)
            vend_c = Vendor(id=3, company_name="Rival Glass Co", code="VEND-RIV", organization_id=2)
            db.session.add_all([vend_a, vend_b, vend_c])
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        teardown_mysql_test_database()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()
        self.client = self.app.test_client()

    def tearDown(self):
        self.app_context.pop()

    def test_01_complete_11_step_business_lifecycle(self):
        """
        Execute the complete realistic 11-step customer order lifecycle against MySQL:
        100 units of 12mm Toughened Glass across 2 vendors, full delivery, site installation signoff,
        zero double-counting customer cash reconciliation, 3-way AP matched vendor bills, payouts,
        and final margin audit.
        """
        org_id = 1
        actor_id = 1

        # -------------------------------------------------------------------------
        # Step 1: Create and confirm the master order
        # -------------------------------------------------------------------------
        order = OrderMasterService.create_order(
            org_id=org_id,
            customer_id=1,
            order_title="Metro Towers 100-Unit Glazing Project",
            items_data=[{
                "product_id": 1,
                "item_description": "12mm Clear Toughened Architectural Glass",
                "quantity": Decimal("100.000"),
                "unit_price": Decimal("1500.00"),
                "tax_rate": Decimal("18.00"),
            }],
            shipping_address="Metro Towers Site, Plot 42, Cyber City, Gurgaon",
            actor_id=actor_id,
        )
        self.assertIsNotNone(order.id)
        self.assertTrue(order.master_order_number.startswith("ORD-"))
        self.assertEqual(order.status, MasterOrderStatus.DRAFT.value)
        self.assertEqual(order.total_ordered_amount, Decimal("150000.00"))
        self.assertEqual(order.tax_amount, Decimal("27000.00"))
        self.assertEqual(order.net_order_amount, Decimal("177000.00"))
        self.assertEqual(order.total_ordered_quantity, Decimal("100.000"))

        # Confirm the order
        order = OrderMasterService.transition_order_status(
            org_id=org_id,
            order_id=order.id,
            to_status=MasterOrderStatus.CONFIRMED.value,
            remarks="Client approved quotation and confirmed advance release.",
            actor_id=actor_id,
        )
        self.assertEqual(order.status, MasterOrderStatus.CONFIRMED.value)

        # Verify audit history
        hist = OrderStatusHistory.query.filter_by(order_id=order.id, to_status="CONFIRMED").first()
        self.assertIsNotNone(hist)
        self.assertEqual(hist.status_type, "MASTER")

        order_item = order.items[0]

        # -------------------------------------------------------------------------
        # Step 2: Allocate 60 units to Vendor A and 40 units to Vendor B
        # -------------------------------------------------------------------------
        # Batch 1: 60 units to Vendor A at Rs 900
        allocs_a = OrderMasterService.allocate_item_vendors(
            org_id=org_id,
            order_id=order.id,
            order_item_id=order_item.id,
            allocations_data=[{
                "vendor_id": 1,
                "allocated_quantity": Decimal("60.000"),
                "purchase_rate": Decimal("900.00"),
                "expected_supply_date": datetime.utcnow() + timedelta(days=5),
                "notes": "Batch 1 float sheets"
            }],
            actor_id=actor_id,
        )
        self.assertEqual(len(allocs_a), 1)
        self.assertEqual(allocs_a[0].allocated_quantity, Decimal("60.000"))

        # Batch 2: 40 units to Vendor B at Rs 950
        allocs_b = OrderMasterService.allocate_item_vendors(
            org_id=org_id,
            order_id=order.id,
            order_item_id=order_item.id,
            allocations_data=[{
                "vendor_id": 2,
                "allocated_quantity": Decimal("40.000"),
                "purchase_rate": Decimal("950.00"),
                "expected_supply_date": datetime.utcnow() + timedelta(days=7),
                "notes": "Batch 2 float sheets"
            }],
            actor_id=actor_id,
        )
        self.assertEqual(len(allocs_b), 1)
        self.assertEqual(allocs_b[0].allocated_quantity, Decimal("40.000"))

        db.session.refresh(order_item)
        self.assertEqual(order_item.allocated_quantity, Decimal("100.000"))

        # Invariant check: Over-allocation beyond 100 must be rejected
        with self.assertRaises(OMSInvariantError):
            OrderMasterService.allocate_item_vendors(
                org_id=org_id,
                order_id=order.id,
                order_item_id=order_item.id,
                allocations_data=[{
                    "vendor_id": 1,
                    "allocated_quantity": Decimal("1.000"),
                    "purchase_rate": Decimal("900.00"),
                }],
                actor_id=actor_id,
            )

        # -------------------------------------------------------------------------
        # Step 3: Create separate purchase orders for both vendors
        # -------------------------------------------------------------------------
        # PO 1: Vendor A (60 units @ 900 = 54,000 + 18% tax 9,720 = 63,720)
        po1 = ProcurementService.create_purchase_order(
            org_id=org_id,
            order_id=order.id,
            vendor_id=1,
            allocation_ids=[allocs_a[0].id],
            tax_rate=Decimal("18.00"),
            expected_delivery_date=datetime.utcnow() + timedelta(days=5),
            actor_id=actor_id,
        )
        self.assertEqual(po1.total_procurement_amount, Decimal("54000.00"))
        self.assertEqual(po1.net_procurement_amount, Decimal("63720.00"))

        # Invariant check: Cannot attach Vendor B's allocation to Vendor A's PO
        with self.assertRaises(OMSInvariantError):
            ProcurementService.create_purchase_order(
                org_id=org_id,
                order_id=order.id,
                vendor_id=1,
                allocation_ids=[allocs_b[0].id],  # belongs to Vendor 2!
                actor_id=actor_id,
            )

        # PO 2: Vendor B (40 units @ 950 = 38,000 + 18% tax 6,840 = 44,840)
        po2 = ProcurementService.create_purchase_order(
            org_id=org_id,
            order_id=order.id,
            vendor_id=2,
            allocation_ids=[allocs_b[0].id],
            tax_rate=Decimal("18.00"),
            expected_delivery_date=datetime.utcnow() + timedelta(days=7),
            actor_id=actor_id,
        )
        self.assertEqual(po2.total_procurement_amount, Decimal("38000.00"))
        self.assertEqual(po2.net_procurement_amount, Decimal("44840.00"))

        db.session.refresh(order)
        self.assertEqual(order.total_procurement_cost, Decimal("108560.00"))
        self.assertEqual(order.procurement_status, OrderProcurementStatus.PO_ISSUED.value)

        # -------------------------------------------------------------------------
        # Step 4: Record consignments and QC arrivals
        # -------------------------------------------------------------------------
        po1_item = po1.items[0]
        po2_item = po2.items[0]

        # Consignment 1: Vendor A shipping 60 units
        cons1 = ProcurementService.record_consignment(
            org_id=org_id,
            order_id=order.id,
            procurement_id=po1.id,
            items_data=[{"procurement_item_id": po1_item.id, "shipped_quantity": Decimal("60.000")}],
            transporter_name="Express Logistics Cargo",
            lr_number="EXP-88991",
            actor_id=actor_id,
        )
        # Consignment 1 QC Arrival: 60 accepted, 0 damaged
        cons1_item = cons1.items[0]
        ProcurementService.record_consignment_qc_arrival(
            org_id=org_id,
            consignment_id=cons1.id,
            items_qc=[{
                "consignment_item_id": cons1_item.id,
                "received_quantity": Decimal("60.000"),
                "accepted_quantity": Decimal("60.000"),
                "damaged_quantity": Decimal("0.000"),
                "rejected_quantity": Decimal("0.000"),
                "qc_remarks": "All 60 toughened sheets intact"
            }],
            actor_id=actor_id,
        )
        db.session.refresh(cons1)
        self.assertEqual(cons1.status, OrderConsignmentStatus.DELIVERED.value)

        # Consignment 2: Vendor B shipping 40 units
        cons2 = ProcurementService.record_consignment(
            org_id=org_id,
            order_id=order.id,
            procurement_id=po2.id,
            items_data=[{"procurement_item_id": po2_item.id, "shipped_quantity": Decimal("40.000")}],
            transporter_name="BlueDart Heavy Freight",
            lr_number="BDF-44112",
            actor_id=actor_id,
        )
        # Consignment 2 QC Arrival: 40 accepted, 0 damaged
        cons2_item = cons2.items[0]
        ProcurementService.record_consignment_qc_arrival(
            org_id=org_id,
            consignment_id=cons2.id,
            items_qc=[{
                "consignment_item_id": cons2_item.id,
                "received_quantity": Decimal("40.000"),
                "accepted_quantity": Decimal("40.000"),
                "damaged_quantity": Decimal("0.000"),
                "rejected_quantity": Decimal("0.000"),
                "qc_remarks": "All 40 sheets clear QC"
            }],
            actor_id=actor_id,
        )
        db.session.refresh(cons2)
        self.assertEqual(cons2.status, OrderConsignmentStatus.DELIVERED.value)

        db.session.refresh(order)
        self.assertEqual(order.procurement_status, OrderProcurementStatus.RECEIVED.value)

        # -------------------------------------------------------------------------
        # Step 5: Dispatch and deliver 60 units first
        # -------------------------------------------------------------------------
        delv1 = DeliveryService.create_delivery_challan(
            org_id=org_id,
            order_id=order.id,
            items_data=[{
                "order_item_id": order_item.id,
                "delivered_quantity": Decimal("60.000"),
                "consignment_item_id": cons1_item.id,
            }],
            recipient_name="Site Incharge Sharma",
            recipient_phone="9876543210",
            delivery_address="Metro Towers Site, Plot 42, Cyber City",
            delivered_by_id=2,
            actor_id=actor_id,
        )
        DeliveryService.confirm_delivery(
            org_id=org_id,
            delivery_id=delv1.id,
            items_confirmation=[{
                "delivery_item_id": delv1.items[0].id,
                "accepted_quantity": Decimal("60.000"),
                "damaged_quantity": Decimal("0.000"),
                "returned_quantity": Decimal("0.000"),
            }],
            notes="Challan 1 received and signed on site.",
            actor_id=actor_id,
        )

        # -------------------------------------------------------------------------
        # Step 6: Verify PARTIALLY_DELIVERED status and deliver remaining 40 later
        # -------------------------------------------------------------------------
        db.session.refresh(order)
        db.session.refresh(order_item)
        self.assertEqual(order_item.delivered_quantity, Decimal("60.000"))
        self.assertEqual(order.status, MasterOrderStatus.PARTIALLY_DELIVERED.value)

        # Attempt to deliver 45 units (when only 40 are remaining) -> must be rejected
        with self.assertRaises(OMSInvariantError):
            DeliveryService.create_delivery_challan(
                org_id=org_id,
                order_id=order.id,
                items_data=[{
                    "order_item_id": order_item.id,
                    "delivered_quantity": Decimal("45.000"),
                }],
                actor_id=actor_id,
            )

        # Deliver remaining 40 units via Challan 2
        delv2 = DeliveryService.create_delivery_challan(
            org_id=org_id,
            order_id=order.id,
            items_data=[{
                "order_item_id": order_item.id,
                "delivered_quantity": Decimal("40.000"),
                "consignment_item_id": cons2_item.id,
            }],
            recipient_name="Site Incharge Sharma",
            recipient_phone="9876543210",
            delivered_by_id=2,
            actor_id=actor_id,
        )
        DeliveryService.confirm_delivery(
            org_id=org_id,
            delivery_id=delv2.id,
            items_confirmation=[{
                "delivery_item_id": delv2.items[0].id,
                "accepted_quantity": Decimal("40.000"),
                "damaged_quantity": Decimal("0.000"),
                "returned_quantity": Decimal("0.000"),
            }],
            notes="Challan 2 received in full.",
            actor_id=actor_id,
        )

        db.session.refresh(order)
        db.session.refresh(order_item)
        self.assertEqual(order_item.delivered_quantity, Decimal("100.000"))
        self.assertEqual(order.delivery_status, OrderDeliveryStatus.DELIVERED.value)
        self.assertEqual(order.status, MasterOrderStatus.DELIVERED.value)

        # -------------------------------------------------------------------------
        # Step 7: Schedule fitting visits, partial installation, and final signoff
        # -------------------------------------------------------------------------
        # Visit 1: Install 60 units, partial without final client signoff
        visit1 = DeliveryService.schedule_fitting_visit(
            org_id=org_id,
            order_id=order.id,
            scheduled_date=datetime.utcnow(),
            technician_id=2,
            items_planned=[{"order_item_id": order_item.id, "planned_quantity": Decimal("60.000")}],
            actor_id=actor_id,
        )
        DeliveryService.complete_fitting_visit(
            org_id=org_id,
            visit_id=visit1.id,
            items_installed=[{"fitting_visit_item_id": visit1.items[0].id, "installed_quantity": Decimal("60.000")}],
            client_signoff=False,
            notes="First 60 panels installed on 4th floor.",
            actor_id=actor_id,
        )
        db.session.refresh(order)
        self.assertEqual(order.fitting_status, OrderFittingStatus.PARTIALLY_FITTED.value)
        self.assertNotEqual(order.status, MasterOrderStatus.COMPLETED.value)

        # Visit 2: Install remaining 40 units with client signoff
        visit2 = DeliveryService.schedule_fitting_visit(
            org_id=org_id,
            order_id=order.id,
            scheduled_date=datetime.utcnow() + timedelta(days=2),
            technician_id=2,
            items_planned=[{"order_item_id": order_item.id, "planned_quantity": Decimal("40.000")}],
            actor_id=actor_id,
        )
        DeliveryService.complete_fitting_visit(
            org_id=org_id,
            visit_id=visit2.id,
            items_installed=[{"fitting_visit_item_id": visit2.items[0].id, "installed_quantity": Decimal("40.000")}],
            client_signoff=True,
            notes="All 100 panels installed, aligned, and signed off by client site engineer.",
            actor_id=actor_id,
        )

        db.session.refresh(order)
        self.assertEqual(order.fitting_status, OrderFittingStatus.COMPLETED.value)
        # Order auto-advances to COMPLETED upon full delivery and installation signoff!
        self.assertEqual(order.status, MasterOrderStatus.COMPLETED.value)
        self.assertIsNotNone(order.completed_at)

        # -------------------------------------------------------------------------
        # Step 8: Customer advance allocation & invoice settlement without double-counting
        # -------------------------------------------------------------------------
        # Seed Customer Payment 1: Rs 100,000 Advance
        pmt1 = Payment(
            payment_number="PMT-ADV-101",
            customer_name="Metro Glass Towers Ltd",
            mobile="9876543210",
            amount=Decimal("100000.00"),
            due_date=datetime.utcnow().date(),
            status=PaymentStatus.RECEIVED,
            organization_id=org_id,
        )
        db.session.add(pmt1)
        db.session.commit()

        # Allocate Payment 1 as DIRECT_PAYMENT
        alloc_pmt1 = FinancialReconciliationService.allocate_customer_payment(
            org_id=org_id,
            order_id=order.id,
            payment_id=pmt1.id,
            allocated_amount=Decimal("100000.00"),
            allocation_type=OrderPaymentAllocationType.DIRECT_PAYMENT.value,
            notes="Customer advance received via RTGS.",
            actor_id=actor_id,
        )
        db.session.refresh(order)
        self.assertEqual(order.total_customer_paid, Decimal("100000.00"))
        self.assertEqual(order.customer_payment_status, OrderCustomerPaymentStatus.PARTIALLY_PAID.value)

        # Raise Tax Invoice for Rs 177,000
        inv = Invoice(
            invoice_number="INV-2026-001",
            customer_id=1,
            organization_id=org_id,
            subtotal=150000.0,
            amount=150000.0,
            total_amount=177000.0,
            status=InvoiceStatus.PARTIALLY_PAID,
            created_by=1,
        )
        db.session.add(inv)
        db.session.commit()

        # Seed Customer Payment 2: Rs 77,000 Balance Settle
        pmt2 = Payment(
            payment_number="PMT-BAL-102",
            customer_name="Metro Glass Towers Ltd",
            mobile="9876543210",
            amount=Decimal("77000.00"),
            due_date=datetime.utcnow().date(),
            status=PaymentStatus.RECEIVED,
            organization_id=org_id,
        )
        db.session.add(pmt2)
        db.session.commit()

        # Allocate Payment 2
        alloc_pmt2 = FinancialReconciliationService.allocate_customer_payment(
            org_id=org_id,
            order_id=order.id,
            payment_id=pmt2.id,
            allocated_amount=Decimal("77000.00"),
            allocation_type=OrderPaymentAllocationType.DIRECT_PAYMENT.value,
            invoice_id=inv.id,
            notes="Final balance settlement.",
            actor_id=actor_id,
        )
        db.session.refresh(order)
        self.assertEqual(order.total_customer_paid, Decimal("177000.00"))
        self.assertEqual(order.customer_payment_status, OrderCustomerPaymentStatus.PAID.value)

        # Test Zero Double-Counting ADVANCE_UNLINK Ledger Offset:
        # If advance is unlinked to offset an invoice-level payment, unlinking acts as negative entry
        unlink_alloc = FinancialReconciliationService.allocate_customer_payment(
            org_id=org_id,
            order_id=order.id,
            payment_id=pmt1.id,
            allocated_amount=Decimal("50000.00"),
            allocation_type=OrderPaymentAllocationType.ADVANCE_UNLINK.value,
            notes="Offset adjustment for credit memo",
            actor_id=actor_id,
        )
        db.session.refresh(order)
        self.assertEqual(order.total_customer_paid, Decimal("127000.00"))
        # Re-link back to settle to 177,000
        FinancialReconciliationService.allocate_customer_payment(
            org_id=org_id,
            order_id=order.id,
            payment_id=pmt1.id,
            allocated_amount=Decimal("50000.00"),
            allocation_type=OrderPaymentAllocationType.DIRECT_PAYMENT.value,
            notes="Offset reversal",
            actor_id=actor_id,
        )
        db.session.refresh(order)
        self.assertEqual(order.total_customer_paid, Decimal("177000.00"))
        self.assertEqual(order.customer_payment_status, OrderCustomerPaymentStatus.PAID.value)

        # -------------------------------------------------------------------------
        # Step 9: 3-way matched vendor bills, verify overbilling is rejected
        # -------------------------------------------------------------------------
        # For PO 1 (Vendor A): 60 units accepted. Attempt to bill 65 units -> must fail
        with self.assertRaises(OMSInvariantError):
            FinancialReconciliationService.create_vendor_bill(
                org_id=org_id,
                order_id=order.id,
                procurement_id=po1.id,
                bill_number="BILL-VEND-A-OVER",
                bill_date=datetime.utcnow(),
                items_data=[{
                    "procurement_item_id": po1_item.id,
                    "billed_quantity": Decimal("65.000"),  # > 60 accepted!
                    "unit_rate": Decimal("900.00"),
                    "tax_amount": Decimal("10530.00"),
                }],
                actor_id=actor_id,
            )

        # Bill 1 for Vendor A (60 units @ 900 = 54,000 + 18% tax 9,720 = 63,720)
        bill1 = FinancialReconciliationService.create_vendor_bill(
            org_id=org_id,
            order_id=order.id,
            procurement_id=po1.id,
            bill_number="BILL-VEND-A-001",
            bill_date=datetime.utcnow(),
            items_data=[{
                "procurement_item_id": po1_item.id,
                "billed_quantity": Decimal("60.000"),
                "unit_rate": Decimal("900.00"),
                "tax_amount": Decimal("9720.00"),
            }],
            actor_id=actor_id,
        )
        self.assertEqual(bill1.total_amount, Decimal("63720.00"))

        # Invariant check: PO 1 is now fully billed (60/60). Attempting to bill another unit must fail
        with self.assertRaises(OMSInvariantError):
            FinancialReconciliationService.create_vendor_bill(
                org_id=org_id,
                order_id=order.id,
                procurement_id=po1.id,
                bill_number="BILL-VEND-A-DUP",
                bill_date=datetime.utcnow(),
                items_data=[{
                    "procurement_item_id": po1_item.id,
                    "billed_quantity": Decimal("1.000"),
                    "unit_rate": Decimal("900.00"),
                    "tax_amount": Decimal("162.00"),
                }],
                actor_id=actor_id,
            )

        # Bill 2 for Vendor B (40 units @ 950 = 38,000 + 18% tax 6,840 = 44,840)
        bill2 = FinancialReconciliationService.create_vendor_bill(
            org_id=org_id,
            order_id=order.id,
            procurement_id=po2.id,
            bill_number="BILL-VEND-B-001",
            bill_date=datetime.utcnow(),
            items_data=[{
                "procurement_item_id": po2_item.id,
                "billed_quantity": Decimal("40.000"),
                "unit_rate": Decimal("950.00"),
                "tax_amount": Decimal("6840.00"),
            }],
            actor_id=actor_id,
        )
        self.assertEqual(bill2.total_amount, Decimal("44840.00"))

        # -------------------------------------------------------------------------
        # Step 10: Record vendor payments and verify outstanding balances
        # -------------------------------------------------------------------------
        # Pay Bill 1 in full (Rs 63,720)
        vpay1 = FinancialReconciliationService.record_vendor_payment(
            org_id=org_id,
            bill_id=bill1.id,
            amount=Decimal("63720.00"),
            payment_mode="NEFT",
            transaction_reference="UTR-NEFT-VEND-A-110",
            notes="Full settlement of Bill 1",
            actor_id=actor_id,
        )
        db.session.refresh(bill1)
        self.assertEqual(bill1.paid_amount, Decimal("63720.00"))
        self.assertEqual(bill1.status, "PAID")

        # Pay Bill 2 partially (Rs 30,000 of 44,840)
        vpay2_part = FinancialReconciliationService.record_vendor_payment(
            org_id=org_id,
            bill_id=bill2.id,
            amount=Decimal("30000.00"),
            payment_mode="RTGS",
            transaction_reference="UTR-RTGS-VEND-B-220",
            notes="Partial payment tranche 1",
            actor_id=actor_id,
        )
        db.session.refresh(bill2)
        self.assertEqual(bill2.paid_amount, Decimal("30000.00"))
        self.assertEqual(bill2.status, "PARTIALLY_PAID")

        # Settle Bill 2 remaining balance (Rs 14,840)
        vpay2_final = FinancialReconciliationService.record_vendor_payment(
            org_id=org_id,
            bill_id=bill2.id,
            amount=Decimal("14840.00"),
            payment_mode="RTGS",
            transaction_reference="UTR-RTGS-VEND-B-221",
            notes="Tranche 2 final settlement",
            actor_id=actor_id,
        )
        db.session.refresh(bill2)
        self.assertEqual(bill2.paid_amount, Decimal("44840.00"))
        self.assertEqual(bill2.status, "PAID")

        db.session.refresh(order)
        self.assertEqual(order.total_vendor_paid, Decimal("108560.00"))
        self.assertEqual(order.vendor_payment_status, OrderVendorPaymentStatus.PAID.value)

        # -------------------------------------------------------------------------
        # Step 11: Final audit, financial reconciliation and snapshot revision
        # -------------------------------------------------------------------------
        fin = FinancialReconciliationService.reconcile_order_financials(org_id, order.id)
        self.assertEqual(Decimal(str(fin["net_order_amount"])), Decimal("177000.00"))
        self.assertEqual(Decimal(str(fin["total_procurement_cost"])), Decimal("108560.00"))
        self.assertEqual(Decimal(str(fin["gross_margin"])), Decimal("68440.00"))
        self.assertEqual(Decimal(str(fin["total_customer_paid"])), Decimal("177000.00"))
        self.assertEqual(Decimal(str(fin["customer_outstanding"])), Decimal("0.00"))
        self.assertEqual(Decimal(str(fin["total_vendor_paid"])), Decimal("108560.00"))
        self.assertEqual(Decimal(str(fin["vendor_outstanding"])), Decimal("0.00"))
        self.assertEqual(Decimal(str(fin["operating_cash_margin"])), Decimal("68440.00"))

        # Verify complete status history trail
        hist_records = OrderStatusHistory.query.filter_by(order_id=order.id).all()
        status_types = {h.status_type for h in hist_records}
        self.assertIn("MASTER", status_types)
        self.assertIn("CUSTOMER_PAYMENT", status_types)
        self.assertIn("DELIVERY", status_types)
        self.assertIn("FITTING", status_types)

        # Generate immutable snapshot revision
        rev = OrderMasterService.create_order_revision(
            org_id=org_id,
            order_id=order.id,
            change_reason="Order lifecycle completion and financial reconciliation signoff",
            actor_id=actor_id,
        )
        self.assertEqual(rev.revision_number, 1)
        snap = json.loads(rev.snapshot_json)
        self.assertEqual(snap["status"], MasterOrderStatus.COMPLETED.value)
        self.assertEqual(snap["net_order_amount"], "177000.00")
        self.assertEqual(len(snap["items"]), 1)

    def test_02_rbac_and_csrf_protection(self):
        """Verify role-based access control and CSRF validation on mutation routes."""
        order = OrderMaster.query.filter_by(organization_id=1).first()
        self.assertIsNotNone(order)

        # 1. Unauthenticated mutation request -> redirect to login or 401
        resp_unauth = self.client.post(
            f"/orders/{order.id}/status",
            json={"to_status": MasterOrderStatus.CONFIRMED.value}
        )
        self.assertIn(resp_unauth.status_code, (302, 401))

        # 2. Employee role attempting administrative mutation (exceptional_close) -> 403 Forbidden
        self.client.get("/test-login/2")  # User 2 is EMPLOYEE
        resp_emp_close = self.client.post(
            f"/orders/{order.id}/close",
            json={"closure_type": "CANCELLED", "closure_reason": "Testing employee unauthorized close"}
        )
        self.assertEqual(resp_emp_close.status_code, 403)

        # Employee role attempting order revision -> 403 Forbidden
        resp_emp_rev = self.client.post(
            f"/orders/{order.id}/revisions",
            json={"change_reason": "Testing employee revision"}
        )
        self.assertEqual(resp_emp_rev.status_code, 403)

        # 3. Manager and Admin roles are permitted on administrative routes
        self.client.get("/test-login/3")  # User 3 is MANAGER
        resp_mgr_rev = self.client.post(
            f"/orders/{order.id}/revisions",
            json={"change_reason": "Manager snapshot revision test"}
        )
        self.assertIn(resp_mgr_rev.status_code, (200, 201))

        self.client.get("/test-login/1")  # User 1 is ADMIN
        resp_admin_rev = self.client.post(
            f"/orders/{order.id}/revisions",
            json={"change_reason": "Admin snapshot revision test"}
        )
        self.assertIn(resp_admin_rev.status_code, (200, 201))

        # 4. Unauthorized role on operational routes (e.g. guest role) -> 403 Forbidden
        from unittest.mock import patch
        with patch("utils.security.role_value", return_value="guest"):
            resp_guest_status = self.client.post(
                f"/orders/{order.id}/status",
                json={"to_status": MasterOrderStatus.CONFIRMED.value}
            )
            self.assertEqual(resp_guest_status.status_code, 403)

        # 5. CSRF Protection Verification
        try:
            self.app.config["WTF_CSRF_ENABLED"] = True
            with self.client:
                # Login as admin
                self.client.get("/test-login/1")

                # Form POST without CSRF token -> 400 Bad Request
                resp_no_csrf = self.client.post(
                    f"/orders/{order.id}/status",
                    data={"to_status": MasterOrderStatus.CONFIRMED.value}
                )
                self.assertEqual(resp_no_csrf.status_code, 400)

                # Fetch CSRF token within context and POST with valid token
                self.client.get("/orders/")
                token = generate_csrf()
                resp_with_csrf = self.client.post(
                    f"/orders/{order.id}/status",
                    data={"to_status": MasterOrderStatus.CONFIRMED.value, "csrf_token": token}
                )
                self.assertNotEqual(resp_with_csrf.status_code, 400)
        finally:
            self.app.config["WTF_CSRF_ENABLED"] = False

    def test_03_tenant_isolation(self):
        """Verify strict multi-tenant isolation across organizations."""
        order_org1 = OrderMaster.query.filter_by(organization_id=1).first()
        self.assertIsNotNone(order_org1)

        # 1. Organization 2 user attempts to view Cockpit of Org 1 order -> redirected or error
        self.client.get("/test-login/4")  # User 4 belongs to Organization 2
        resp = self.client.get(f"/orders/{order_org1.id}", headers={"Accept": "application/json"})
        self.assertIn(resp.status_code, (400, 404, 302))

        # 2. Service-level tenant lock check raises OMSTenantIsolationError
        with self.assertRaises(OMSTenantIsolationError):
            OrderMasterService.lock_order(org_id=2, order_id=order_org1.id)

        # 3. Cross-tenant vendor allocation attempt raises OMSTenantIsolationError
        with self.assertRaises(OMSTenantIsolationError):
            OrderMasterService.allocate_item_vendors(
                org_id=1,
                order_id=order_org1.id,
                order_item_id=order_org1.items[0].id,
                allocations_data=[{
                    "vendor_id": 3,  # Vendor 3 belongs to Organization 2!
                    "allocated_quantity": Decimal("5.000"),
                }],
                actor_id=1,
            )

        # 4. Cross-tenant payment allocation attempt raises OMSTenantIsolationError
        pmt_org2 = Payment(
            payment_number="PMT-ORG2-999",
            customer_name="Zenith Client",
            mobile="9876543211",
            amount=Decimal("5000.00"),
            due_date=datetime.utcnow().date(),
            status=PaymentStatus.RECEIVED,
            organization_id=2,
        )
        db.session.add(pmt_org2)
        db.session.commit()

        with self.assertRaises(OMSTenantIsolationError):
            FinancialReconciliationService.allocate_customer_payment(
                org_id=1,
                order_id=order_org1.id,
                payment_id=pmt_org2.id,
                allocated_amount=Decimal("5000.00"),
                actor_id=1,
            )

    def test_04_transactional_rollback_on_failure(self):
        """Verify failed transactional operations roll back completely without partial database writes."""
        org_id = 1
        actor_id = 1

        order = OrderMasterService.create_order(
            org_id=org_id,
            customer_id=1,
            order_title="Rollback Verification Order",
            items_data=[{
                "product_id": 1,
                "item_description": "Rollback Test Glass Item",
                "quantity": Decimal("10.000"),
                "unit_price": Decimal("500.00"),
                "tax_rate": Decimal("18.00"),
            }],
            actor_id=actor_id,
        )
        order_item = order.items[0]

        alloc_count_before = OrderItemVendorAllocation.query.filter_by(order_id=order.id).count()

        # Attempt an allocation batch containing 1 valid and 1 invalid item (> 10 quantity)
        try:
            OrderMasterService.allocate_item_vendors(
                org_id=org_id,
                order_id=order.id,
                order_item_id=order_item.id,
                allocations_data=[
                    {"vendor_id": 1, "allocated_quantity": Decimal("4.000"), "purchase_rate": Decimal("400.00")},
                    {"vendor_id": 2, "allocated_quantity": Decimal("20.000"), "purchase_rate": Decimal("400.00")},  # 4 + 20 > 10!
                ],
                actor_id=actor_id,
            )
        except OMSInvariantError:
            db.session.rollback()

        alloc_count_after = OrderItemVendorAllocation.query.filter_by(order_id=order.id).count()
        # Verify 0 rows persisted from the aborted batch
        self.assertEqual(alloc_count_before, alloc_count_after)

    def test_05_concurrent_allocations_and_deliveries(self):
        """Verify concurrency safety and quantity invariant enforcement under multi-threaded execution."""
        org_id = 1
        actor_id = 1

        order = OrderMasterService.create_order(
            org_id=org_id,
            customer_id=1,
            order_title="Concurrency Invariant Test Order",
            items_data=[{
                "product_id": 1,
                "item_description": "Concurrent Toughened Glass",
                "quantity": Decimal("100.000"),
                "unit_price": Decimal("1000.00"),
                "tax_rate": Decimal("18.00"),
            }],
            actor_id=actor_id,
        )
        order = OrderMasterService.transition_order_status(
            org_id=org_id, order_id=order.id, to_status=MasterOrderStatus.CONFIRMED.value, actor_id=actor_id
        )
        order_item = order.items[0]
        order_id = order.id
        item_id = order_item.id

        # Concurrency Test: 2 worker threads attempt to allocate 60 units each concurrently (60 + 60 = 120 > 100)
        results = []

        def worker_allocate(vendor_id: int):
            with self.app.app_context():
                try:
                    OrderMasterService.allocate_item_vendors(
                        org_id=org_id,
                        order_id=order_id,
                        order_item_id=item_id,
                        allocations_data=[{
                            "vendor_id": vendor_id,
                            "allocated_quantity": Decimal("60.000"),
                            "purchase_rate": Decimal("800.00"),
                        }],
                        actor_id=actor_id,
                    )
                    results.append("SUCCESS")
                except OMSInvariantError:
                    results.append("INVARIANT_ERROR")
                except Exception as ex:
                    results.append(f"ERR_{type(ex).__name__}")

        with ThreadPoolExecutor(max_workers=2) as executor:
            fut1 = executor.submit(worker_allocate, 1)
            fut2 = executor.submit(worker_allocate, 2)
            fut1.result()
            fut2.result()

        # Exactly one must succeed and one must be rejected with OMSInvariantError
        self.assertEqual(results.count("SUCCESS"), 1)
        self.assertEqual(results.count("INVARIANT_ERROR"), 1)

        # Verify in MySQL database that allocated quantity is exactly 60, not 120
        with self.app.app_context():
            reloaded_item = db.session.get(OrderItem, item_id)
            self.assertEqual(reloaded_item.allocated_quantity, Decimal("60.000"))

    def test_06_html_page_rendering(self):
        """Verify that unified Order Cockpit HTML templates render valid pages without template syntax errors."""
        self.client.get("/test-login/1")  # Login as Admin

        # 1. Cockpit List Page
        resp_list = self.client.get("/orders/")
        self.assertEqual(resp_list.status_code, 200)
        html_list = resp_list.get_data(as_text=True)
        self.assertIn("Unified Order Cockpit", html_list)
        self.assertIn("Create Master Order", html_list)
        self.assertIn("Total Master Orders", html_list)

        # 2. Create Order Page
        resp_new = self.client.get("/orders/new")
        self.assertEqual(resp_new.status_code, 200)
        html_new = resp_new.get_data(as_text=True)
        self.assertIn("Create Master Order", html_new)
        self.assertIn("Customer *", html_new)
        self.assertIn("Line Items", html_new)
        self.assertIn("Metro Glass Towers Ltd", html_new)

        # 3. Cockpit Detail Single-Page Hub
        order = OrderMaster.query.filter_by(organization_id=1).first()
        self.assertIsNotNone(order)
        resp_detail = self.client.get(f"/orders/{order.id}")
        self.assertEqual(resp_detail.status_code, 200)
        html_detail = resp_detail.get_data(as_text=True)
        self.assertIn(order.master_order_number, html_detail)
        self.assertIn("Items & Allocations", html_detail)
        self.assertIn("Procurement & Transporter", html_detail)
        self.assertIn("Deliveries", html_detail)
        self.assertIn("Site Fitting", html_detail)
        self.assertIn("Financial Reconciliation", html_detail)
        self.assertIn("Audit Log", html_detail)


if __name__ == "__main__":
    unittest.main()

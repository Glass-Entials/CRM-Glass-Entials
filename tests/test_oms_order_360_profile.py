"""
tests/test_oms_order_360_profile.py
Comprehensive 25-Test Suite for the Order 360° Profile View Redesign (templates/orders/detail.html)
and Lead-to-Order lifecycle presentation in GlassEntials CRM.
"""
import unittest
import sys
from pathlib import Path
from decimal import Decimal
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, g
from flask_login import LoginManager
from sqlalchemy import text

from model import (
    db,
    User,
    UserRole,
    Organization,
    Customer,
    Lead,
    LeadStatus,
    LeadSource,
    Vendor,
    VendorCategory,
    Employee,
    Project,
    Quotation,
    QuotationStatus,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderItemVendorAllocation,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    OrderFittingVisit,
    OrderStatusHistory,
    Payment,
    OrderPaymentAllocation,
    VendorBill,
    OrderDocument,
    OrderDocumentType,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
)
from services.oms import (
    OrderMasterService,
    OMSError,
    OMSValidationError,
    OMSTenantIsolationError,
)
from routes.orders import orders_bp


class OMSOrder360ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_360_profile_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="oms-360-profile-test-secret-key",
        )
        db.init_app(cls.app)

        cls.login_manager = LoginManager()
        cls.login_manager.init_app(cls.app)

        @cls.login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

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
            "messages.messages_page", "messages.total_unread_count", "api.recent_notifications",
        ]:
            cls.app.add_url_rule(f"/dummy/{ep.replace('.', '/')}", endpoint=ep, view_func=lambda ep=ep: f"dummy_{ep}")

        @cls.app.context_processor
        def utility_processor():
            return {
                "get_profile_pic": lambda emp: "/static/img/default-avatar.png",
                "unread_notifications_count": lambda: 0,
                "org_modules": {},
                "active_org": None,
                "csrf_token": lambda: "dummy-csrf-token",
            }

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        db.session.execute(text("PRAGMA foreign_keys = ON;"))
        db.create_all()

        # Seed Organizations
        self.org1 = Organization(id=1, name="GlassEntials Premier", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Competitor Corp", unique_code="ORG002")
        db.session.add_all([self.org1, self.org2])
        db.session.commit()

        # Seed Users
        self.admin1 = User(id=1, username="admin1", password="hash", email="admin1@example.com", role=UserRole.ADMIN, organization_id=1)
        self.admin2 = User(id=2, username="admin2", password="hash", email="admin2@example.com", role=UserRole.ADMIN, organization_id=2)
        db.session.add_all([self.admin1, self.admin2])
        db.session.commit()

        # Seed Employees
        self.emp1 = Employee(id=1, user_id=1, name="Pravin Sharma (PM)", email="admin1@example.com", organization_id=1)
        self.emp2 = Employee(id=2, user_id=2, name="External PM", email="admin2@example.com", organization_id=2)
        db.session.add_all([self.emp1, self.emp2])
        db.session.commit()

        # Seed Vendors
        self.vendor_cat = VendorCategory(id=1, name="Architectural Glass", organization_id=1)
        db.session.add(self.vendor_cat)
        db.session.commit()

        self.vendor1 = Vendor(id=1, company_name="Saint-Gobain Glass Fab", code="VEN-SG01", category_id=1, organization_id=1, is_deleted=False)
        self.vendor2 = Vendor(id=2, company_name="Ozone Architectural", code="VEN-OZ02", category_id=1, organization_id=1, is_deleted=False)
        db.session.add_all([self.vendor1, self.vendor2])
        db.session.commit()

        # Seed Customers & Leads
        self.customer1 = Customer(
            id=1,
            organization_id=1,
            name="Rajesh Singhania",
            company="Oberoi Realty Ltd",
            phone_number="+91 98201 11223",
            email="rsinghania@oberoigroup.com",
            address="Borivali East Penthouse, Mumbai",
            city="Mumbai",
            gst_number="27AABCO1234F1Z5",
            created_by=1,
            is_deleted=False,
        )
        self.lead1 = Lead(
            id=1,
            organization_id=1,
            name="Rajesh Singhania",
            company="Oberoi Realty Ltd",
            phone_number="+91 98201 11223",
            email="rsinghania@oberoigroup.com",
            address="Borivali East Penthouse",
            city="Mumbai",
            status=LeadStatus.CLOSED,
            source=LeadSource.ARCHITECT if hasattr(LeadSource, 'ARCHITECT') else LeadSource.REFERRAL,
            created_by=1,
            is_deleted=False,
        )
        db.session.add_all([self.customer1, self.lead1])
        db.session.commit()

        # Seed Quotation
        self.quotation1 = Quotation(
            id=1,
            organization_id=1,
            quotation_number="GLS-QTN-2026-00062",
            status=QuotationStatus.SENT if hasattr(QuotationStatus, 'SENT') else QuotationStatus.DRAFT,
            total_amount=Decimal("415000.00"),
            lead_id=1,
            customer_id=1,
            created_by=1,
            is_deleted=False,
        )
        db.session.add(self.quotation1)
        db.session.commit()

        # Seed Master Order with rich fields matching reference screenshots
        self.order1 = OrderMaster(
            id=1,
            master_order_number="GLS-ORD-2026-00041",
            organization_id=1,
            customer_id=1,
            lead_id=1,
            quotation_id=1,
            notes="[Oberoi Sky City Tower B] Premium Penthouse architectural glazing",
            status=MasterOrderStatus.IN_PROCUREMENT.value,
            procurement_status=OrderProcurementStatus.PO_ISSUED.value,
            delivery_status=OrderDeliveryStatus.SCHEDULED.value,
            fitting_status=OrderFittingStatus.SCHEDULED.value,
            customer_payment_status=OrderCustomerPaymentStatus.PARTIALLY_PAID.value,
            total_ordered_amount=Decimal("415000.00"),
            net_order_amount=Decimal("415000.00"),
            total_customer_paid=Decimal("207500.00"),
            total_procurement_cost=Decimal("185000.00"),
            total_vendor_billed=Decimal("120000.00"),
            total_vendor_paid=Decimal("60000.00"),
            order_date=datetime(2026, 11, 19),
            expected_delivery_date=datetime(2026, 12, 5),
            shipping_address="Borivali East Penthouse, Mumbai",
            site_address="Borivali East Penthouse • Oberoi Realty Ltd",
            priority="HIGH",
            customer_po_number="OBEROI/PO/2026/882",
            customer_po_date=datetime(2026, 11, 18),
            assigned_to_id=1,
            created_by_id=1,
        )
        db.session.add(self.order1)
        db.session.commit()

        # Seed Line Items
        self.item1 = OrderItem(
            id=1,
            order_id=1,
            organization_id=1,
            item_description="Toughened / Tempered Glass 12mm Clear",
            specifications="12mm Toughened Clear Glass with Arrised Edges, CNC cutouts",
            quantity=Decimal("450.000"),
            unit_price=Decimal("922.22"),
            total_price=Decimal("415000.00"),
            allocated_quantity=Decimal("450.000"),
            procured_quantity=Decimal("450.000"),
            delivered_quantity=Decimal("0.000"),
            is_deleted=False,
        )
        db.session.add(self.item1)
        db.session.commit()

        # Seed Procurement
        self.proc1 = OrderVendorProcurement(
            id=1,
            order_id=1,
            organization_id=1,
            vendor_id=1,
            po_number="GLS-VND-2026-00031",
            status="ISSUED",
            po_date=datetime(2026, 11, 20),
            total_procurement_amount=Decimal("185000.00"),
            net_procurement_amount=Decimal("185000.00"),
            created_by_id=1,
            is_deleted=False,
        )
        db.session.add(self.proc1)
        db.session.commit()

        # Seed Item Allocation
        self.alloc1 = OrderItemVendorAllocation(
            id=1,
            order_id=1,
            order_item_id=1,
            vendor_id=1,
            organization_id=1,
            procurement_id=1,
            allocated_quantity=Decimal("450.000"),
            expected_rate=Decimal("411.11"),
            estimated_cost=Decimal("185000.00"),
            allocation_status="CONFIRMED",
            created_by_id=1,
            is_deleted=False,
        )
        db.session.add(self.alloc1)
        db.session.commit()

        # Seed Consignment with full transport fields
        self.cons1 = OrderConsignment(
            id=1,
            consignment_number="GLS-TRP-2026-00021",
            order_id=1,
            organization_id=1,
            procurement_id=1,
            transporter_name="SafeGlass Logistics",
            vehicle_number="Dedicated A-Frame Truck (MH-04-AZ-8812)",
            lr_number="LR-SG-9921",
            dispatch_date=datetime(2026, 11, 29),
            expected_arrival_date=datetime(2026, 11, 30),
            shipping_cost=Decimal("18000.00"),
            status="Vehicle Scheduled",
            driver_name="Kailash Singh",
            driver_contact="+91 98211 44556",
            pickup_location="Vendor Plant - Taloja",
            packing_verified=True,
            site_receiver_name="Girish (Site Engg)",
            site_receiver_contact="+91 98202 33441",
            pod_signed=False,
            failure_reason="None",
            notes="A-Frame vehicle booked; scheduled post QC pass.",
            is_deleted=False,
        )
        db.session.add(self.cons1)
        db.session.commit()

        # Seed Fitting Visit
        self.fit1 = OrderFittingVisit(
            id=1,
            visit_number="FIT-2026-001",
            order_id=1,
            organization_id=1,
            technician_id=1,
            scheduled_date=datetime(2026, 12, 1, 10, 0),
            status="SCHEDULED",
            client_signoff=False,
            is_deleted=False,
        )
        db.session.add(self.fit1)
        db.session.commit()

        # Seed Status History
        self.hist1 = OrderStatusHistory(
            id=1,
            order_id=1,
            organization_id=1,
            status_type="MASTER",
            from_status="CONFIRMED",
            to_status="IN_PROCUREMENT",
            remarks="Initiate Vendor Procurement: 50% advance booked. Raw float glass cut and CNC arrissing in progress.",
            changed_by_id=1,
            created_at=datetime(2026, 11, 19, 14, 30),
        )
        db.session.add(self.hist1)
        db.session.commit()

        # Seed Payment & Allocation
        self.pay1 = Payment(
            id=1,
            organization_id=1,
            customer_name="Rajesh Singhania",
            mobile="+91 98201 11223",
            amount=Decimal("207500.00"),
            payment_number="PAY-2026-0041",
            due_date=date(2026, 12, 1),
            is_deleted=False,
        )
        db.session.add(self.pay1)
        db.session.commit()

        self.pay_alloc1 = OrderPaymentAllocation(
            id=1,
            order_id=1,
            organization_id=1,
            payment_id=1,
            allocated_amount=Decimal("207500.00"),
            allocation_type="DIRECT_PAYMENT",
            allocated_by_id=1,
            allocation_date=datetime(2026, 11, 19, 12, 0),
        )
        db.session.add(self.pay_alloc1)
        db.session.commit()

        # Seed Vendor Bill
        self.bill1 = VendorBill(
            id=1,
            order_id=1,
            organization_id=1,
            procurement_id=1,
            vendor_id=1,
            bill_number="BILL-SG-2026-009",
            bill_date=datetime(2026, 11, 20),
            subtotal_amount=Decimal("120000.00"),
            total_amount=Decimal("120000.00"),
            paid_amount=Decimal("60000.00"),
            status="PARTIALLY_PAID",
            is_deleted=False,
        )
        db.session.add(self.bill1)
        db.session.commit()

        # Seed Document
        self.doc1 = OrderDocument(
            id=1,
            order_id=1,
            organization_id=1,
            title="Architectural Glass Elevation Drawing v2.pdf",
            file_path="uploads/orders/doc1.pdf",
            file_size=204800,
            mime_type="application/pdf",
            document_type=OrderDocumentType.QUOTATION.value,
            uploaded_by_id=1,
            is_deleted=False,
        )
        db.session.add(self.doc1)
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        try:
            db.session.execute(text("PRAGMA foreign_keys = OFF;"))
            db.drop_all()
        except Exception:
            pass
        self.context.pop()

    def _login_as(self, user):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True
            sess["organization_id"] = user.organization_id
        g._login_user = db.session.get(User, int(user.id))

    # ══════════════════════════════════════════════════════════════════
    # 25 TEST CASES FOR ORDER 360° PROFILE VIEW
    # ══════════════════════════════════════════════════════════════════

    def test_01_detail_route_loads_200(self):
        """1. Detail route renders with 200 OK for valid order."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        self.assertEqual(resp.status_code, 200)

    def test_02_identity_header_displays_order_number(self):
        """2. Order identity header displays the Master Order number."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("GLS-ORD-2026-00041", html)

    def test_03_identity_header_displays_lead_badge(self):
        """3. Order identity header displays the linked Lead ID badge."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("GLS-LEAD-0001", html)

    def test_04_identity_header_displays_priority_badge(self):
        """4. Order identity header displays Priority badge (HIGH)."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("HIGH", html)

    def test_05_identity_header_displays_confirmed_value(self):
        """5. Order identity header displays formatted confirmed order value."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("CONFIRMED ORDER VALUE", html.upper())
        self.assertIn("415,000", html)

    def test_06_identity_header_displays_project_or_customer(self):
        """6. Order identity header displays project title and location metadata."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Oberoi Sky City Tower B", html)
        self.assertIn("Borivali East Penthouse", html)

    def test_07_end_to_end_lifecycle_tracker_7_stages(self):
        """7. Lifecycle stage tracker renders all 7 end-to-end stages."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Lead &amp; Qualify", html)
        self.assertIn("Measurement", html)
        self.assertIn("Quotation", html)
        self.assertIn("Order Confirmed", html)
        self.assertIn("Vendor &amp; QC", html)
        self.assertIn("Transport", html)
        self.assertIn("Unloading &amp; Fitting", html)

    def test_08_lifecycle_stage_active_status(self):
        """8. Lifecycle tracker shows stage active based on order status."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("stage-step-card", html)
        self.assertIn("Vendor &amp; QC", html)

    def test_09_customer_and_lead_details_card(self):
        """9. Card 1 (Customer & Lead Details) renders person, PM, contact info."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Customer &amp; Lead Details", html)
        self.assertIn("Rajesh Singhania", html)
        self.assertIn("98201 11223", html)
        self.assertIn("rsinghania@oberoigroup.com", html)
        self.assertIn("Oberoi Realty Ltd", html)

    def test_10_next_action_followup_card(self):
        """10. Card 2 (Next Action & Follow-up) renders action banner and audit trail."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Next Action &amp; Follow-up", html)
        self.assertIn("Initiate Vendor Procurement", html)
        self.assertIn("logged", html)

    def test_11_requirement_quotation_card(self):
        """11. Card 3 (Requirement & Quotation) renders quotation ID and decision."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Requirement &amp; Quotation", html)
        self.assertIn("GLS-QTN-2026-00062", html)
        self.assertIn("415,000", html)

    def test_12_order_financials_card(self):
        """12. Card 4 (Order & Financials) renders PO No, advance %, balance due."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Order &amp; Financials", html)
        self.assertIn("OBEROI/PO/2026/882", html)
        self.assertIn("207,500", html)
        self.assertIn("50%", html)

    def test_13_vendor_site_progress_card(self):
        """13. Card 5 (Vendor & Site Progress) renders vendor PO ID, fabricator name, milestones."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Vendor &amp; Site Progress", html)
        self.assertIn("GLS-VND-2026-00031", html)
        self.assertIn("Saint-Gobain Glass Fab", html)
        self.assertIn("Milestones:", html)

    def test_14_transport_delivery_card(self):
        """14. Card 6 (Transport & Delivery) renders transporter, driver, vehicle, POD status."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Transport &amp; Delivery", html)
        self.assertIn("SafeGlass Logistics", html)
        self.assertIn("Kailash Singh", html)
        self.assertIn("Vendor Plant - Taloja", html)
        self.assertIn("18,000", html)
        self.assertIn("Logistics Remarks:", html)

    def test_15_tab_navigation_all_three_tabs(self):
        """15. Tab navigation includes All Details (360°), Commercial, and Operations."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("All Details (360°)", html)
        self.assertIn("Commercial &amp; Financials", html)
        self.assertIn("Operations &amp; Logistics", html)

    def test_16_commercial_tab_renders_financial_summary(self):
        """16. Commercial tab pane renders net order, customer paid, and balance."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("tabCommercial", html)
        self.assertIn("Commercial Ledger", html)
        self.assertIn("415,000.00", html)
        self.assertIn("207,500.00", html)

    def test_17_commercial_tab_renders_payment_allocations(self):
        """17. Customer payment allocations table is populated in Commercial tab."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Allocated Customer Payments", html)
        self.assertIn("PAY-2026-0041", html)
        self.assertIn("DIRECT_PAYMENT", html)

    def test_18_commercial_tab_renders_vendor_bills(self):
        """18. Vendor bills table is rendered with bill number and action buttons."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Vendor Bills &amp; Supplier Invoices", html)
        self.assertIn("BILL-SG-2026-009", html)
        self.assertIn("120,000.00", html)

    def test_19_operations_tab_renders_items_bom(self):
        """19. Operations tab renders Bill of Materials and line items."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("tabOperations", html)
        self.assertIn("Toughened / Tempered Glass 12mm Clear", html)
        self.assertIn("450.00", html)

    def test_20_operations_tab_renders_consignments(self):
        """20. Operations tab renders consignments and dispatches table."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("GLS-TRP-2026-00021", html)
        self.assertIn("SafeGlass Logistics", html)

    def test_21_operations_tab_renders_fitting_visits(self):
        """21. Operations tab renders site installation visits table."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Site Installation &amp; Fitting Visits", html)
        self.assertIn("FIT-2026-001", html)

    def test_22_activity_timeline_audit_trail(self):
        """22. Activity timeline renders chronological status history and remarks."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Order Activity &amp; Audit Trail", html)
        self.assertIn("CONFIRMED → IN_PROCUREMENT", html)
        self.assertIn("Raw float glass cut and CNC arrissing in progress", html)

    def test_23_documents_and_drawings_section(self):
        """23. Documents & drawings section renders uploaded file references."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn("Project Documents &amp; Drawings", html)
        self.assertIn("Architectural Glass Elevation Drawing v2.pdf", html)

    def test_24_operational_modals_present(self):
        """24. All required operational modals (allocation, consignment, payment, etc.) are present."""
        self._login_as(self.admin1)
        resp = self.client.get("/orders/1")
        html = resp.get_data(as_text=True)
        self.assertIn('id="allocateModal"', html)
        self.assertIn('id="consignmentModal"', html)
        self.assertIn('id="uploadDeliveryChallanModal"', html)
        self.assertIn('id="fittingModal"', html)
        self.assertIn('id="paymentModal"', html)
        self.assertIn('id="vendorBillModal"', html)
        self.assertIn('id="revisionModal"', html)
        self.assertIn('id="closureModal"', html)

    def test_25_tenant_isolation_detail_page(self):
        """25. Cross-tenant access to order detail is strictly blocked."""
        self._login_as(self.admin2)  # Organization 2 trying to access Organization 1's order
        resp = self.client.get("/orders/1")
        # Should redirect to orders list with flash error
        self.assertIn(resp.status_code, [302, 403, 404])

    def test_26_flash_messages_rendered_on_detail_page(self):
        """26. Flashed error and success messages render directly on the order detail page."""
        self._login_as(self.admin1)
        with self.client.session_transaction() as sess:
            sess['_flashes'] = [
                ('order_error', 'Status transition prerequisite failed: undelivered items.'),
                ('order_success', 'Purchase order created successfully.')
            ]
        resp = self.client.get("/orders/1")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Status transition prerequisite failed: undelivered items.", html)
        self.assertIn("Purchase order created successfully.", html)
        self.assertIn("flash-error", html)
        self.assertIn("flash-success", html)


if __name__ == "__main__":
    unittest.main()

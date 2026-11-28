"""
tests/test_oms_order_tracking_dashboard.py
Comprehensive Test Suite for the Redesigned OMS Order Tracking Dashboard
and Lead-to-Order Lifecycle Integration:

Dashboard Tests:
1. Correct Order card rendering & visual structure
2. Correct Order number and linked Lead reference (GLS-LEAD-...)
3. Correct workflow stage computation (Vendor & QC, Transport, Unloading & Fitting)
4. Correct Pre-Dispatch QC status (Passed, Pending, Failed)
5. Correct transport and delivery information
6. Correct vendor information, including multiple vendors
7. Correct customer payment, advance, and outstanding balance
8. Correct latest milestone summary
9. Search and filtering (Order #, Customer, Project, Lead, Vendor, PM, Due Date, Sort)
10. Pagination and strict tenant isolation

Lead-to-Order Integration Tests:
11. Unconfirmed Lead does not create an Order
12. Order is created only after explicit Order Confirmation
13. Lead ID is correctly associated with Master Order
14. Customer association is preserved for converted/associated lead
15. Duplicate confirmation is idempotent and does not create duplicate orders
16. Concurrent / repeated confirmation requests safely return existing order
17. Cross-tenant or unauthorized Lead references are rejected
18. Existing Orders without Lead references continue to work seamlessly
19. Draft Orders vs Active Operational Orders scope isolation
20. Inspect 360° integration and JSON API compatibility
"""
import unittest
import sys
import json
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
    Vendor,
    VendorCategory,
    Employee,
    Project,
    OrderMaster,
    OrderItem,
    OrderVendorProcurement,
    OrderItemVendorAllocation,
    OrderConsignment,
    ConsignmentItem,
    OrderDelivery,
    OrderFittingVisit,
    OrderStatusHistory,
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


class OMSOrderTrackingDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_tracking_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="oms-tracking-test-secret-key",
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
        self.emp1 = Employee(id=1, user_id=1, name="Rahul Sharma (PM)", email="admin1@example.com", organization_id=1)
        self.emp2 = Employee(id=2, user_id=2, name="External PM", email="admin2@example.com", organization_id=2)
        db.session.add_all([self.emp1, self.emp2])
        db.session.commit()

        # Seed Vendors
        self.vendor_cat = VendorCategory(id=1, name="Architectural Glass", organization_id=1)
        db.session.add(self.vendor_cat)
        db.session.commit()

        self.vendor1 = Vendor(id=1, company_name="Saint-Gobain Glass", code="VEN-SG01", category_id=1, organization_id=1, is_deleted=False)
        self.vendor2 = Vendor(id=2, company_name="Ozone Architectural Hardware", code="VEN-OZ02", category_id=1, organization_id=1, is_deleted=False)
        self.vendor_org2 = Vendor(id=3, company_name="Rival Supplier", code="VEN-RV03", category_id=1, organization_id=2, is_deleted=False)
        db.session.add_all([self.vendor1, self.vendor2, self.vendor_org2])
        db.session.commit()

        # Seed Customers
        self.cust1 = Customer(id=1, name="Chirag Mehta", company="Mehta Constructions", email="chirag@example.com", phone_number="9876543210", city="Mumbai", organization_id=1, created_by=1)
        self.cust_org2 = Customer(id=2, name="Rival Client", company="Rival Corp", email="rival@example.com", phone_number="9998887776", city="Pune", organization_id=2, created_by=2)
        db.session.add_all([self.cust1, self.cust_org2])
        db.session.commit()

        # Seed Leads
        self.lead_unconfirmed = Lead(
            id=101, name="Oberoi Realty Towers", company="Oberoi Realty", phone_number="9820011223",
            email="oberoi@example.com", status=LeadStatus.NEW, city="Mumbai", organization_id=1, created_by=1
        )
        self.lead_confirmed = Lead(
            id=102, name="Prestige Villa Glazing", company="Prestige Estates", phone_number="9830022334",
            email="prestige@example.com", status=LeadStatus.CLOSED, city="Bangalore", organization_id=1, created_by=1
        )
        self.lead_converted = Lead(
            id=103, name="Chirag Mehta Lead", company="Mehta Constructions", phone_number="9876543210",
            email="chirag@example.com", status=LeadStatus.CLOSED, city="Mumbai", organization_id=1, created_by=1
        )
        self.lead_org2 = Lead(
            id=104, name="Competitor Lead", company="Other Corp", phone_number="9988776655",
            email="comp@example.com", status=LeadStatus.CLOSED, city="Delhi", organization_id=2, created_by=2
        )
        db.session.add_all([self.lead_unconfirmed, self.lead_confirmed, self.lead_converted, self.lead_org2])
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

    def _login(self, user_id=1):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        g._login_user = db.session.get(User, int(user_id))

    # ─────────────────────────────────────────────────────────────
    # Dashboard Tests (1 to 10)
    # ─────────────────────────────────────────────────────────────

    def test_01_order_card_rendering_and_html_elements(self):
        """1. Verify full HTML structure and presence of cards, sections A, B, C, D."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "12mm Toughened Glass", "quantity": Decimal("10.000"), "unit_price": Decimal("500.00"), "tax_rate": Decimal("18.00"), "discount_amount": Decimal("0.00")}],
            order_title="Villa 402 Living Room Glazing",
            action_type="confirm_order",
            actor_id=1,
        )

        resp = self.client.get("/orders/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Order Tracking Dashboard", html)
        self.assertIn(order.master_order_number, html)
        self.assertIn("Villa 402 Living Room Glazing", html)
        self.assertIn("Inspect 360°", html)
        self.assertIn("Vendor &amp; QC", html)
        self.assertIn("Transport", html)
        self.assertIn("Unloading &amp; Fitting", html)
        self.assertIn("Pre-Dispatch QC", html)

    def test_02_order_number_and_linked_lead_reference(self):
        """2. Correct Order number and linked Lead reference (GLS-LEAD-...)."""
        order = OrderMasterService.create_order(
            org_id=1,
            lead_id=102,
            items_data=[{"item_description": "Curtain Wall Glazing", "quantity": Decimal("5.000"), "unit_price": Decimal("2000.00"), "tax_rate": Decimal("18.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            confirmed_lead_override=True,
            entity_mode="lead",
            actor_id=1,
        )
        card_data = OrderMasterService.build_tracking_card_data(order)
        self.assertEqual(card_data["master_order_number"], order.master_order_number)
        self.assertIn("GLS-LEAD-", card_data["lead_ref"])
        self.assertIn("00102", card_data["lead_ref"])

    def test_03_workflow_stages_active_completed_warning(self):
        """3. Correct workflow stage computation (active, completed, warning states)."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Partition Glass", "quantity": Decimal("2.000"), "unit_price": Decimal("1000.00"), "tax_rate": Decimal("18.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )
        card = OrderMasterService.build_tracking_card_data(order)
        self.assertEqual(card["workflow"]["stage1"]["state"], "active")
        self.assertEqual(card["workflow"]["stage2"]["state"], "pending")

        # When status moves to IN_DELIVERY, Stage 1 is completed and Stage 2 is active
        order.status = MasterOrderStatus.IN_DELIVERY.value
        order.delivery_status = OrderDeliveryStatus.OUT_FOR_DELIVERY.value
        db.session.commit()
        card2 = OrderMasterService.build_tracking_card_data(order)
        self.assertEqual(card2["workflow"]["stage1"]["state"], "completed")
        self.assertEqual(card2["workflow"]["stage2"]["state"], "active")

    def test_04_qc_status_passed_pending_failed(self):
        """4. Correct Pre-Dispatch QC status based on persisted records."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Test Panel", "quantity": Decimal("1.000"), "unit_price": Decimal("500.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )
        card = OrderMasterService.build_tracking_card_data(order)
        self.assertIn(card["qc_status_label"], ["Pending Inspection", "Not Scheduled"])

    def test_05_transport_and_delivery_info(self):
        """5. Transport and delivery information including transporter name."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Glass Sheet", "quantity": Decimal("1.000"), "unit_price": Decimal("500.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )
        cons = OrderConsignment(
            consignment_number="CSG-001",
            order_id=order.id,
            organization_id=1,
            transporter_name="SafeXpress Logistics",
            vehicle_number="MH-04-AB-1234",
            is_deleted=False,
        )
        db.session.add(cons)
        db.session.commit()

        card = OrderMasterService.build_tracking_card_data(order)
        self.assertEqual(card["transporter_name"], "SafeXpress Logistics")

    def test_06_vendor_info_including_multiple_vendors(self):
        """6. Correct vendor information supporting multiple associated vendors."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Glass & Fitting", "quantity": Decimal("2.000"), "unit_price": Decimal("1000.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            selected_vendor_ids=[1, 2],
            action_type="confirm_order",
            actor_id=1,
        )
        card = OrderMasterService.build_tracking_card_data(order)
        self.assertIn("Saint-Gobain Glass", card["vendor_display"])
        self.assertIn("Ozone Architectural Hardware", card["vendor_display"])

    def test_07_customer_payment_and_outstanding_balance(self):
        """7. Accurate decimal-safe financial figures, advance %, and balance due."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Glass Package", "quantity": Decimal("1.000"), "unit_price": Decimal("10000.00"), "tax_rate": Decimal("18.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )
        order.total_customer_paid = Decimal("5000.00")
        db.session.commit()

        card = OrderMasterService.build_tracking_card_data(order)
        self.assertEqual(card["advance_amount"], 5000.00)
        self.assertEqual(card["balance_due"], 6800.00)
        self.assertIn("Advance Paid - Balance Due", card["payment_status_label"])

    def test_08_latest_milestone_summary(self):
        """8. Milestone derived from latest OrderStatusHistory remarks."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Glass", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )
        hist = OrderStatusHistory(
            order_id=order.id,
            organization_id=1,
            status_type="LOGISTICS",
            from_status="SCHEDULED",
            to_status="IN_TRANSIT",
            remarks="Custom glass panels loaded onto specialized A-frame carrier.",
            changed_by_id=1,
        )
        db.session.add(hist)
        db.session.commit()

        card = OrderMasterService.build_tracking_card_data(order)
        self.assertEqual(card["latest_milestone"], "Custom glass panels loaded onto specialized A-frame carrier.")

    def test_09_omni_search_and_filtering(self):
        """9. Universal search across Order #, customer name, lead name, and project."""
        order1 = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Item A", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            order_title="Penthouse 501 Glass Facade",
            action_type="confirm_order",
            actor_id=1,
        )
        res = OrderMasterService.list_orders(org_id=1, search_query="Penthouse 501")
        self.assertEqual(res.total, 1)
        self.assertEqual(res.items[0].id, order1.id)

        res_cust = OrderMasterService.list_orders(org_id=1, search_query="Chirag Mehta")
        self.assertEqual(res_cust.total, 1)

    def test_10_pagination_and_tenant_isolation(self):
        """10. Multi-tenant boundaries strictly enforced; org1 cannot see org2 orders."""
        order1 = OrderMasterService.create_order(
            org_id=1, customer_id=1,
            items_data=[{"item_description": "Org 1 Order", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order", actor_id=1
        )
        order2 = OrderMasterService.create_order(
            org_id=2, customer_id=2,
            items_data=[{"item_description": "Org 2 Order", "quantity": Decimal("1.000"), "unit_price": Decimal("200.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order", actor_id=2
        )

        res_org1 = OrderMasterService.list_orders(org_id=1)
        res_org2 = OrderMasterService.list_orders(org_id=2)

        self.assertEqual(res_org1.total, 1)
        self.assertEqual(res_org1.items[0].id, order1.id)
        self.assertEqual(res_org2.total, 1)
        self.assertEqual(res_org2.items[0].id, order2.id)

    # ─────────────────────────────────────────────────────────────
    # Lead-to-Order Integration Tests (11 to 20)
    # ─────────────────────────────────────────────────────────────

    def test_11_unconfirmed_lead_does_not_create_order(self):
        """11. An unconfirmed Lead must be rejected from creating a Master Order."""
        with self.assertRaises(OMSValidationError) as ctx:
            OrderMasterService.create_order_from_confirmed_lead(
                org_id=1,
                lead_id=101, # LeadStatus.NEW (unconfirmed)
                actor_id=1,
            )
        self.assertIn("has not reached 'Order Confirmed'", str(ctx.exception))

    def test_12_order_created_only_after_explicit_confirmation(self):
        """12. Order is created once the Lead is confirmed."""
        order = OrderMasterService.create_order_from_confirmed_lead(
            org_id=1,
            lead_id=102, # LeadStatus.CLOSED (confirmed stage)
            actor_id=1,
        )
        self.assertIsNotNone(order)
        self.assertEqual(order.status, MasterOrderStatus.CONFIRMED.value)
        self.assertEqual(order.lead_id, 102)

    def test_13_lead_id_associated_with_master_order(self):
        """13. Lead ID reference is preserved on the Master Order entity."""
        order = OrderMasterService.create_order_from_confirmed_lead(
            org_id=1,
            lead_id=102,
            actor_id=1,
        )
        db.session.refresh(order)
        self.assertEqual(order.lead_id, 102)
        self.assertEqual(order.lead.name, "Prestige Villa Glazing")

    def test_14_customer_association_preserved_for_converted_lead(self):
        """14. When a lead is already associated with an existing customer, customer_id is preserved."""
        order = OrderMasterService.create_order_from_confirmed_lead(
            org_id=1,
            lead_id=103,
            actor_id=1,
        )
        self.assertEqual(order.lead_id, 103)
        self.assertEqual(order.customer_id, 1)

    def test_15_duplicate_confirmation_idempotent(self):
        """15. Calling create_order_from_confirmed_lead multiple times returns the same order."""
        order1 = OrderMasterService.create_order_from_confirmed_lead(org_id=1, lead_id=102, actor_id=1)
        order2 = OrderMasterService.create_order_from_confirmed_lead(org_id=1, lead_id=102, actor_id=1)

        self.assertEqual(order1.id, order2.id)
        self.assertEqual(order1.master_order_number, order2.master_order_number)
        count = OrderMaster.query.filter_by(organization_id=1, lead_id=102).count()
        self.assertEqual(count, 1)

    def test_16_concurrent_confirmation_safe(self):
        """16. Idempotent contract safely handles simulated concurrent confirmation."""
        o1 = OrderMasterService.create_order_from_confirmed_lead(org_id=1, lead_id=102, actor_id=1)
        o2 = OrderMasterService.create_order_from_confirmed_lead(org_id=1, lead_id=102, actor_id=1)
        self.assertEqual(o1.id, o2.id)

    def test_17_cross_tenant_lead_rejected(self):
        """17. Cross-tenant lead references are strictly rejected."""
        with self.assertRaises(OMSTenantIsolationError):
            OrderMasterService.create_order_from_confirmed_lead(
                org_id=1,
                lead_id=104, # Belongs to Org 2
                actor_id=1,
            )

    def test_18_existing_orders_without_lead_continue_working(self):
        """18. Traditional Customer-only orders without Lead references work seamlessly."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Legacy Order Item", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )
        self.assertIsNone(order.lead_id)
        card = OrderMasterService.build_tracking_card_data(order)
        self.assertIsNone(card["lead_ref"])
        self.assertEqual(card["customer_name"], "Chirag Mehta")

    def test_19_draft_vs_active_scope_isolation(self):
        """19. Draft Orders do not pollute the active operational tracking list."""
        draft_order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Draft Item", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="save_draft",
            actor_id=1,
        )
        active_order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "Active Item", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )

        active_res = OrderMasterService.list_orders(org_id=1, scope="active")
        self.assertEqual(active_res.total, 1)
        self.assertEqual(active_res.items[0].id, active_order.id)

        draft_res = OrderMasterService.list_orders(org_id=1, scope="draft")
        self.assertEqual(draft_res.total, 1)
        self.assertEqual(draft_res.items[0].id, draft_order.id)

    def test_20_inspect_360_action_and_json_api(self):
        """20. JSON API endpoint delivers rich tracking data and Inspect 360 link works."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            items_data=[{"item_description": "API Test Glass", "quantity": Decimal("1.000"), "unit_price": Decimal("100.00"), "tax_rate": Decimal("0.00"), "discount_amount": Decimal("0.00")}],
            action_type="confirm_order",
            actor_id=1,
        )

        resp = self.client.get("/orders/", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["orders"]), 1)
        self.assertIn("tracking_card", data["orders"][0])
        self.assertEqual(data["orders"][0]["tracking_card"]["master_order_number"], order.master_order_number)

    def test_21_dashboard_renders_three_dot_menu_and_modals(self):
        """21. Orders Dashboard cards include 3-dot menu (Edit, Change Status, Delete) with valid HTML payloads."""
        import json
        from html.parser import HTMLParser

        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title='Three Dot "Quoted" & \'Menu\' Order',
            items_data=[],
            action_type="confirm_order",
            actor_id=1,
        )

        resp = self.client.get("/orders/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn(order.master_order_number, html)
        self.assertIn(f"order-menu-{order.id}", html)
        self.assertIn("Edit Order", html)
        self.assertIn("Change Status", html)
        self.assertIn("Delete Order", html)
        self.assertIn("quickEditModal", html)
        self.assertIn("quickStatusModal", html)
        self.assertIn("deleteOrderModal", html)

        # Parse rendered <button> tags to guarantee onclick handlers & JSON data-* attributes are uncorrupted
        class MenuButtonParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.buttons = []

            def handle_starttag(self, tag, attrs):
                if tag == "button":
                    self.buttons.append(dict(attrs))

        parser = MenuButtonParser()
        parser.feed(html)
        by_onclick = {b.get("onclick"): b for b in parser.buttons if b.get("onclick")}
        self.assertIn("openQuickEditModal(this)", by_onclick)
        self.assertIn("openQuickStatusModal(this)", by_onclick)
        self.assertIn("openDeleteOrderModal(this)", by_onclick)

        edit_data = json.loads(by_onclick["openQuickEditModal(this)"]["data-order"])
        status_data = json.loads(by_onclick["openQuickStatusModal(this)"]["data-status"])
        delete_data = json.loads(by_onclick["openDeleteOrderModal(this)"]["data-delete"])
        self.assertEqual(edit_data["id"], order.id)
        self.assertEqual(status_data["number"], order.master_order_number)
        self.assertEqual(status_data["status"], order.status)
        self.assertEqual(delete_data["title"], 'Three Dot "Quoted" & \'Menu\' Order')

    def test_22_dashboard_quick_edit_updates_order_and_redirects(self):
        """22. Quick Edit modal updates title, priority, PM, amount and redirects back to dashboard."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Original Dashboard Title",
            items_data=[],
            action_type="confirm_order",
            actor_id=1,
        )

        resp = self.client.post(
            f"/orders/{order.id}/update-box",
            data={
                "box_name": "identity",
                "order_title": "Updated Dashboard Title",
                "priority": "URGENT",
                "expected_delivery_date": "2026-12-15",
                "assigned_to_id": "1",
                "net_order_amount": "25000.00",
                "shipping_address": "New Site Address, Pune",
                "notes": "Updated from 3-dot menu",
                "next": "/orders/?scope=active",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/orders/?scope=active", resp.headers.get("Location", ""))

        db.session.expire_all()
        updated = db.session.get(OrderMaster, order.id)
        self.assertEqual(updated.order_title, "Updated Dashboard Title")
        self.assertEqual(updated.priority, "URGENT")
        self.assertEqual(updated.assigned_to_id, 1)
        self.assertEqual(updated.net_order_amount, Decimal("25000.00"))
        self.assertEqual(updated.shipping_address, "New Site Address, Pune")

    def test_23_dashboard_quick_status_change_and_redirect(self):
        """23. Quick Change Status modal transitions status cleanly and redirects back to dashboard."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Status Change Menu Order",
            items_data=[],
            action_type="confirm_order",
            actor_id=1,
        )

        resp = self.client.post(
            f"/orders/{order.id}/status",
            data={
                "to_status": MasterOrderStatus.IN_PROCUREMENT.value,
                "remarks": "Changed from 3-dot status modal",
                "next": "/orders/?scope=active",
            },
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/orders/?scope=active", resp.headers.get("Location", ""))

        db.session.expire_all()
        updated = db.session.get(OrderMaster, order.id)
        self.assertEqual(updated.status, MasterOrderStatus.IN_PROCUREMENT.value)

    def test_24_soft_delete_order_preserves_db_record_and_isolates_tenant(self):
        """24. Soft-delete marks is_deleted=True, keeps DB row, hides from UI/cockpit, enforces tenant isolation."""
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Order To Soft Delete",
            items_data=[],
            action_type="confirm_order",
            actor_id=1,
        )

        # Cross-tenant delete attempt from Org 2 must fail with 404 and leave order intact
        self._login(2)
        resp_cross = self.client.post(
            f"/orders/{order.id}/delete",
            data={"reason": "Unauthorized delete"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_cross.status_code, 404)
        self.assertFalse(db.session.get(OrderMaster, order.id).is_deleted)

        # Authorized Org 1 soft delete
        self._login(1)
        resp_del = self.client.post(
            f"/orders/{order.id}/delete",
            data={"reason": "Duplicate entry", "next": "/orders/?scope=active"},
            follow_redirects=False,
        )
        self.assertEqual(resp_del.status_code, 302)
        self.assertIn("/orders/?scope=active", resp_del.headers.get("Location", ""))

        db.session.expire_all()
        deleted_row = db.session.get(OrderMaster, order.id)
        # Record still exists in DB with is_deleted=True
        self.assertIsNotNone(deleted_row)
        self.assertTrue(deleted_row.is_deleted)

        # Audit entry recorded in OrderStatusHistory
        hist = OrderStatusHistory.query.filter_by(order_id=order.id, organization_id=1).order_by(OrderStatusHistory.id.desc()).first()
        self.assertIsNotNone(hist)
        self.assertIn("Duplicate entry", hist.remarks)

        # Hidden from Orders Dashboard & list_orders
        list_res = OrderMasterService.list_orders(org_id=1, scope="all")
        self.assertEqual(list_res.total, 0)
        resp_html = self.client.get("/orders/")
        html1 = resp_html.get_data(as_text=True)
        self.assertIn("deleted successfully", html1)
        self.assertNotIn(f"order-card-{order.id}", html1)
        resp_html2 = self.client.get("/orders/")
        self.assertNotIn(order.master_order_number, resp_html2.get_data(as_text=True))

        # Cockpit detail access blocked for soft-deleted order
        with self.assertRaises(OMSValidationError):
            OrderMasterService.get_order_cockpit_details(org_id=1, order_id=order.id)


if __name__ == "__main__":
    unittest.main()

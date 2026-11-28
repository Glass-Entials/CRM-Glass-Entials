import os
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flask import Flask, g
from flask_login import LoginManager
from sqlalchemy import text

from model import (
    db,
    Organization,
    User,
    UserRole,
    Employee,
    Customer,
    OrderMaster,
    OrderItem,
    OrderStatusHistory,
)
from routes.orders import orders_bp
from routes.trash import trash_bp
from services.oms.order_master_service import OrderMasterService
from utils.timezone import ist_filter


class TrashOrdersIntegrationTestCase(unittest.TestCase):
    """End-to-end verification that soft-deleted orders appear in Trash Management and support restore & permanent delete."""

    @classmethod
    def setUpClass(cls):
        cls.app = Flask("trash_orders_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="trash-orders-test-secret",
        )
        db.init_app(cls.app)
        cls.app.add_template_filter(ist_filter, "ist")

        cls.login_manager = LoginManager()
        cls.login_manager.init_app(cls.app)

        @cls.login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        cls.app.register_blueprint(orders_bp)
        cls.app.register_blueprint(trash_bp)

        for ep in [
            "home_page", "customers.customers_list", "org_customers.org_customers_list",
            "leads.leads_list", "leads.all_follow_ups", "projects.projects_list",
            "tasks.tasks_list", "tasks.daily_tasks_list", "quotations.quotation_list",
            "accounts.invoice_list", "payments.due_payments", "payments.received_payments",
            "expenses.expenses_list", "gst_reports.gst_report_page", "products.products_list",
            "employees.employee_list", "contacts.contacts_list", "vendors.vendor_list",
            "call_logger.dashboard", "org.organization_settings",
            "auth.user_profile", "auth.settings", "auth.logout", "auth.notifications",
            "messages.messages_page", "messages.total_unread_count", "api.recent_notifications",
            "saas.billing_page",
        ]:
            cls.app.add_url_rule(
                f"/dummy/{ep.replace('.', '/')}",
                endpoint=ep,
                view_func=lambda ep=ep: f"dummy_{ep}",
            )

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

        self.org1 = Organization(id=1, name="GlassEntials Premier", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Other Org", unique_code="ORG002")
        db.session.add_all([self.org1, self.org2])
        db.session.commit()

        self.admin1 = User(id=1, username="admin1", password="hash", email="a1@example.com", role=UserRole.ADMIN, organization_id=1)
        self.admin2 = User(id=2, username="admin2", password="hash", email="a2@example.com", role=UserRole.ADMIN, organization_id=2)
        db.session.add_all([self.admin1, self.admin2])
        db.session.commit()

        self.emp1 = Employee(id=1, name="Aarav Mehta", email="aarav@example.com", user_id=1, organization_id=1)
        self.emp2 = Employee(id=2, name="Other PM", email="other@example.com", user_id=2, organization_id=2)
        db.session.add_all([self.emp1, self.emp2])
        db.session.commit()

        self.cust1 = Customer(id=1, name="Vanguard Towers", email="vanguard@example.com", phone_number="9999900001", organization_id=1, created_by=1)
        self.cust2 = Customer(id=2, name="Other Customer", email="otherc@example.com", phone_number="9999900002", organization_id=2, created_by=2)
        db.session.add_all([self.cust1, self.cust2])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        db.session.execute(text("PRAGMA foreign_keys = OFF;"))
        db.drop_all()
        self.context.pop()

    def _login(self, user_id):
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True
        g._login_user = db.session.get(User, int(user_id))

    def test_01_deleted_order_shows_in_trash_and_restores(self):
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Skyline Facade Glazing",
            items_data=[{
                "item_description": "12mm Toughened Glass",
                "quantity": Decimal("10"),
                "unit_price": Decimal("500"),
                "tax_rate": Decimal("18"),
            }],
            action_type="confirm_order",
            actor_id=1,
        )

        # Soft delete via Orders Dashboard route
        resp_del = self.client.post(f"/orders/{order.id}/delete", data={"reason": "Entered twice"}, follow_redirects=False)
        self.assertEqual(resp_del.status_code, 302)

        # Visible in Trash Management (All Modules & Orders filter + search + premium module tabs/counts)
        resp_all = self.client.get("/workplace/trash/")
        self.assertEqual(resp_all.status_code, 200)
        html_all = resp_all.get_data(as_text=True)
        self.assertIn(order.master_order_number, html_all)
        self.assertIn("Skyline Facade Glazing", html_all)
        self.assertIn("Aarav Mehta", html_all)
        self.assertIn("orders", html_all)
        self.assertIn("Total in Trash:", html_all)
        self.assertIn("trash-tabs-strip", html_all)

        resp_mod = self.client.get(f"/workplace/trash/?module=orders&search={order.master_order_number}")
        self.assertEqual(resp_mod.status_code, 200)
        self.assertIn(order.master_order_number, resp_mod.get_data(as_text=True))

        # Org 2 isolation: cannot see or restore Org 1's trashed order
        self._login(2)
        resp_org2 = self.client.get("/workplace/trash/?module=orders")
        self.assertEqual(resp_org2.status_code, 200)
        self.assertNotIn(order.master_order_number, resp_org2.get_data(as_text=True))
        self.client.post(f"/workplace/trash/restore/orders/{order.id}", follow_redirects=False)
        db.session.expire_all()
        self.assertTrue(db.session.get(OrderMaster, order.id).is_deleted)

        # Restore back as Org 1 Admin
        self._login(1)
        resp_restore = self.client.post(f"/workplace/trash/restore/orders/{order.id}", follow_redirects=False)
        self.assertEqual(resp_restore.status_code, 302)
        db.session.expire_all()
        restored = db.session.get(OrderMaster, order.id)
        self.assertFalse(restored.is_deleted)
        self.assertEqual(OrderMasterService.list_orders(org_id=1, scope="all").total, 1)

    def test_02_trashed_order_scan_dependencies_and_permanent_delete(self):
        self._login(1)
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=1,
            order_title="Permanent Purge Test Order",
            items_data=[{
                "item_description": "Laminated Panel",
                "quantity": Decimal("5"),
                "unit_price": Decimal("1000"),
                "tax_rate": Decimal("18"),
            }],
            action_type="confirm_order",
            actor_id=1,
        )
        self.client.post(f"/orders/{order.id}/delete", data={"reason": "Test purge"})

        resp_scan = self.client.get(f"/workplace/trash/scan-dependencies?module=orders&record_id={order.id}")
        self.assertEqual(resp_scan.status_code, 200)
        scan_data = resp_scan.get_json()
        self.assertTrue(scan_data["success"])
        self.assertIn(order.master_order_number, scan_data["record_name"])
        self.assertTrue(scan_data["has_dependencies"])
        self.assertEqual(scan_data["dependencies"].get("Order Items"), 1)

        resp_perm = self.client.post(f"/workplace/trash/delete/orders/{order.id}", follow_redirects=False)
        self.assertEqual(resp_perm.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(OrderMaster, order.id))
        self.assertEqual(OrderItem.query.filter_by(order_id=order.id).count(), 0)
        self.assertEqual(OrderStatusHistory.query.filter_by(order_id=order.id).count(), 0)


if __name__ == "__main__":
    unittest.main()

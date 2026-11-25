"""
tests/test_oms_notifications.py
Unit tests verifying scoped OMS notification delivery:
1. Only directly involved order stakeholders are notified (created_by, assigned_to,
   active fitting technicians, procurement PO owners, delivery staff).
2. Unrelated Admins, Managers, and Employees in the same organization do NOT receive
   notifications for orders they are not involved in.
3. Inactive users, deleted employees, deleted visits/POs, and cross-tenant users are excluded.
4. Status transition route logs and notifies with the real previous status (not 'INIT').
"""
import unittest
import sys
from pathlib import Path
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from flask_login import LoginManager
from sqlalchemy import text

from model import (
    db,
    User,
    UserRole,
    Organization,
    Customer,
    Vendor,
    Employee,
    OrderMaster,
    OrderVendorProcurement,
    OrderDelivery,
    OrderFittingVisit,
    Notification,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingVisitStatus,
)
from services.oms import OrderMasterService, OMSNotificationService
from routes.orders import orders_bp
from utils.extensions import socketio


class OMSNotificationScopingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_notification_scoping_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="oms-notif-scoping-test-secret",
        )
        db.init_app(cls.app)
        socketio.init_app(cls.app, async_mode="threading")

        cls.login_manager = LoginManager()
        cls.login_manager.init_app(cls.app)

        @cls.login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        cls.app.register_blueprint(orders_bp)

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        db.session.execute(text("PRAGMA foreign_keys = ON;"))
        db.create_all()

        self.org1 = Organization(id=1, name="GlassEntials Main", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Other Org", unique_code="ORG002")
        db.session.add_all([self.org1, self.org2])
        db.session.flush()

        # Users & Employees in Org 1
        self.u_creator = User(id=1, username="creator_admin", password="pw", email="u1@test.com", role=UserRole.ADMIN, organization_id=1)
        self.u_unrel_admin = User(id=2, username="unrel_admin", password="pw", email="u2@test.com", role=UserRole.ADMIN, organization_id=1)
        self.u_unrel_mgr = User(id=3, username="unrel_mgr", password="pw", email="u3@test.com", role=UserRole.MANAGER, organization_id=1)
        self.u_assigned_pm = User(id=4, username="assigned_pm", password="pw", email="u4@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_unrel_emp = User(id=5, username="unrel_emp", password="pw", email="u5@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_tech = User(id=6, username="fit_tech", password="pw", email="u6@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_proc = User(id=7, username="proc_owner", password="pw", email="u7@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_delv = User(id=8, username="delv_staff", password="pw", email="u8@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_del_tech = User(id=9, username="del_tech", password="pw", email="u9@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_org2_admin = User(id=10, username="org2_admin", password="pw", email="u10@test.com", role=UserRole.ADMIN, organization_id=2)

        db.session.add_all([
            self.u_creator, self.u_unrel_admin, self.u_unrel_mgr,
            self.u_assigned_pm, self.u_unrel_emp, self.u_tech,
            self.u_proc, self.u_delv, self.u_del_tech, self.u_org2_admin,
        ])
        db.session.flush()

        self.emp_creator = Employee(id=1, user_id=1, name="Creator Admin", organization_id=1)
        self.emp_unrel_admin = Employee(id=2, user_id=2, name="Unrelated Admin", organization_id=1)
        self.emp_unrel_mgr = Employee(id=3, user_id=3, name="Unrelated Manager", organization_id=1)
        self.emp_assigned_pm = Employee(id=4, user_id=4, name="Assigned PM", organization_id=1)
        self.emp_unrel_emp = Employee(id=5, user_id=5, name="Unrelated Emp", organization_id=1)
        self.emp_tech = Employee(id=6, user_id=6, name="Fitting Tech", organization_id=1)
        self.emp_proc = Employee(id=7, user_id=7, name="Procurement Owner", organization_id=1)
        self.emp_delv = Employee(id=8, user_id=8, name="Delivery Staff", organization_id=1)
        self.emp_del_tech = Employee(id=9, user_id=9, name="Removed Tech", organization_id=1)
        self.emp_org2 = Employee(id=10, user_id=10, name="Org2 Admin", organization_id=2)

        db.session.add_all([
            self.emp_creator, self.emp_unrel_admin, self.emp_unrel_mgr,
            self.emp_assigned_pm, self.emp_unrel_emp, self.emp_tech,
            self.emp_proc, self.emp_delv, self.emp_del_tech, self.emp_org2,
        ])

        self.cust = Customer(id=1, name="Wardha Glass", phone_number="9876543210", email="wg@test.com", organization_id=1, created_by=1)
        self.vendor = Vendor(id=1, code="VND-001", company_name="Saint Gobain", organization_id=1, created_by=1)
        db.session.add_all([self.cust, self.vendor])
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

    def _login(self, user_id: int):
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    def test_01_unrelated_admins_and_managers_do_not_receive_order_notifications(self):
        """Only creator & assigned PM receive notifications, not unrelated Admins/Managers."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=self.cust.id,
            order_title="Wardha Glass",
            items_data=[],
            assigned_to_id=self.emp_assigned_pm.id,
            actor_id=self.emp_creator.id,
        )
        db.session.commit()

        OMSNotificationService.notify_status_transition(
            org_id=1,
            order=order,
            from_status="DRAFT",
            to_status="CONFIRMED",
            actor_id=self.emp_creator.id,
        )
        db.session.commit()

        notifs = Notification.query.filter_by(organization_id=1).all()
        recipient_ids = {n.recipient_id for n in notifs}

        self.assertEqual(recipient_ids, {self.emp_assigned_pm.id})
        self.assertNotIn(self.emp_unrel_admin.id, recipient_ids)
        self.assertNotIn(self.emp_unrel_mgr.id, recipient_ids)
        self.assertNotIn(self.emp_unrel_emp.id, recipient_ids)
        self.assertNotIn(self.emp_creator.id, recipient_ids)  # actor excluded

    def test_02_involved_tech_procurement_and_delivery_owners_included(self):
        """Fitting tech, procurement owner, and delivery staff on active order records are notified."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=self.cust.id,
            order_title="Complex Site Order",
            items_data=[],
            assigned_to_id=self.emp_assigned_pm.id,
            actor_id=self.emp_creator.id,
        )

        # Active fitting visit
        visit = OrderFittingVisit(
            visit_number="FIT-001",
            order_id=order.id,
            organization_id=1,
            technician_id=self.emp_tech.id,
            scheduled_date=datetime.utcnow(),
            status=OrderFittingVisitStatus.SCHEDULED.value,
        )
        # Soft-deleted fitting visit (must NOT notify emp_del_tech)
        deleted_visit = OrderFittingVisit(
            visit_number="FIT-002",
            order_id=order.id,
            organization_id=1,
            technician_id=self.emp_del_tech.id,
            scheduled_date=datetime.utcnow(),
            status=OrderFittingVisitStatus.CANCELLED.value,
            is_deleted=True,
        )
        # Active procurement PO
        po = OrderVendorProcurement(
            po_number="PO-001",
            order_id=order.id,
            vendor_id=self.vendor.id,
            organization_id=1,
            status=OrderProcurementStatus.PO_ISSUED.value,
            created_by_id=self.emp_proc.id,
        )
        # Active delivery
        delv = OrderDelivery(
            delivery_number="DEL-001",
            order_id=order.id,
            organization_id=1,
            status=OrderDeliveryStatus.OUT_FOR_DELIVERY.value,
            delivered_by_id=self.emp_delv.id,
        )
        db.session.add_all([visit, deleted_visit, po, delv])
        db.session.commit()

        stakeholders = OMSNotificationService.get_order_stakeholders(1, order)
        self.assertEqual(
            stakeholders,
            {
                self.emp_creator.id,
                self.emp_assigned_pm.id,
                self.emp_tech.id,
                self.emp_proc.id,
                self.emp_delv.id,
            },
        )
        self.assertNotIn(self.emp_unrel_admin.id, stakeholders)
        self.assertNotIn(self.emp_unrel_mgr.id, stakeholders)
        self.assertNotIn(self.emp_del_tech.id, stakeholders)

    def test_03_inactive_and_deleted_and_cross_org_excluded(self):
        """Inactive users, deleted employees, and cross-org IDs are filtered out."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=self.cust.id,
            order_title="Isolation Test Order",
            items_data=[],
            assigned_to_id=self.emp_assigned_pm.id,
            actor_id=self.emp_creator.id,
        )
        visit = OrderFittingVisit(
            visit_number="FIT-ISO-1",
            order_id=order.id,
            organization_id=1,
            technician_id=self.emp_tech.id,
            scheduled_date=datetime.utcnow(),
        )
        db.session.add(visit)

        # Deactivate assigned PM's user & soft-delete tech's employee record
        self.u_assigned_pm.is_active = False
        self.emp_tech.is_deleted = True
        # Force cross-org employee ID onto order directly to verify safeguard
        order.assigned_to_id = self.emp_org2.id
        db.session.commit()

        stakeholders = OMSNotificationService.get_order_stakeholders(1, order)
        self.assertEqual(stakeholders, {self.emp_creator.id})
        self.assertNotIn(self.emp_assigned_pm.id, stakeholders)
        self.assertNotIn(self.emp_tech.id, stakeholders)
        self.assertNotIn(self.emp_org2.id, stakeholders)

    def test_04_transition_status_route_uses_real_from_status_and_scoped_recipients(self):
        """POST /orders/<id>/status records real previous status and notifies only stakeholders."""
        order = OrderMasterService.create_order(
            org_id=1,
            customer_id=self.cust.id,
            order_title="Wardha Glass",
            items_data=[{
                "item_description": "12mm Toughened Glass",
                "quantity": Decimal("10"),
                "unit_price": Decimal("500"),
                "tax_rate": Decimal("18"),
            }],
            assigned_to_id=self.emp_assigned_pm.id,
            actor_id=self.emp_creator.id,
        )
        db.session.commit()

        self._login(self.u_creator.id)
        resp = self.client.post(
            f"/orders/{order.id}/status",
            data={"to_status": MasterOrderStatus.CONFIRMED.value, "remarks": "Confirm order"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp.status_code, 200)

        notifs = Notification.query.filter_by(organization_id=1).all()
        self.assertEqual(len(notifs), 1)
        notif = notifs[0]
        self.assertEqual(notif.recipient_id, self.emp_assigned_pm.id)
        self.assertIn("from DRAFT to CONFIRMED", notif.message)
        self.assertNotIn("from INIT", notif.message)


if __name__ == "__main__":
    unittest.main()

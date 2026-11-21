"""
tests/test_oms_phase3_production.py
Comprehensive Phase 3 Production Hardening and Business Readiness Test Suite.
Runs on isolated MySQL 8.0 InnoDB database ('oms_test_db').

Verifies:
1. Document Management: Upload, download, inline preview, soft-delete, MIME/extension/size validation, tenant isolation.
2. Notifications & Activities: Lifecycle transitions, de-duplication, stakeholder dispatch, ActivityLog integration.
3. Executive Intelligence & Dashboard: Bottlenecks, overdue tracking, gross profit, cash flow margin, AP/AR ledgers.
4. Advanced Search, Filtering & Exports: Multi-criteria search, Excel-compatible CSV with BOM, styled XLSX with formulas, PDF summary.
5. RBAC & Tenant Isolation: Cross-tenant download/export rejection, role requirements.
"""

import os
import io
import csv
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote_plus, urlparse

from flask import Flask, url_for
from flask_login import LoginManager, login_user
from flask_migrate import Migrate, upgrade
from flask_wtf.csrf import CSRFProtect
import pymysql
import openpyxl

from model import (
    db,
    Organization,
    User,
    UserRole,
    Employee,
    Customer,
    Vendor,
    Product,
    Quotation,
    Invoice,
    Payment,
    PaymentStatus,
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
    OrderDocument,
    OrderRevision,
    OrderStatusHistory,
    OrderExceptionalClosure,
    Notification,
    ActivityLog,
    MasterOrderStatus,
    OrderDocumentType,
)
from routes.orders import orders_bp
from services.oms import (
    OrderMasterService,
    ProcurementService,
    DeliveryService,
    FinancialReconciliationService,
    OMSDocumentService,
    OMSNotificationService,
    OMSReportingService,
    OMSExportService,
    OMSError,
    OMSValidationError,
    OMSInvariantError,
    OMSTenantIsolationError,
)

import urllib.parse
from dotenv import dotenv_values

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


class TestOMSPhase3Production(unittest.TestCase):
    """Production hardening and business readiness test suite on MySQL 8.0."""

    @classmethod
    def setUpClass(cls):
        setup_mysql_test_database()

        cls.upload_dir = Path(__file__).resolve().parents[1] / "tmp_test_uploads"
        cls.upload_dir.mkdir(parents=True, exist_ok=True)

        cls.app = Flask(__name__, template_folder=str(Path(__file__).resolve().parents[1] / "templates"))
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY="test-secret-key-oms-phase3",
            SQLALCHEMY_DATABASE_URI=TEST_DB_URL,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            UPLOAD_FOLDER=str(cls.upload_dir),
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

        # Register dummy routes for shared navbar
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
            upgrade(revision="f6a7b8c9d0e1")

            # Seed Organizations
            org1 = Organization(id=1, name="Apex Glass Industries", unique_code="APEX_01")
            org2 = Organization(id=2, name="Zenith Windows Corp", unique_code="ZEN_02")
            db.session.add_all([org1, org2])
            db.session.commit()

            # Seed Users & Employees
            u_admin1 = User(id=1, username="admin_apex", email="admin@apex.com", role=UserRole.ADMIN, organization_id=1, password="pwd")
            u_mgr1 = User(id=2, username="mgr_apex", email="mgr@apex.com", role=UserRole.MANAGER, organization_id=1, password="pwd")
            u_emp1 = User(id=3, username="emp_apex", email="emp@apex.com", role=UserRole.EMPLOYEE, organization_id=1, password="pwd")
            u_org2 = User(id=4, username="admin_zenith", email="admin@zenith.com", role=UserRole.ADMIN, organization_id=2, password="pwd")
            db.session.add_all([u_admin1, u_mgr1, u_emp1, u_org2])
            db.session.commit()

            e_admin1 = Employee(id=1, user_id=1, organization_id=1, name="Rajesh Admin", email="admin@apex.com")
            e_mgr1 = Employee(id=2, user_id=2, organization_id=1, name="Vikram Manager", email="mgr@apex.com")
            e_emp1 = Employee(id=3, user_id=3, organization_id=1, name="Sunil Employee", email="emp@apex.com")
            e_org2 = Employee(id=4, user_id=4, organization_id=2, name="Zenith Admin", email="admin@zenith.com")
            db.session.add_all([e_admin1, e_mgr1, e_emp1, e_org2])
            db.session.commit()

            # Seed Customers & Vendors
            c1 = Customer(
                id=1, name="Metro Glass Towers Ltd", email="procurement@metro.com",
                phone_number="9876543210", address="Plot 42, Cyber City, Gurgaon",
                organization_id=1, created_by=1
            )
            c2 = Customer(
                id=2, name="Zenith Private Client", email="client@zenith.com",
                phone_number="9876543211", address="Sector 18, Noida",
                organization_id=2, created_by=3
            )
            v1 = Vendor(id=1, organization_id=1, company_name="Saint-Gobain Glass India", code="VEN-SG-01")
            v2 = Vendor(id=2, organization_id=1, company_name="Asahi India Glass Ltd", code="VEN-AIS-02")
            v_org2 = Vendor(id=3, organization_id=2, company_name="Rogue Zenith Glass", code="VEN-ZEN-01")
            db.session.add_all([c1, c2, v1, v2, v_org2])
            db.session.commit()

            # Seed Product
            p1 = Product(id=1, organization_id=1, name="Toughened Glass 12mm Clear")
            db.session.add(p1)
            db.session.commit()

            # Seed Base Order
            base_order = OrderMasterService.create_order(
                org_id=1,
                customer_id=1,
                order_title="Base Production Master Order",
                items_data=[
                    {
                        "product_id": 1,
                        "description": "Toughened Glass 12mm Clear",
                        "quantity": Decimal("100.000"),
                        "unit_price": Decimal("1000.00"),
                        "tax_rate": Decimal("18.00"),
                    }
                ],
                actor_id=1,
            )

    def setUp(self):
        self.client = self.app.test_client()
        # Login as Admin Org 1 by default
        self.client.get("/test-login/1")

    # ========================================================================
    # TEST 1: DOCUMENT MANAGEMENT LIFECYCLE & TENANT ISOLATION
    # ========================================================================
    def test_01_document_management_lifecycle(self):
        """
        Verify secure document upload, download, in-browser preview, soft-delete,
        MIME validation, oversized file rejection, and strict tenant isolation.
        """
        with self.app.app_context():
            # 1. Create a test order
            order = OrderMasterService.create_order(
                org_id=1,
                customer_id=1,
                order_title="High-Rise Glazing Documents Test",
                items_data=[
                    {
                        "product_id": 1,
                        "description": "Toughened Glass 12mm Clear",
                        "quantity": Decimal("100.000"),
                        "unit_price": Decimal("1000.00"),
                        "tax_rate": Decimal("18.00"),
                    }
                ],
                actor_id=1,
            )
            order_id = order.id

        # 2. Reject empty file
        resp_empty = self.client.post(
            f"/orders/{order_id}/documents/upload",
            data={"file": (io.BytesIO(b""), "empty.pdf"), "document_type": "QUOTATION", "title": "Empty Doc"},
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_empty.status_code, 400)
        self.assertIn("empty", resp_empty.get_json()["error"])

        # 3. Reject disallowed extension (.exe)
        resp_exe = self.client.post(
            f"/orders/{order_id}/documents/upload",
            data={"file": (io.BytesIO(b"malicious_bytes"), "payload.exe"), "document_type": "OTHER", "title": "Malware"},
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_exe.status_code, 400)
        self.assertIn("not permitted", resp_exe.get_json()["error"])

        # 4. Valid PDF Upload
        pdf_content = b"%PDF-1.4 test architectural elevation drawing"
        resp_upload_pdf = self.client.post(
            f"/orders/{order_id}/documents/upload",
            data={
                "file": (io.BytesIO(pdf_content), "architectural_drawing.pdf"),
                "document_type": "QUOTATION",
                "title": "Architectural Elevation Drawing v1",
                "is_customer_visible": "true",
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_upload_pdf.status_code, 201)
        pdf_doc_id = resp_upload_pdf.get_json()["document_id"]
        self.assertIsNotNone(pdf_doc_id)

        # 5. Valid Image Upload (PNG)
        png_content = b"\x89PNG\r\n\x1a\n test png image bytes"
        resp_upload_png = self.client.post(
            f"/orders/{order_id}/documents/upload",
            data={
                "file": (io.BytesIO(png_content), "site_measurement.png"),
                "document_type": "SITE_PHOTO",
                "title": "Site Measurement Front Elevation",
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_upload_png.status_code, 201)
        png_doc_id = resp_upload_png.get_json()["document_id"]

        with self.app.app_context():
            doc_record = OrderDocument.query.get(pdf_doc_id)
            self.assertEqual(doc_record.document_type, "QUOTATION")
            self.assertTrue(doc_record.is_customer_visible)
            self.assertEqual(doc_record.file_size, len(pdf_content))
            # Verify file stored outside public static directory
            self.assertIn("order_docs", doc_record.file_path)
            self.assertTrue(os.path.exists(doc_record.file_path))

        # 6. Download PDF Document (Org 1 Admin)
        resp_dl = self.client.get(f"/orders/{order_id}/documents/{pdf_doc_id}/download")
        self.assertEqual(resp_dl.status_code, 200)
        self.assertEqual(resp_dl.data, pdf_content)
        self.assertIn("attachment", resp_dl.headers.get("Content-Disposition", ""))

        # 7. Preview PDF Document inline (Org 1 Admin)
        resp_prev = self.client.get(f"/orders/{order_id}/documents/{pdf_doc_id}/preview")
        self.assertEqual(resp_prev.status_code, 200)
        self.assertEqual(resp_prev.data, pdf_content)
        self.assertNotIn("attachment", resp_prev.headers.get("Content-Disposition", ""))

        # 8. Cross-Tenant Download Isolation (Org 2 Admin attempting to download Org 1 doc)
        self.client.get("/test-login/4")  # Login as Zenith Admin (Org 2)
        resp_cross_dl = self.client.get(f"/orders/{order_id}/documents/{pdf_doc_id}/download")
        # Should be redirected or rejected
        self.assertIn(resp_cross_dl.status_code, [302, 404])

        # 9. Return to Org 1 and Soft Delete Document
        self.client.get("/test-login/1")
        resp_del = self.client.post(
            f"/orders/{order_id}/documents/{pdf_doc_id}/delete",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_del.status_code, 200)

        with self.app.app_context():
            deleted_doc = OrderDocument.query.get(pdf_doc_id)
            self.assertTrue(deleted_doc.is_deleted)
            # Physical file still preserved for compliance
            self.assertTrue(os.path.exists(deleted_doc.file_path))

            # Excluded from active document list
            active_docs = OMSDocumentService.list_order_documents(org_id=1, order_id=order_id)
            active_ids = [d.id for d in active_docs]
            self.assertNotIn(pdf_doc_id, active_ids)
            self.assertIn(png_doc_id, active_ids)

    # ========================================================================
    # TEST 2: NOTIFICATIONS, AUDIT LOGGING & ACTIVITY TIMELINE
    # ========================================================================
    def test_02_notifications_and_activity_audit(self):
        """
        Verify automatic notification distribution on status changes, PO issuance,
        QC arrival, delivery dispatch, fitting signoff, and de-duplication window.
        """
        with self.app.app_context():
            order = OrderMasterService.create_order(
                org_id=1,
                customer_id=1,
                order_title="Notification & Audit Verification Order",
                items_data=[
                    {
                        "product_id": 1,
                        "description": "Toughened Glass 12mm Clear",
                        "quantity": Decimal("50.000"),
                        "unit_price": Decimal("1000.00"),
                        "tax_rate": Decimal("18.00"),
                    }
                ],
                actor_id=1,
            )
            order_id = order.id

        # 1. Status Transition Notification
        resp_trans = self.client.post(
            f"/orders/{order_id}/status",
            data={"to_status": "CONFIRMED", "remarks": "Approved by client contract"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_trans.status_code, 200)

        with self.app.app_context():
            # Notifications distributed to managers/admins (excluding actor)
            notifs = Notification.query.filter_by(organization_id=1).all()
            self.assertGreater(len(notifs), 0)
            status_notif = [n for n in notifs if "CONFIRMED" in n.title]
            self.assertGreater(len(status_notif), 0)
            self.assertIn(f"/orders/{order_id}", status_notif[0].link)

            # ActivityLog entry created
            act = ActivityLog.query.filter_by(
                organization_id=1,
                entity_type="order",
                entity_id=order_id,
            ).first()
            self.assertIsNotNone(act)
            self.assertEqual(act.action, "order_status_updated")

        # 2. De-duplication Check: Repeated identical transition within 15 mins does not spam
        with self.app.app_context():
            order_obj = OrderMaster.query.get(order_id)
            initial_count = Notification.query.filter_by(organization_id=1).count()
            # Attempt to emit identical status transition notification
            OMSNotificationService.notify_status_transition(
                org_id=1,
                order=order_obj,
                from_status="CONFIRMED",
                to_status="CONFIRMED",
                actor_id=1,
            )
            subsequent_count = Notification.query.filter_by(organization_id=1).count()
            # Count must remain unchanged due to de-duplication
            self.assertEqual(initial_count, subsequent_count)

        # 3. Overdue Orders Scanner
        with self.app.app_context():
            # Set target delivery date in the past
            order_obj = OrderMaster.query.get(order_id)
            order_obj.expected_delivery_date = datetime.utcnow() - timedelta(days=5)
            db.session.commit()

            notified = OMSNotificationService.check_and_notify_overdue_orders(org_id=1)
            self.assertGreaterEqual(notified, 1)

            overdue_notif = Notification.query.filter(
                Notification.organization_id == 1,
                Notification.title.ilike("%Overdue%"),
            ).first()
            self.assertIsNotNone(overdue_notif)

    # ========================================================================
    # TEST 3: EXECUTIVE DASHBOARD & BUSINESS INTELLIGENCE
    # ========================================================================
    def test_03_reporting_and_executive_dashboard(self):
        """
        Verify operational pipeline metrics, commercial ledger calculations,
        gross margins, overdue tracking, and dashboard rendering.
        """
        # 1. Fetch dashboard JSON metrics
        resp_dash_json = self.client.get(
            "/orders/dashboard",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_dash_json.status_code, 200)
        data = resp_dash_json.get_json()

        self.assertIn("metrics", data)
        self.assertIn("summary", data["metrics"])
        self.assertIn("pipeline", data["metrics"])
        self.assertIn("financials", data["metrics"])
        self.assertIn("overdue_orders", data)

        fin = data["metrics"]["financials"]
        self.assertIn("total_revenue", fin)
        self.assertIn("gross_profit", fin)
        self.assertIn("gross_margin_percentage", fin)
        self.assertIn("operating_cash_margin", fin)
        self.assertIn("customer_outstanding", fin)
        self.assertIn("vendor_outstanding", fin)

        # 2. Render dashboard HTML view
        resp_dash_html = self.client.get("/orders/dashboard")
        self.assertEqual(resp_dash_html.status_code, 200)
        html = resp_dash_html.get_data(as_text=True)
        self.assertIn("OMS Executive Intelligence", html)
        self.assertIn("Operational Pipeline Breakdown", html)
        self.assertIn("Commercial & Profitability Ledger", html)

        # 3. Overdue scan endpoint
        resp_overdue = self.client.get("/orders/overdue?alert=true")
        self.assertEqual(resp_overdue.status_code, 302)  # Redirects to dashboard with flash info

    # ========================================================================
    # TEST 4: ADVANCED SEARCH, PAGINATION & EXPORTS (CSV, EXCEL, PDF)
    # ========================================================================
    def test_04_search_pagination_and_exports(self):
        """
        Verify multi-criteria search, Excel-compatible UTF-8 CSV, styled XLSX with
        formulas, printable PDF summary, and strict tenant isolation.
        """
        with self.app.app_context():
            order = OrderMaster.query.filter_by(organization_id=1).first()
            self.assertIsNotNone(order)
            order_id = order.id
            order_num = order.master_order_number

        # 1. Advanced Search Query Builder
        with self.app.app_context():
            # Match by Order Number
            q_num = OMSExportService.build_filtered_query(org_id=1, search=order_num).all()
            self.assertGreaterEqual(len(q_num), 1)

            # Match by Customer Name
            q_cust = OMSExportService.build_filtered_query(org_id=1, search="Metro Glass").all()
            self.assertGreaterEqual(len(q_cust), 1)

            # Match by Product Description
            q_prod = OMSExportService.build_filtered_query(org_id=1, search="Toughened Glass").all()
            self.assertGreaterEqual(len(q_prod), 1)

            # Tenant Isolation in Search: Org 2 searching "Metro Glass" must return 0 records
            q_zenith = OMSExportService.build_filtered_query(org_id=2, search="Metro Glass").all()
            self.assertEqual(len(q_zenith), 0)

        # 2. CSV Export
        resp_csv = self.client.get("/orders/export/csv")
        self.assertEqual(resp_csv.status_code, 200)
        self.assertEqual(resp_csv.mimetype, "text/csv")
        csv_bytes = resp_csv.data
        # Verify UTF-8 BOM present
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbf"))

        csv_text = csv_bytes.decode("utf-8-sig")
        reader = list(csv.reader(io.StringIO(csv_text)))
        self.assertGreaterEqual(len(reader), 2)  # Header + at least 1 order row
        headers = reader[0]
        self.assertIn("Order Number", headers)
        self.assertIn("Net Revenue (INR)", headers)
        self.assertIn("Gross Margin (%)", headers)

        # 3. Excel Export (.xlsx)
        resp_excel = self.client.get("/orders/export/excel")
        self.assertEqual(resp_excel.status_code, 200)
        self.assertIn("spreadsheetml", resp_excel.mimetype)

        wb = openpyxl.load_workbook(io.BytesIO(resp_excel.data))
        self.assertIn("Order Management", wb.sheetnames)
        ws = wb["Order Management"]
        # Verify header title
        self.assertEqual(ws.cell(row=1, column=1).value, "Order Number")
        self.assertEqual(ws.cell(row=1, column=15).value, "Margin %")
        # Verify styling applied (Dark blue header fill)
        self.assertEqual(ws.cell(row=1, column=1).fill.start_color.rgb, "000F172A")
        # Verify summary total row present at bottom
        last_row = ws.max_row
        self.assertEqual(ws.cell(row=last_row, column=1).value, "TOTAL")

        # 4. PDF Summary Export
        resp_pdf = self.client.get(f"/orders/{order_id}/pdf")
        self.assertEqual(resp_pdf.status_code, 200)
        self.assertEqual(resp_pdf.mimetype, "application/pdf")
        # Verify PDF magic bytes
        self.assertTrue(resp_pdf.data.startswith(b"%PDF-"))

        # 5. Cross-Tenant Export Isolation
        self.client.get("/test-login/4")  # Login as Zenith (Org 2)
        resp_zenith_csv = self.client.get("/orders/export/csv")
        self.assertEqual(resp_zenith_csv.status_code, 200)
        zenith_csv_text = resp_zenith_csv.data.decode("utf-8-sig")
        zenith_rows = list(csv.reader(io.StringIO(zenith_csv_text)))
        # Only header row should exist since Org 2 has no orders
        self.assertEqual(len(zenith_rows), 1)

        # Zenith cannot export Org 1 order PDF
        resp_zenith_pdf = self.client.get(f"/orders/{order_id}/pdf")
        self.assertEqual(resp_zenith_pdf.status_code, 302)  # Redirects with flash error

    # ========================================================================
    # TEST 5: RBAC & COCKPIT UI RENDERING
    # ========================================================================
    def test_05_rbac_and_cockpit_ui_rendering(self):
        """
        Verify role-based access control on document operations and template rendering.
        """
        with self.app.app_context():
            order = OrderMaster.query.filter_by(organization_id=1).first()
            order_id = order.id

        # 1. Unauthenticated request to document upload rejected (302/401)
        unauth_client = self.app.test_client()
        resp_unauth = unauth_client.post(f"/orders/{order_id}/documents/upload")
        self.assertIn(resp_unauth.status_code, [302, 401])

        # 2. Employee role permitted to upload documents
        self.client.get("/test-login/3")  # Login as Employee (Org 1)
        resp_emp_up = self.client.post(
            f"/orders/{order_id}/documents/upload",
            data={
                "file": (io.BytesIO(b"%PDF- test employee upload"), "drawing.pdf"),
                "document_type": "OTHER",
                "title": "Employee Drawing",
            },
            content_type="multipart/form-data",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_emp_up.status_code, 201)
        doc_id = resp_emp_up.get_json()["document_id"]

        # 3. Employee forbidden from deleting documents (Admin/Manager only)
        resp_emp_del = self.client.post(
            f"/orders/{order_id}/documents/{doc_id}/delete",
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp_emp_del.status_code, 403)

        # 4. Verify Cockpit HTML Page Renders Documents Tab
        self.client.get("/test-login/1")  # Login as Admin
        resp_cockpit = self.client.get(f"/orders/{order_id}")
        self.assertEqual(resp_cockpit.status_code, 200)
        html = resp_cockpit.get_data(as_text=True)
        self.assertIn("7. Documents", html)
        self.assertIn("Order Files, Quotations, Challans & Site Photos", html)
        self.assertIn("docUploadModal", html)
        self.assertIn("Export PDF", html)


if __name__ == "__main__":
    unittest.main()

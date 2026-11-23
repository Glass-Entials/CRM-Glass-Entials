"""
tests/test_oms_master_order_creation.py
Comprehensive Test Suite for OMS Master Order Creation Redesign:
1. Customer Search API (GET /orders/api/customers/search)
2. Customer Detail API (GET /orders/api/customers/<id>)
3. Inline Customer Creation API (POST /orders/api/customers/inline)
4. Vendor Search API (GET /orders/api/vendors/search)
5. Vendor Detail API & RBAC Banking Masking (GET /orders/api/vendors/<id>)
6. Inline Vendor Creation API (POST /orders/api/vendors/inline)
7. Full Master Order Creation with Customer Overrides, Multi-Vendors, and Line Item Allocations
8. Revision 1 Immutable Snapshot Verification
9. Draft vs Confirmed Actions
10. Tenant Isolation Invariants
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
    UserRole,
    Organization,
    Customer,
    Lead,
    Vendor,
    VendorCategory,
    Product,
    Employee,
    OrderMaster,
    OrderItem,
    OrderItemVendorAllocation,
    OrderRevision,
    MasterOrderStatus,
    OrderItemAllocationStatus,
)
from services.oms import (
    OrderMasterService,
    OMSError,
    OMSValidationError,
    OMSTenantIsolationError,
)
from routes.orders import orders_bp


class OMSMasterOrderCreationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask("oms_master_order_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="oms-master-creation-test-secret",
        )
        db.init_app(cls.app)

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

        # Seed Organizations
        self.org1 = Organization(id=1, name="GlassEntials Premier", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Competitor Infra", unique_code="ORG002")
        db.session.add_all([self.org1, self.org2])
        db.session.commit()

        # Seed Users: Admin, Employee for Org1; Admin for Org2
        self.admin_user = User(
            id=1, username="admin1", password="hash_password", email="admin1@example.com",
            role=UserRole.ADMIN, organization_id=1
        )
        self.staff_user = User(
            id=2, username="staff1", password="hash_password", email="staff1@example.com",
            role=UserRole.EMPLOYEE, organization_id=1
        )
        self.org2_user = User(
            id=3, username="org2admin", password="hash_password", email="org2admin@example.com",
            role=UserRole.ADMIN, organization_id=2
        )
        db.session.add_all([self.admin_user, self.staff_user, self.org2_user])
        db.session.commit()

        # Seed Employees
        self.emp1 = Employee(id=1, user_id=1, name="Alice Admin", email="admin1@example.com", organization_id=1)
        self.emp2 = Employee(id=2, user_id=2, name="Bob Staff", email="staff1@example.com", organization_id=1)
        self.emp_org2 = Employee(id=3, user_id=3, name="Charlie Rival", email="org2admin@example.com", organization_id=2)
        db.session.add_all([self.emp1, self.emp2, self.emp_org2])

        # Seed Vendor Category
        self.cat_glass = VendorCategory(id=1, name="Architectural Glass", organization_id=1)
        self.cat_hardware = VendorCategory(id=2, name="Hardware & Fittings", organization_id=1)
        db.session.add_all([self.cat_glass, self.cat_hardware])

        # Seed Customers
        self.cust1 = Customer(
            id=1, name="Horizon Heights", company="Horizon Developers",
            email="horizon@example.com", phone_number="9876543210",
            gst_number="27ABCDE1234F1Z5", address="101 Marine Drive",
            city="Mumbai", state="Maharashtra", pincode="400020",
            organization_id=1, created_by=1
        )
        self.cust_org2 = Customer(
            id=2, name="Skyline Towers", company="Skyline Corp",
            email="skyline@example.com", phone_number="9112233445",
            organization_id=2, created_by=3
        )
        db.session.add_all([self.cust1, self.cust_org2])

        # Seed Leads
        self.lead1 = Lead(
            id=1, name="Apex Skyline Glass", company="Apex Skyline Ltd",
            phone_number="9123456789", email="contact@apexskyline.com",
            address="Plot 55, MIDC Industrial Area", city="Navi Mumbai", state="Maharashtra",
            pincode="400705", gst_number="27AAACA9999Z1Z0", organization_id=1, is_deleted=False, created_by=1
        )
        self.lead_converted = Lead(
            id=2, name="Horizon Heights Lead", company="Horizon Old",
            phone_number="9876500000", email="oldhorizon@example.com",
            organization_id=1, is_deleted=False, created_by=1
        )
        self.cust1.lead_id = 2  # Direct conversion link
        self.lead_phone_match = Lead(
            id=3, name="Mobile Match Lead", company="Match Co",
            phone_number="+91 9876543210", email="unrelated@example.com",
            organization_id=1, is_deleted=False, created_by=1
        )
        self.lead_email_match = Lead(
            id=4, name="Email Match Lead", company="Mail Co",
            phone_number="9999988888", email=" HORIZON@EXAMPLE.COM ",
            organization_id=1, is_deleted=False, created_by=1
        )
        self.lead_name_only = Lead(
            id=5, name="Horizon Heights", company="Different Entity",
            phone_number="8888877777", email="totallydifferent@example.com",
            organization_id=1, is_deleted=False, created_by=1
        )
        self.lead_org2 = Lead(
            id=6, name="Org 2 Competitor Lead", company="Org2 Co",
            phone_number="9777766666", email="org2lead@example.com",
            organization_id=2, is_deleted=False, created_by=3
        )
        db.session.add_all([
            self.lead1, self.lead_converted, self.lead_phone_match,
            self.lead_email_match, self.lead_name_only, self.lead_org2
        ])

        # Seed Vendors
        self.vend1 = Vendor(
            id=1, company_name="Saint-Gobain India", code="VEN-00001",
            contact_person="Ramesh Sharma", phone="9820011223", email="orders@saint-gobain.in",
            gstin="27AAACS1234A1Z1", city="Mumbai", category_id=1,
            bank_name="HDFC Bank", bank_account_no="5010022334455", bank_ifsc="HDFC0000123",
            organization_id=1, created_by=1
        )
        self.vend2 = Vendor(
            id=2, company_name="Dorma Kaba Architectural", code="VEN-00002",
            contact_person="Anita Verma", phone="9820099887", email="sales@dorma.in",
            gstin="27AAACD9876B1Z2", city="Pune", category_id=2,
            bank_name="ICICI Bank", bank_account_no="001122334455", bank_ifsc="ICIC0000456",
            organization_id=1, created_by=1
        )
        self.vend_org2 = Vendor(
            id=3, company_name="External Glass Ltd", code="VEN-00003",
            organization_id=2, created_by=3
        )
        db.session.add_all([self.vend1, self.vend2, self.vend_org2])

        # Seed Products
        self.prod1 = Product(id=1, name="12mm Toughened Clear Glass", organization_id=1)
        self.prod2 = Product(id=2, name="SS Glass Patch Fitting Top", organization_id=1)
        db.session.add_all([self.prod1, self.prod2])

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
        from flask import g
        g._login_user = db.session.get(User, int(user_id))

    # --------------------------------------------------------------------------
    # 1. Customer Search API
    # --------------------------------------------------------------------------
    def test_01_customer_search_api(self):
        """Test GET /orders/api/customers/search matches query and enforces tenant isolation."""
        self._login(1)

        # Search by name substring
        res = self.client.get("/orders/api/customers/search?q=Horizon")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["name"], "Horizon Heights")

        # Search by phone substring
        res = self.client.get("/orders/api/customers/search?q=98765")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 1)

        # Tenant isolation: Org1 cannot search or see Org2's Skyline customer
        res = self.client.get("/orders/api/customers/search?q=Skyline")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 0)

    # --------------------------------------------------------------------------
    # 2. Customer Detail API
    # --------------------------------------------------------------------------
    def test_02_customer_detail_api(self):
        """Test GET /orders/api/customers/<id> returns profile and enforces tenant isolation."""
        self._login(1)

        # Own customer detail
        res = self.client.get(f"/orders/api/customers/{self.cust1.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        cust = data["customer"]
        self.assertEqual(cust["name"], "Horizon Heights")
        self.assertEqual(cust["phone_number"], "9876543210")
        self.assertEqual(cust["gst_number"], "27ABCDE1234F1Z5")
        self.assertEqual(cust["billing_address"], "101 Marine Drive")

        # Other org's customer -> 404
        res = self.client.get(f"/orders/api/customers/{self.cust_org2.id}")
        self.assertEqual(res.status_code, 404)

    # --------------------------------------------------------------------------
    # 3. Inline Customer Creation API
    # --------------------------------------------------------------------------
    def test_03_customer_inline_creation_validation_and_duplicates(self):
        """Test POST /orders/api/customers/inline validation, duplicate detection, and auto-creation."""
        self._login(1)

        # Missing required name
        res = self.client.post("/orders/api/customers/inline", json={
            "phone_number": "9988776655"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("Customer name is required", res.get_json()["message"])

        # Invalid phone format
        res = self.client.post("/orders/api/customers/inline", json={
            "name": "Invalid Phone User",
            "phone_number": "1234"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("10-digit mobile number", res.get_json()["message"])

        # Invalid email format
        res = self.client.post("/orders/api/customers/inline", json={
            "name": "Invalid Email User",
            "phone_number": "9988776655",
            "email": "not-an-email"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("valid email address", res.get_json()["message"])

        # Duplicate phone within same tenant (cust1 phone is 9876543210)
        res = self.client.post("/orders/api/customers/inline", json={
            "name": "Duplicate Phone Customer",
            "phone_number": "9876543210"
        })
        self.assertEqual(res.status_code, 409)
        self.assertIn("already exists", res.get_json()["message"])

        # Successful inline creation with complete details
        payload = {
            "name": "Apex Towers Pvt Ltd",
            "company": "Apex Group",
            "phone_number": "9123456780",
            "email": "procurement@apextowers.com",
            "gst_number": "27AABCA1122C1Z8",
            "pan_number": "AABCA1122C",
            "address": "404 Business Bay, BKC",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pincode": "400051",
            "country": "India",
            "notes": "Premium commercial client."
        }
        res = self.client.post("/orders/api/customers/inline", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        new_cust = data["customer"]
        self.assertEqual(new_cust["name"], "Apex Towers Pvt Ltd")
        self.assertEqual(new_cust["phone_number"], "9123456780")
        self.assertEqual(new_cust["billing_address"], "404 Business Bay, BKC")

        # Verify persisted in database under org1
        saved = Customer.query.filter_by(id=new_cust["id"], organization_id=1).first()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.company, "Apex Group")

    # --------------------------------------------------------------------------
    # 4. Vendor Search API
    # --------------------------------------------------------------------------
    def test_04_vendor_search_api(self):
        """Test GET /orders/api/vendors/search matches query and enforces tenant isolation."""
        self._login(1)

        # Search by company name
        res = self.client.get("/orders/api/vendors/search?q=Saint")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["company_name"], "Saint-Gobain India")

        # Search by code
        res = self.client.get("/orders/api/vendors/search?q=VEN-00002")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["company_name"], "Dorma Kaba Architectural")

        # Tenant isolation: Org1 cannot see Org2's External Glass Ltd
        res = self.client.get("/orders/api/vendors/search?q=External")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 0)

    # --------------------------------------------------------------------------
    # 5. Vendor Detail API & Banking RBAC Masking
    # --------------------------------------------------------------------------
    def test_05_vendor_detail_api_rbac_masking(self):
        """Test GET /orders/api/vendors/<id> masks banking details for non-privileged staff."""
        # Admin access (privileged)
        self._login(1)
        res = self.client.get(f"/orders/api/vendors/{self.vend1.id}")
        self.assertEqual(res.status_code, 200)
        vend_data = res.get_json()["vendor"]
        self.assertEqual(vend_data["bank_name"], "HDFC Bank")
        self.assertEqual(vend_data["bank_account_no"], "5010022334455")
        self.assertEqual(vend_data["bank_ifsc"], "HDFC0000123")

        # Employee/Staff access (non-privileged) -> banking masked
        self._login(2)
        res = self.client.get(f"/orders/api/vendors/{self.vend1.id}")
        self.assertEqual(res.status_code, 200)
        vend_data = res.get_json()["vendor"]
        self.assertEqual(vend_data["bank_name"], "••••••••")
        self.assertEqual(vend_data["bank_account_no"], "••••••••")
        self.assertEqual(vend_data["bank_ifsc"], "••••••••")

        # Tenant isolation: 404 for foreign vendor
        res = self.client.get(f"/orders/api/vendors/{self.vend_org2.id}")
        self.assertEqual(res.status_code, 404)

    # --------------------------------------------------------------------------
    # 6. Inline Vendor Creation API
    # --------------------------------------------------------------------------
    def test_06_vendor_inline_creation_and_auto_code(self):
        """Test POST /orders/api/vendors/inline auto-generates code and protects banking fields."""
        # Non-privileged staff creating vendor: bank details ignored
        self._login(2)
        payload = {
            "company_name": "ModiGuard Glass Ltd",
            "contact_person": "Vikas Modi",
            "phone": "9811223344",
            "email": "vikas@modiguard.in",
            "gstin": "27AAACM5544K1Z3",
            "city": "Vadodara",
            "bank_name": "HDFC Bank",
            "bank_account_no": "999988887777",
            "bank_ifsc": "HDFC0000999"
        }
        res = self.client.post("/orders/api/vendors/inline", json=payload)
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        v_info = data["vendor"]
        self.assertEqual(v_info["company_name"], "ModiGuard Glass Ltd")
        self.assertTrue(v_info["code"].startswith("VEN-"))

        # Verify in DB that non-admin did not persist bank details
        saved_v = Vendor.query.filter_by(id=v_info["id"]).first()
        self.assertIsNone(saved_v.bank_account_no)

        # Admin creating vendor: bank details allowed
        self._login(1)
        payload_admin = {
            "company_name": "Ozone Hardware Overseas",
            "contact_person": "Rajesh Gupta",
            "phone": "9811998877",
            "email": "rajesh@ozone-india.com",
            "bank_name": "Axis Bank",
            "bank_account_no": "9180200334455",
            "bank_ifsc": "UTIB0000123"
        }
        res = self.client.post("/orders/api/vendors/inline", json=payload_admin)
        self.assertEqual(res.status_code, 201)
        admin_v_id = res.get_json()["vendor"]["id"]
        saved_admin_v = Vendor.query.filter_by(id=admin_v_id).first()
        self.assertEqual(saved_admin_v.bank_account_no, "9180200334455")

    # --------------------------------------------------------------------------
    # 7. Full Master Order Creation with All 7 Sections & Revision 1 Snapshot
    # --------------------------------------------------------------------------
    def test_07_full_master_order_creation_workflow(self):
        """Test complete order creation with customer overrides, multi-vendors, line allocations, and Revision 1."""
        self._login(1)

        payload = {
            # Section 01: Customer Selection & Address Overrides
            "customer_id": self.cust1.id,
            "billing_address": "Suite 800, Express Towers, Nariman Point, Mumbai",
            "shipping_address": "Site Plot 42, Bandra-Kurla Complex, Mumbai",
            "site_address": "Site Plot 42, Bandra-Kurla Complex, Mumbai",

            # Section 02: Selected Vendors (Multi-vendor association)
            "selected_vendor_ids": [self.vend1.id, self.vend2.id],

            # Section 03: Master Order Information
            "order_title": "BKC Tower Commercial Glazing Phase 1",
            "order_date": "2026-12-01",
            "expected_delivery_date": "2026-12-20",
            "expected_fitting_date": "2026-12-25",
            "order_priority": "urgent",
            "order_description": "Full facade structural glazing and spider fittings.",

            # Section 04: Line Items with vendor allocation
            "items": [
                {
                    "item_description": "12mm Toughened Clear Glass 2400x1200",
                    "product_id": self.prod1.id,
                    "quantity": 50,
                    "unit_price": 2500.00,
                    "tax_rate": 18.00,
                    "discount_amount": 500.00,
                    "specifications": "SKU: GLS-12MM-CLR | UOM: SQFT | Beveled polished edges",
                    "allocated_vendor_id": self.vend1.id,
                },
                {
                    "item_description": "SS 304 Spider Fitting 4-Way Assembly",
                    "product_id": self.prod2.id,
                    "quantity": 100,
                    "unit_price": 850.00,
                    "tax_rate": 18.00,
                    "discount_amount": 0.00,
                    "specifications": "SKU: FIT-SPD-304 | UOM: NOS | Satin finish",
                    "allocated_vendor_id": self.vend2.id,
                }
            ],

            # Section 05: Commercial Details
            "commercial_details": {
                "currency": "INR",
                "payment_terms": "net_30",
                "advance_percentage": 25,
                "freight_charges": 15000.00,
                "additional_charges": 5000.00,
                "gst_treatment": "registered_business",
            },

            # Section 06: Delivery & Installation Requirements
            "delivery_details": {
                "transport_mode": "Dedicated Flatbed Truck",
                "site_contact_person": "Ajay Site Foreman",
                "site_contact_phone": "9820055443",
                "delivery_instructions": "Unload at Gate 3 with boom crane.",
                "expected_delivery_date": "2026-12-20",
            },
            "installation_details": {
                "installation_required": True,
                "expected_fitting_date": "2026-12-25",
                "site_supervisor": "Amit Engineer",
                "fitting_instructions": "Wear safety harnesses. Night installation only.",
            },

            # Section 07 / Actions
            "action_type": "confirm_order",
        }

        res = self.client.post("/orders/", json=payload, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        order_id = data["order_id"]
        order_number = data["master_order_number"]
        self.assertTrue(order_number.startswith("ORD-"))

        # Fetch and verify OrderMaster in database
        order = OrderMaster.query.get(order_id)
        self.assertIsNotNone(order)
        self.assertEqual(order.status, MasterOrderStatus.CONFIRMED.value)
        self.assertEqual(order.billing_address, "Suite 800, Express Towers, Nariman Point, Mumbai")
        self.assertEqual(order.shipping_address, "Site Plot 42, Bandra-Kurla Complex, Mumbai")
        self.assertEqual(order.site_address, "Site Plot 42, Bandra-Kurla Complex, Mumbai")

        # Verify Line Items
        items = OrderItem.query.filter_by(order_id=order_id).all()
        self.assertEqual(len(items), 2)

        # Verify Vendor Allocations
        allocations = OrderItemVendorAllocation.query.filter_by(order_id=order_id).all()
        self.assertEqual(len(allocations), 2)
        alloc_vend_ids = {a.vendor_id for a in allocations}
        self.assertIn(self.vend1.id, alloc_vend_ids)
        self.assertIn(self.vend2.id, alloc_vend_ids)

        # Verify Immutable OrderRevision (Revision 1) snapshot
        revisions = OrderRevision.query.filter_by(order_id=order_id).all()
        self.assertEqual(len(revisions), 1)
        rev1 = revisions[0]
        self.assertEqual(rev1.revision_number, 1)

        snapshot = json.loads(rev1.snapshot_json)
        self.assertEqual(snapshot["order_number"], order_number)
        self.assertEqual(snapshot["status"], MasterOrderStatus.CONFIRMED.value)

        # Verify customer snapshot in revision
        cust_snap = snapshot["customer_snapshot"]
        self.assertEqual(cust_snap["id"], self.cust1.id)
        self.assertEqual(cust_snap["name"], "Horizon Heights")
        self.assertEqual(cust_snap["billing_address"], "Suite 800, Express Towers, Nariman Point, Mumbai")

        # Verify selected vendors in revision
        vends_snap = snapshot["selected_vendors"]
        self.assertEqual(len(vends_snap), 2)
        v_names = [v["company_name"] for v in vends_snap]
        self.assertIn("Saint-Gobain India", v_names)
        self.assertIn("Dorma Kaba Architectural", v_names)

        # Verify commercial & delivery details in revision
        self.assertEqual(snapshot["commercial_details"]["currency"], "INR")
        self.assertEqual(snapshot["delivery_details"]["transport_mode"], "Dedicated Flatbed Truck")
        self.assertTrue(snapshot["installation_details"]["installation_required"])

    # --------------------------------------------------------------------------
    # 8. Draft vs Confirmed Action Type
    # --------------------------------------------------------------------------
    def test_08_order_creation_draft_action(self):
        """Test creating order with action_type='save_draft' creates DRAFT order."""
        self._login(1)
        payload = {
            "customer_id": self.cust1.id,
            "order_title": "Draft Glass Proposal",
            "items": [
                {"item_description": "Sample Panel", "quantity": 1, "unit_price": 500.00}
            ],
            "action_type": "save_draft",
        }
        res = self.client.post("/orders/", json=payload, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 201)
        order_id = res.get_json()["order_id"]
        order = OrderMaster.query.get(order_id)
        self.assertEqual(order.status, MasterOrderStatus.DRAFT.value)

    # --------------------------------------------------------------------------
    # 9. Tenant Isolation Guardrails
    # --------------------------------------------------------------------------
    def test_09_tenant_isolation_guards(self):
        """Test attempting to create order with customer or vendor belonging to another tenant fails."""
        self._login(1)

        # Attempting with another tenant's customer
        payload_bad_cust = {
            "customer_id": self.cust_org2.id,
            "order_title": "Cross Tenant Order",
            "items": [{"item_description": "Item", "quantity": 1, "unit_price": 100}],
        }
        res = self.client.post("/orders/", json=payload_bad_cust, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("not found in organization", res.get_json()["message"])

        # Attempting with another tenant's vendor in selected_vendor_ids
        payload_bad_vend = {
            "customer_id": self.cust1.id,
            "order_title": "Cross Tenant Vendor Order",
            "selected_vendor_ids": [self.vend_org2.id],
            "items": [{"item_description": "Item", "quantity": 1, "unit_price": 100}],
        }
        res = self.client.post("/orders/", json=payload_bad_vend, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("does not belong to organization", res.get_json()["message"])

    # --------------------------------------------------------------------------
    # 10. Lead Search API
    # --------------------------------------------------------------------------
    def test_10_lead_search_api(self):
        """Test GET /orders/api/leads/search matches query and enforces tenant isolation."""
        self._login(1)

        # Search by lead name
        res = self.client.get("/orders/api/leads/search?q=Apex")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["name"], "Apex Skyline Glass")

        # Search by phone substring
        res = self.client.get("/orders/api/leads/search?q=91234")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 1)

        # Tenant isolation: Org 1 cannot find Org 2 lead
        res = self.client.get("/orders/api/leads/search?q=Competitor")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(len(data["results"]), 0)

    # --------------------------------------------------------------------------
    # 11. Lead Detail API and Duplicate Customer Detection Priorities
    # --------------------------------------------------------------------------
    def test_11_lead_detail_api_and_duplicate_detection_priorities(self):
        """Test GET /orders/api/leads/<id> and customer duplicate detection priorities."""
        self._login(1)

        # 1. Plain lead (no customer link)
        res = self.client.get(f"/orders/api/leads/{self.lead1.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        self.assertFalse(data["is_already_customer"])
        self.assertIsNone(data["associated_customer"])

        # 2. Priority 1: Converted Lead (cust1.lead_id == lead_converted.id)
        res = self.client.get(f"/orders/api/leads/{self.lead_converted.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["is_already_customer"])
        self.assertIsNotNone(data["associated_customer"])
        self.assertEqual(data["associated_customer"]["id"], self.cust1.id)
        self.assertEqual(data["associated_customer"]["match_reason"], "Direct Lead Conversion Record")

        # 3. Priority 2: Mobile match (last 10 digits match cust1)
        res = self.client.get(f"/orders/api/leads/{self.lead_phone_match.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["is_already_customer"])
        self.assertEqual(data["associated_customer"]["id"], self.cust1.id)

        # 4. Priority 3: Email match (normalized lowercase match cust1)
        res = self.client.get(f"/orders/api/leads/{self.lead_email_match.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["is_already_customer"])
        self.assertEqual(data["associated_customer"]["id"], self.cust1.id)

        # 5. Name only match: must NOT trigger customer detection
        res = self.client.get(f"/orders/api/leads/{self.lead_name_only.id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertFalse(data["is_already_customer"])
        self.assertIsNone(data["associated_customer"])

        # 6. Tenant isolation: Org 1 user fetching Org 2 lead -> 404
        res = self.client.get(f"/orders/api/leads/{self.lead_org2.id}")
        self.assertEqual(res.status_code, 404)

    # --------------------------------------------------------------------------
    # 12. Create Order in Lead Mode (Plain Lead)
    # --------------------------------------------------------------------------
    def test_12_create_order_lead_mode_plain(self):
        """Test creating an order in Lead mode for a lead without customer link."""
        self._login(1)
        payload = {
            "entity_mode": "lead",
            "lead_id": self.lead1.id,
            "order_title": "Apex Commercial Facade Order",
            "items": [
                {
                    "item_description": "Curtain Wall Glass 24mm DGU",
                    "quantity": 10,
                    "unit_price": 4500.00,
                }
            ],
            "action_type": "confirm_order",
        }
        res = self.client.post("/orders/", json=payload, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 201)
        data = res.get_json()
        self.assertTrue(data["success"])
        order_id = data["order_id"]

        order = db.session.get(OrderMaster, order_id)
        self.assertIsNotNone(order)
        self.assertEqual(order.lead_id, self.lead1.id)
        self.assertIsNone(order.customer_id)
        self.assertEqual(order.client_type, "LEAD")
        self.assertEqual(order.client_name, self.lead1.name)

        # Verify revision snapshot
        rev = OrderRevision.query.filter_by(order_id=order_id).first()
        self.assertIsNotNone(rev)
        snap = json.loads(rev.snapshot_json)
        self.assertEqual(snap["customer_snapshot"]["client_type"], "LEAD")
        self.assertTrue(snap["customer_snapshot"]["is_lead"])
        self.assertEqual(snap["customer_snapshot"]["name"], self.lead1.name)

    # --------------------------------------------------------------------------
    # 13. Create Order in Lead Mode with Customer Match & Confirmation Override
    # --------------------------------------------------------------------------
    def test_13_create_order_lead_mode_with_customer_match_and_confirmation(self):
        """Test creating an order for a lead that matches a customer: requires confirmation override."""
        self._login(1)

        # Without confirmation override -> 400 validation error
        payload_unconfirmed = {
            "entity_mode": "lead",
            "lead_id": self.lead_phone_match.id,
            "order_title": "Unconfirmed Override Order",
            "items": [{"item_description": "Glass", "quantity": 1, "unit_price": 100}],
            "confirmed_lead_override": False,
        }
        res = self.client.post("/orders/", json=payload_unconfirmed, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("already registered as a customer", res.get_json()["message"])

        # With confirmed_lead_override = True -> succeeds!
        payload_confirmed = {
            "entity_mode": "lead",
            "lead_id": self.lead_phone_match.id,
            "order_title": "Confirmed Lead Override Order",
            "items": [{"item_description": "Glass", "quantity": 1, "unit_price": 100}],
            "confirmed_lead_override": True,
            "action_type": "confirm_order",
        }
        cust_count_before = Customer.query.count()
        res = self.client.post("/orders/", json=payload_confirmed, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 201)
        order_id = res.get_json()["order_id"]

        order = db.session.get(OrderMaster, order_id)
        self.assertIsNotNone(order)
        self.assertEqual(order.lead_id, self.lead_phone_match.id)
        self.assertEqual(order.customer_id, self.cust1.id)
        self.assertEqual(order.client_type, "LEAD_CUSTOMER")
        self.assertEqual(order.client_name, self.lead_phone_match.name)

        # Verify no duplicate customer was created
        cust_count_after = Customer.query.count()
        self.assertEqual(cust_count_before, cust_count_after)

    # --------------------------------------------------------------------------
    # 14. Lead Mode Draft and Tenant Isolation Guards
    # --------------------------------------------------------------------------
    def test_14_lead_mode_draft_and_tenant_guards(self):
        """Test Lead mode draft action and tenant isolation protections."""
        self._login(1)

        # Draft action with lead
        payload_draft = {
            "entity_mode": "lead",
            "lead_id": self.lead1.id,
            "order_title": "Draft Lead Order",
            "items": [{"item_description": "Draft Item", "quantity": 2, "unit_price": 200}],
            "action_type": "save_draft",
        }
        res = self.client.post("/orders/", json=payload_draft, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 201)
        order_id = res.get_json()["order_id"]
        order = db.session.get(OrderMaster, order_id)
        self.assertEqual(order.status, MasterOrderStatus.DRAFT.value)
        self.assertEqual(order.lead_id, self.lead1.id)

        # Cross-tenant lead ID -> 403 Forbidden
        payload_cross_tenant = {
            "entity_mode": "lead",
            "lead_id": self.lead_org2.id,
            "order_title": "Hacked Cross-Tenant Order",
            "items": [{"item_description": "Draft Item", "quantity": 1, "unit_price": 100}],
        }
        res = self.client.post("/orders/", json=payload_cross_tenant, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 403)
        self.assertIn("not found in organization", res.get_json()["message"])

        # Missing both customer and lead -> 400 Bad Request
        payload_missing_client = {
            "order_title": "Missing Client Order",
            "items": [{"item_description": "Item", "quantity": 1, "unit_price": 100}],
        }
        res = self.client.post("/orders/", json=payload_missing_client, headers={"Accept": "application/json"})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()

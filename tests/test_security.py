import pytest
from model import db, User, Employee, UserRole, Customer, Lead, LeadSource, LeadStatus
from tests.conftest import make_org, make_user
from io import BytesIO

class TestSecurity:
    def test_idor_customer_access(self, client, db, two_orgs):
        """Test Direct Object Reference (IDOR) protection for customers."""
        org_a, org_b, user_a, user_b, emp_a, emp_b, cust_b, lead_b = two_orgs

        # Login as User A
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_a.id)
            sess["_fresh"] = True

        # Attempt to access Org B's customer directly via URL
        resp_view = client.get(f"/view-customer/{cust_b.id}")
        assert resp_view.status_code == 404

        resp_edit = client.post(f"/edit-customer/{cust_b.id}", data={"name": "Hacked"})
        assert resp_edit.status_code == 404

    def test_role_escalation_attempt(self, client, db, seed_org_user):
        """Test that a regular employee cannot access admin areas or change their own role."""
        org, admin_user, admin_emp = seed_org_user
        # Create normal employee
        emp_user, emp_emp = make_user(org, "emp2@test.com", "emp2", UserRole.EMPLOYEE, "Pass123!")
        db.session.commit()

        # Login as normal employee
        with client.session_transaction() as sess:
            sess["_user_id"] = str(emp_user.id)
            sess["_fresh"] = True

        # Try to access Employee list
        resp_list = client.get("/employee")
        assert resp_list.status_code in [403, 302]

        # Try to POST to add-employee to create an admin
        resp_add = client.post("/add-employee", data={
            "name": "Hacker",
            "email": "hacker@test.com",
            "phone_number": "0000000000",
            "position": "Staff",
            "role": "admin"
        })
        assert resp_add.status_code in [403, 302]

    def test_file_upload_validation(self, logged_in_client, seed_org_user):
        """Test that only allowed file types can be uploaded to customers."""
        org, user, emp = seed_org_user
        cust = Customer(name="Upload Test", email="upload@test.com", phone_number="1234567890", organization_id=org.id, created_by=emp.id)
        db.session.add(cust)
        db.session.commit()

        # Try uploading a malicious PHP file
        data = {
            "document": (BytesIO(b"<?php echo 'hacked'; ?>"), "shell.php")
        }
        resp = logged_in_client.post(
            f"/customer-document/upload/{cust.id}", 
            data=data, 
            content_type="multipart/form-data",
            follow_redirects=True
        )
        assert resp.status_code == 400

    def test_authentication_bypass(self, client):
        """Test that endpoints are not accessible without login."""
        resp = client.get("/customers")
        # Should redirect to login
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("Location", "")

        resp2 = client.get("/leads")
        assert resp2.status_code == 302

import pytest
from model import db, Employee, UserRole
from tests.conftest import make_user

class TestEmployeeManagement:
    def test_employee_list_renders(self, logged_in_client):
        resp = logged_in_client.get("/employee")
        assert resp.status_code == 200
        assert b"Employee" in resp.data

    def test_admin_can_add_employee(self, logged_in_client, seed_org_user):
        org, user, emp = seed_org_user
        resp = logged_in_client.post(
            "/add-employee",
            data={
                "name": "New Employee",
                "email": "new.emp@test.com",
                "phone_number": "1231231234",
                "position": "Staff"
            },
            follow_redirects=True
        )
        assert resp.status_code == 200

        # Verify employee is in DB
        new_emp = Employee.query.filter_by(email="new.emp@test.com").first()
        assert new_emp is not None
        assert new_emp.organization_id == org.id

    def test_add_duplicate_employee_prevented(self, logged_in_client, seed_org_user):
        org, user, emp = seed_org_user
        # User already exists as admin@test.com
        resp = logged_in_client.post(
            "/add-employee",
            data={
                "name": "Duplicate",
                "email": "admin@test.com",  # Duplicate email
                "phone_number": "1112223333",
                "position": "Staff"
            },
            follow_redirects=True
        )
        assert resp.status_code == 200
        assert b"Email already registered" in resp.data

    def test_employee_cannot_add_employee(self, client, db, seed_org_user):
        org, user, emp = seed_org_user
        # Create regular employee
        user_emp, emp_emp = make_user(org, "emp@test.com", "emp", UserRole.EMPLOYEE, "Pass123!")
        db.session.commit()

        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_emp.id)
            sess["_fresh"] = True

        resp = client.get("/add-employee")
        # Should be forbidden or redirect
        assert resp.status_code in [403, 302]

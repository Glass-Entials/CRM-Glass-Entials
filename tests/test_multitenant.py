"""
Tests for multi-tenant isolation — ensures that Org A cannot access Org B's
customers, leads, tasks, expenses, or documents.
"""
import pytest
from model import (
    db, Organization, User, Employee, UserRole,
    Customer, Lead, Expense, LeadStatus, LeadSource,
    ExpenseCategory, ExpenseStatus,
)
from werkzeug.security import generate_password_hash
from tests.conftest import make_org, make_user


# ─── Fixtures: two isolated organizations ─────────────────────────────────────


class TestCustomerIsolation:
    def test_org_a_cannot_see_org_b_customers(self, client, two_orgs, app):
        _, _, user_a, _, _, _, cust_b, _ = two_orgs

        # Authenticate as Org A admin
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_a.id)
            sess["_fresh"] = True

        resp = client.get("/customers")
        assert resp.status_code == 200
        # Org B's customer name must NOT appear in Org A's list
        assert b"Org B Customer" not in resp.data

    def test_org_a_cannot_access_org_b_customer_directly(self, client, two_orgs):
        _, _, user_a, _, _, _, cust_b, _ = two_orgs
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_a.id)
            sess["_fresh"] = True

        # Direct access to Org B's customer ID should return 404
        resp = client.get(f"/customers/view/{cust_b.id}")
        assert resp.status_code == 404


class TestLeadIsolation:
    def test_org_a_cannot_see_org_b_leads(self, client, two_orgs):
        _, _, user_a, _, _, _, _, lead_b = two_orgs
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_a.id)
            sess["_fresh"] = True

        resp = client.get("/leads")
        assert resp.status_code == 200
        assert b"Org B Lead" not in resp.data

    def test_org_a_cannot_access_org_b_lead_directly(self, client, two_orgs):
        _, _, user_a, _, _, _, _, lead_b = two_orgs
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_a.id)
            sess["_fresh"] = True

        resp = client.get(f"/leads/view/{lead_b.id}")
        assert resp.status_code == 404


class TestExpenseIsolation:
    def test_employee_only_sees_own_org_expenses(self, client, two_orgs, db):
        org_a, org_b, user_a, _, emp_a, emp_b, _, _ = two_orgs

        # Add expense in Org B
        exp_b = Expense(
            title="Org B Only Expense",
            amount=100.0,
            category=ExpenseCategory.OTHER,
            organization_id=org_b.id,
            employee_id=emp_b.id,
        )
        db.session.add(exp_b)
        db.session.commit()

        # Login as Org A admin
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_a.id)
            sess["_fresh"] = True

        resp = client.get("/expenses/")
        assert resp.status_code == 200
        assert b"Org B Only Expense" not in resp.data


class TestLeadUniqueConstraint:
    def test_same_email_different_orgs_allowed(self, db, two_orgs):
        """Two orgs can have leads with the same email (per-org unique)."""
        org_a, org_b, _, _, emp_a, emp_b, _, _ = two_orgs
        lead_a = Lead(
            name="Shared Email Lead",
            email="shared@email.com",
            phone_number="4444444441",
            organization_id=org_a.id,
            created_by=emp_a.id,
        )
        lead_b = Lead(
            name="Shared Email Lead B",
            email="shared@email.com",   # Same email, different org — allowed
            phone_number="4444444442",
            organization_id=org_b.id,
            created_by=emp_b.id,
        )
        db.session.add_all([lead_a, lead_b])
        db.session.commit()  # Should not raise IntegrityError

        db.session.delete(lead_a)
        db.session.delete(lead_b)
        db.session.commit()

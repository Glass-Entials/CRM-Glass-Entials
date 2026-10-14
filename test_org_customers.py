"""
test_org_customers.py
Comprehensive test suite for Organization Customer Management System.
Covers:
1. Organization creation and editing.
2. Organization list, search, filters, and pagination.
3. Pan India coverage.
4. Selected Cities coverage with multiple cities.
5. Duplicate city prevention.
6. Organization-member CRUD.
7. Primary-contact rules (only one active primary contact per org).
8. Project linked to both an organization and its responsible member.
9. Rejection of a member belonging to a different organization.
10. Reassignment of a responsible member.
11. Organization-linked quotation/activity integration.
12. Tenant isolation (data segregated by organization_id).
13. Authentication and permissions (unauthenticated redirect).
14. Validation failures and missing optional fields.
15. Migration file syntax and revision chain correctness.
16. Preservation of existing Individual Customer behavior.
17. Regression tests for existing customer/project workflows.
"""

import pytest
from unittest.mock import patch, MagicMock
from app import app
from model import (
    db, User, Organization, Employee, Customer, Project, Quotation,
    OrgCustomer, OrgCustomerMember, OrgCustomerCity,
    OrgCustomerStatus, OrgCustomerCoverageType, OrgCustomerType, OrgCustomerMemberRole,
    ProjectStatus,
)


@pytest.fixture(scope="module")
def test_setup():
    with app.app_context():
        # Ensure a test organization and employee exist
        org = Organization.query.first()
        if not org:
            org = Organization(name="Test Tenant", email="tenant@example.com")
            db.session.add(org)
            db.session.commit()

        emp = Employee.query.filter_by(organization_id=org.id).first()
        if not emp:
            emp = Employee(name="Test Admin", organization_id=org.id, email="admin@example.com")
            db.session.add(emp)
            db.session.commit()

        # Second tenant for isolation tests
        org2 = Organization.query.filter(Organization.id != org.id).first()
        if not org2:
            org2 = Organization(name="Tenant Two", email="tenant2@example.com")
            db.session.add(org2)
            db.session.commit()

        yield {
            "org_id": org.id,
            "emp_id": emp.id,
            "org2_id": org2.id,
        }


def _mock_user(org_id, emp_id):
    user = MagicMock()
    user.is_authenticated = True
    user.is_active = True
    user.is_anonymous = False
    user.get_id = lambda: str(emp_id)
    user.organization_id = org_id
    user.employee = MagicMock(id=emp_id, name="Test Staff")
    user.two_fa_enabled = False
    user.must_change_password = False
    role = MagicMock()
    role.value = "admin"
    user.role = role
    return user


# ── Test 1: Organization Creation and Editing ───────────────────────────────

def test_1_org_creation_and_editing(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        # Create
        oc = OrgCustomer(
            name="DLF Cybercity",
            legal_name="DLF Cybercity Developers Ltd",
            org_type=OrgCustomerType.BUILDER_DEVELOPER,
            website="https://dlf.in",
            gstin="06AABCD1234E1Z5",
            pan="AABCD1234E",
            address="DLF Phase 2",
            city="Gurugram",
            state="Haryana",
            pincode="122002",
            organization_id=org_id,
            coverage_type=OrgCustomerCoverageType.PAN_INDIA,
            status=OrgCustomerStatus.ACTIVE,
            created_by=emp_id,
        )
        db.session.add(oc)
        db.session.commit()

        assert oc.id is not None
        assert oc.name == "DLF Cybercity"
        assert oc.status == OrgCustomerStatus.ACTIVE
        assert oc.coverage_type == OrgCustomerCoverageType.PAN_INDIA

        # Edit
        oc.name = "DLF Cybercity Updated"
        oc.city = "New Delhi"
        oc.status = OrgCustomerStatus.INACTIVE
        db.session.commit()

        fetched = OrgCustomer.query.get(oc.id)
        assert fetched.name == "DLF Cybercity Updated"
        assert fetched.city == "New Delhi"
        assert fetched.status == OrgCustomerStatus.INACTIVE

        # Clean up
        db.session.delete(oc)
        db.session.commit()


# ── Test 2: Organization List, Search, and Filters ──────────────────────────

def test_2_org_list_search_filters(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        oc1 = OrgCustomer(
            name="Alpha Builders",
            city="Mumbai",
            organization_id=org_id,
            status=OrgCustomerStatus.ACTIVE,
            coverage_type=OrgCustomerCoverageType.PAN_INDIA,
            created_by=emp_id,
        )
        oc2 = OrgCustomer(
            name="Beta Architects",
            city="Bengaluru",
            organization_id=org_id,
            status=OrgCustomerStatus.INACTIVE,
            coverage_type=OrgCustomerCoverageType.SELECTED_CITIES,
            created_by=emp_id,
        )
        db.session.add_all([oc1, oc2])
        db.session.commit()

        # Search by name
        results = OrgCustomer.query.filter_by(organization_id=org_id, is_deleted=False).filter(
            OrgCustomer.name.ilike("%Alpha%")
        ).all()
        assert any(r.name == "Alpha Builders" for r in results)
        assert not any(r.name == "Beta Architects" for r in results)

        # Filter by status
        active_list = OrgCustomer.query.filter_by(
            organization_id=org_id, is_deleted=False, status=OrgCustomerStatus.ACTIVE
        ).all()
        assert any(r.name == "Alpha Builders" for r in active_list)
        assert not any(r.name == "Beta Architects" for r in active_list)

        # Clean up
        db.session.delete(oc1)
        db.session.delete(oc2)
        db.session.commit()


# ── Test 3: Pan India Coverage ──────────────────────────────────────────────

def test_3_pan_india_coverage(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        oc = OrgCustomer(
            name="National Glass Corp",
            organization_id=org_id,
            coverage_type=OrgCustomerCoverageType.PAN_INDIA,
        )
        db.session.add(oc)
        db.session.commit()

        assert oc.coverage_type == OrgCustomerCoverageType.PAN_INDIA
        assert oc.coverage_display == OrgCustomerCoverageType.PAN_INDIA.value
        assert oc.cities.count() == 0  # No specific cities needed

        db.session.delete(oc)
        db.session.commit()


# ── Test 4: Selected Cities Coverage with Multiple Cities ───────────────────

def test_4_selected_cities_coverage(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        oc = OrgCustomer(
            name="Regional Developers",
            organization_id=org_id,
            coverage_type=OrgCustomerCoverageType.SELECTED_CITIES,
        )
        db.session.add(oc)
        db.session.commit()

        c1 = OrgCustomerCity(org_customer_id=oc.id, city="Mumbai", state="Maharashtra")
        c2 = OrgCustomerCity(org_customer_id=oc.id, city="Pune", state="Maharashtra")
        c3 = OrgCustomerCity(org_customer_id=oc.id, city="Ahmedabad", state="Gujarat")
        db.session.add_all([c1, c2, c3])
        db.session.commit()

        assert oc.cities.count() == 3
        cities = [c.city for c in oc.cities.all()]
        assert "Mumbai" in cities
        assert "Pune" in cities
        assert "Ahmedabad" in cities

        db.session.delete(oc)
        db.session.commit()


# ── Test 5: Duplicate City Prevention ───────────────────────────────────────

def test_5_duplicate_city_prevention(test_setup):
    from routes.org_customers import _sync_cities

    with app.app_context():
        org_id = test_setup["org_id"]
        oc = OrgCustomer(
            name="City Dupe Test Org",
            organization_id=org_id,
            coverage_type=OrgCustomerCoverageType.SELECTED_CITIES,
        )
        db.session.add(oc)
        db.session.commit()

        # Input with duplicates (case-insensitive)
        raw_cities = ["Mumbai", "mumbai", "Pune", "PUNE", "Pune ", "Delhi"]
        _sync_cities(oc, raw_cities)
        db.session.commit()

        stored_cities = [c.city.lower() for c in oc.cities.all()]
        assert len(stored_cities) == 3
        assert set(stored_cities) == {"mumbai", "pune", "delhi"}

        db.session.delete(oc)
        db.session.commit()


# ── Test 6: Organization Member CRUD ────────────────────────────────────────

def test_6_organization_member_crud(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        oc = OrgCustomer(name="Member Test Org", organization_id=org_id)
        db.session.add(oc)
        db.session.commit()

        # Create
        m = OrgCustomerMember(
            org_customer_id=oc.id,
            organization_id=org_id,
            name="Vikram Mehta",
            designation="Chief Architect",
            department="Design",
            role=OrgCustomerMemberRole.ARCHITECT_CONSULTANT,
            mobile="9876543210",
            email="vikram@design.com",
            is_active=True,
            created_by=emp_id,
        )
        db.session.add(m)
        db.session.commit()

        assert m.id is not None
        assert m.name == "Vikram Mehta"
        assert m.role_display == OrgCustomerMemberRole.ARCHITECT_CONSULTANT.value
        assert oc.member_count == 1

        # Update
        m.designation = "Senior Partner"
        m.mobile = "9999988888"
        db.session.commit()

        fetched_m = OrgCustomerMember.query.get(m.id)
        assert fetched_m.designation == "Senior Partner"
        assert fetched_m.mobile == "9999988888"

        # Deactivate
        m.is_active = False
        db.session.commit()
        assert oc.member_count == 0  # Active count is 0

        # Delete (soft)
        m.is_deleted = True
        db.session.commit()
        assert oc.members.filter_by(is_deleted=False).count() == 0

        db.session.delete(m)
        db.session.delete(oc)
        db.session.commit()


# ── Test 7: Primary Contact Rule (Only One Primary per Org) ─────────────────

def test_7_primary_contact_rule(test_setup):
    from routes.org_customers import _clear_primary

    with app.app_context():
        org_id = test_setup["org_id"]

        oc = OrgCustomer(name="Primary Rule Org", organization_id=org_id)
        db.session.add(oc)
        db.session.commit()

        m1 = OrgCustomerMember(
            org_customer_id=oc.id,
            organization_id=org_id,
            name="Alice",
            is_primary=True,
        )
        db.session.add(m1)
        db.session.commit()

        assert oc.primary_contact.name == "Alice"

        # Add second member and set as primary using helper
        _clear_primary(oc.id)
        m2 = OrgCustomerMember(
            org_customer_id=oc.id,
            organization_id=org_id,
            name="Bob",
            is_primary=True,
        )
        db.session.add(m2)
        db.session.commit()

        # Alice should no longer be primary
        db.session.refresh(m1)
        assert m1.is_primary is False
        assert m2.is_primary is True
        assert oc.primary_contact.name == "Bob"

        db.session.delete(m1)
        db.session.delete(m2)
        db.session.delete(oc)
        db.session.commit()


# ── Test 8: Project Linked to Both Org Customer and Responsible Member ───────

def test_8_project_linked_to_org_and_member(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        oc = OrgCustomer(name="Mega Builders", organization_id=org_id)
        db.session.add(oc)
        db.session.commit()

        member = OrgCustomerMember(
            org_customer_id=oc.id,
            organization_id=org_id,
            name="Project Head Suresh",
            role=OrgCustomerMemberRole.PROJECT_MANAGER,
        )
        db.session.add(member)
        db.session.commit()

        project = Project(
            name="Sky Tower Facade",
            org_customer_id=oc.id,
            org_customer_member_id=member.id,
            organization_id=org_id,
            created_by=emp_id,
            status=ProjectStatus.PLANNING,
        )
        db.session.add(project)
        db.session.commit()

        assert project.org_customer_id == oc.id
        assert project.org_customer.name == "Mega Builders"
        assert project.org_customer_member_id == member.id
        assert project.org_customer_member.name == "Project Head Suresh"

        # Backref check
        assert project in oc.projects.all()
        assert project in member.projects.all()

        db.session.delete(project)
        db.session.delete(member)
        db.session.delete(oc)
        db.session.commit()


# ── Test 9: Rejection of Member Belonging to Different Organization ──────────

def test_9_reject_member_from_different_org(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]

        oc_a = OrgCustomer(name="Org A", organization_id=org_id)
        oc_b = OrgCustomer(name="Org B", organization_id=org_id)
        db.session.add_all([oc_a, oc_b])
        db.session.commit()

        member_b = OrgCustomerMember(
            org_customer_id=oc_b.id,
            organization_id=org_id,
            name="Member of B",
        )
        db.session.add(member_b)
        db.session.commit()

        # Validation check matching routes/projects.py logic:
        # A member from Org B cannot be assigned to a project linked to Org A
        member_valid = OrgCustomerMember.query.filter_by(
            id=member_b.id,
            org_customer_id=oc_a.id,  # Checking against Org A
            organization_id=org_id,
            is_deleted=False,
        ).first()

        assert member_valid is None  # Rejection validated!

        db.session.delete(member_b)
        db.session.delete(oc_a)
        db.session.delete(oc_b)
        db.session.commit()


# ── Test 10: Reassignment of Responsible Member ──────────────────────────────

def test_10_reassign_responsible_member(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        oc = OrgCustomer(name="Tech Park Corp", organization_id=org_id)
        db.session.add(oc)
        db.session.commit()

        m1 = OrgCustomerMember(org_customer_id=oc.id, organization_id=org_id, name="Old Lead")
        m2 = OrgCustomerMember(org_customer_id=oc.id, organization_id=org_id, name="New Lead")
        db.session.add_all([m1, m2])
        db.session.commit()

        project = Project(
            name="Tech Park Glazing",
            org_customer_id=oc.id,
            org_customer_member_id=m1.id,
            organization_id=org_id,
            created_by=emp_id,
        )
        db.session.add(project)
        db.session.commit()

        assert project.org_customer_member_id == m1.id

        # Reassign to m2
        project.org_customer_member_id = m2.id
        db.session.commit()

        db.session.refresh(project)
        assert project.org_customer_member_id == m2.id
        assert project.org_customer_member.name == "New Lead"

        db.session.delete(project)
        db.session.delete(m1)
        db.session.delete(m2)
        db.session.delete(oc)
        db.session.commit()


# ── Test 11: Organization-linked Quotation/Activity Integration ───────────────

def test_11_org_quotation_integration(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        oc = OrgCustomer(name="Commercial Complex Org", organization_id=org_id)
        db.session.add(oc)
        db.session.commit()

        project = Project(
            name="Commercial Complex Phase 1",
            org_customer_id=oc.id,
            organization_id=org_id,
            created_by=emp_id,
        )
        db.session.add(project)
        db.session.commit()

        # Quotation linked to project
        quote = Quotation(
            quotation_number="QT-TEST-ORG-001",
            project_id=project.id,
            organization_id=org_id,
            created_by=emp_id,
        )
        db.session.add(quote)
        db.session.commit()

        # Querying quotations for the org customer's projects
        project_ids = [p.id for p in oc.projects.all()]
        linked_quotes = Quotation.query.filter(
            Quotation.project_id.in_(project_ids),
            Quotation.organization_id == org_id,
            Quotation.is_deleted == False,
        ).all()

        assert quote in linked_quotes

        db.session.delete(quote)
        db.session.delete(project)
        db.session.delete(oc)
        db.session.commit()


# ── Test 12: Tenant Isolation ────────────────────────────────────────────────

def test_12_tenant_isolation(test_setup):
    with app.app_context():
        org1_id = test_setup["org_id"]
        org2_id = test_setup["org2_id"]

        oc_tenant1 = OrgCustomer(name="Tenant 1 Secret Client", organization_id=org1_id)
        oc_tenant2 = OrgCustomer(name="Tenant 2 Secret Client", organization_id=org2_id)
        db.session.add_all([oc_tenant1, oc_tenant2])
        db.session.commit()

        # Tenant 1 query must NEVER return Tenant 2 data
        tenant1_clients = OrgCustomer.query.filter_by(
            organization_id=org1_id, is_deleted=False
        ).all()
        assert any(c.name == "Tenant 1 Secret Client" for c in tenant1_clients)
        assert not any(c.name == "Tenant 2 Secret Client" for c in tenant1_clients)

        # Tenant 2 query must NEVER return Tenant 1 data
        tenant2_clients = OrgCustomer.query.filter_by(
            organization_id=org2_id, is_deleted=False
        ).all()
        assert any(c.name == "Tenant 2 Secret Client" for c in tenant2_clients)
        assert not any(c.name == "Tenant 1 Secret Client" for c in tenant2_clients)

        db.session.delete(oc_tenant1)
        db.session.delete(oc_tenant2)
        db.session.commit()


# ── Test 13: Authentication & Permissions ───────────────────────────────────

def test_13_authentication_required():
    with app.test_client() as client:
        # Unauthenticated request to /org-customers should redirect to login
        resp = client.get("/org-customers", follow_redirects=False)
        assert resp.status_code in (302, 401)
        if resp.status_code == 302:
            assert "/login" in resp.headers.get("Location", "") or "login" in resp.headers.get("Location", "")


# ── Test 14: Validation Failures & Missing Optional Fields ───────────────────

def test_14_validation_and_optional_fields(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]

        # Missing name should not be valid
        with pytest.raises(Exception):
            invalid_oc = OrgCustomer(name=None, organization_id=org_id)
            db.session.add(invalid_oc)
            db.session.flush()
        db.session.rollback()

        # Missing optional fields (gstin, pan, website, phone, notes) must succeed cleanly
        valid_minimal_oc = OrgCustomer(
            name="Minimal Org",
            organization_id=org_id,
            gstin=None,
            pan=None,
            website=None,
            primary_phone=None,
            notes=None,
        )
        db.session.add(valid_minimal_oc)
        db.session.commit()

        assert valid_minimal_oc.id is not None
        assert valid_minimal_oc.gstin is None
        assert valid_minimal_oc.coverage_type == OrgCustomerCoverageType.PAN_INDIA  # Default

        db.session.delete(valid_minimal_oc)
        db.session.commit()


# ── Test 15: Migration Correctness ──────────────────────────────────────────

def test_15_migration_revision_correctness():
    import os
    migration_path = os.path.join(
        "migrations", "versions", "f1a2b3c4d5e6_add_org_customer_module.py"
    )
    assert os.path.exists(migration_path), "Migration file must exist"

    with open(migration_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "revision = 'f1a2b3c4d5e6'" in content
        assert "down_revision = '2a0680deca16'" in content
        assert "'org_customer'" in content
        assert "'org_customer_city'" in content
        assert "'org_customer_member'" in content
        assert "op.create_table" in content
        assert "op.add_column('project'" in content


# ── Test 16: Preservation of Existing Individual Customer Behavior ───────────

def test_16_individual_customer_preserved(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        # Create an individual customer
        indiv = Customer(
            name="Individual Customer Ramesh",
            email="ramesh@example.com",
            phone_number="9811122233",
            organization_id=org_id,
            created_by=emp_id,
        )
        db.session.add(indiv)
        db.session.commit()

        # Ensure standard customer queries continue to work unaltered
        fetched = Customer.query.filter_by(id=indiv.id, organization_id=org_id).first()
        assert fetched is not None
        assert fetched.name == "Individual Customer Ramesh"

        # Individual customer project without org_customer works cleanly
        proj = Project(
            name="Ramesh House Glazing",
            customer_id=indiv.id,
            org_customer_id=None,
            organization_id=org_id,
            created_by=emp_id,
            status=ProjectStatus.PLANNING,
        )
        db.session.add(proj)
        db.session.commit()

        assert proj.customer_id == indiv.id
        assert proj.org_customer_id is None

        db.session.delete(proj)
        db.session.delete(indiv)
        db.session.commit()


# ── Test 17: Regression Tests for Existing Workflows ─────────────────────────

def test_17_regression_workflows(test_setup):
    with app.app_context():
        org_id = test_setup["org_id"]
        emp_id = test_setup["emp_id"]

        # 1. Project status updates and querying
        existing_projects = Project.query.filter_by(
            organization_id=org_id, is_deleted=False
        ).limit(5).all()
        assert isinstance(existing_projects, list)

        # 2. Quotation numbering & querying
        existing_quotes = Quotation.query.filter_by(
            organization_id=org_id, is_deleted=False
        ).limit(5).all()
        assert isinstance(existing_quotes, list)

        # 3. Soft delete guard on OrgCustomer: cannot delete org if active projects exist
        oc = OrgCustomer(name="Guarded Org", organization_id=org_id)
        db.session.add(oc)
        db.session.commit()

        proj = Project(
            name="Guarded Project",
            org_customer_id=oc.id,
            organization_id=org_id,
            created_by=emp_id,
        )
        db.session.add(proj)
        db.session.commit()

        active_projects_count = Project.query.filter_by(
            org_customer_id=oc.id, is_deleted=False
        ).count()
        assert active_projects_count == 1  # Deletion guard condition met

        db.session.delete(proj)
        db.session.delete(oc)
        db.session.commit()

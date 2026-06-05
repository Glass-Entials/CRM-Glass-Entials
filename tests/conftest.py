"""
Shared test fixtures for GlassEntials CRM test suite.
Uses SQLite in-memory database for fast, isolated tests.
"""
import os
import pytest

# Ensure we use in-memory SQLite and disable strict cookie security in tests
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("USE_AWS_SECRETS", "false")

from app import app as flask_app
from model import db as _db, User, Employee, Organization, UserRole, Customer, Lead
from werkzeug.security import generate_password_hash
from utils.extensions import limiter

@pytest.fixture(scope="function")
def app():
    """Application fixture — function-scoped for perfect isolation."""
    flask_app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite://",  # Pure memory DB per engine
        WTF_CSRF_ENABLED=False,        # CSRF tested separately
        SESSION_COOKIE_SECURE=False,
        SERVER_NAME="localhost",
    )
    # Disable rate limiter during tests
    limiter.enabled = False
    
    ctx = flask_app.app_context()
    ctx.push()
    
    _db.create_all()
    yield flask_app
    
    # Disable foreign keys temporarily for dropping
    with _db.engine.connect() as conn:
        conn.execute(_db.text("PRAGMA foreign_keys=OFF"))
    _db.drop_all()
    ctx.pop()
    
    # Re-enable limiter after test
    limiter.enabled = True


@pytest.fixture(scope="function")
def db(app):
    """Function-scoped DB fixture. Provided for convenience."""
    return _db


@pytest.fixture(scope="function")
def client(app):
    """Test client."""
    with app.test_client() as c:
        yield c


# ─── Helper: create organization ───────────────────────────────────────────────

def make_org(name="TestOrg", code="TESTORG1"):
    org = Organization(name=name, unique_code=code)
    _db.session.add(org)
    _db.session.flush()
    return org


import random

# ─── Helper: create user + employee pair ───────────────────────────────────────

def make_user(org, email="admin@test.com", username="admin", role=UserRole.ADMIN, pw="TestPass@123"):
    random_phone = str(random.randint(1000000000, 9999999999))
    user = User(
        username=username,
        email=email,
        phone_number=random_phone,
        password=generate_password_hash(pw),
        role=role,
        organization_id=org.id,
    )
    _db.session.add(user)
    _db.session.flush()
    emp = Employee(name=username, email=email, user_id=user.id, organization_id=org.id)
    _db.session.add(emp)
    _db.session.flush()
    return user, emp


@pytest.fixture(scope="function")
def seed_org_user(db):
    """Seed one org + admin user."""
    org = make_org()
    user, emp = make_user(org)
    db.session.commit()
    return org, user, emp


@pytest.fixture(scope="function")
def logged_in_client(client, seed_org_user):
    """Client pre-authenticated as the seeded admin user."""
    _, user, _ = seed_org_user
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    yield client


@pytest.fixture(scope="function")
def two_orgs(db):
    org_a = make_org("OrgA", "ORGA0001")
    org_b = make_org("OrgB", "ORGB0001")

    user_a, emp_a = make_user(org_a, "a@orga.com", "admin_a", pw="PassA@123")
    user_b, emp_b = make_user(org_b, "b@orgb.com", "admin_b", pw="PassB@123")

    # Org B customer — should be invisible to Org A
    cust_b = Customer(
        name="Org B Customer",
        email="custb@orgb.com",
        phone_number="6666666661",
        organization_id=org_b.id,
        created_by=emp_b.id,
    )
    db.session.add(cust_b)

    # Org B lead
    lead_b = Lead(
        name="Org B Lead",
        email="leadb@orgb.com",
        phone_number="5555555551",
        organization_id=org_b.id,
        created_by=emp_b.id,
    )
    db.session.add(lead_b)

    db.session.commit()
    yield org_a, org_b, user_a, user_b, emp_a, emp_b, cust_b, lead_b


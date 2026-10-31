"""
conftest.py — Test database safety and shared fixtures.

PURPOSE
-------
Ensures that *every* pytest invocation is unconditionally isolated from the
real CRM database (MySQL / any DATABASE_URL in .env).  Tests are REFUSED to
start if isolation is not confirmed.

HOW IT WORKS
------------
1. FLASK_ENV forced to "testing" before the Flask app object is imported.
2. SQLALCHEMY_DATABASE_URI overridden to sqlite:///:memory: — never from .env.
3. Session-level guard in pytest_configure aborts the entire test session if
   the URI in use is not the in-memory value.
4. Every test that uses db_session has its writes rolled back at teardown.

COMPATIBILITY NOTES
-------------------
SQLite does not support every MySQL-specific DDL construct, but SQLAlchemy
resolves common types (Boolean, Enum stored as VARCHAR) correctly for SQLite.
If a future model requires MySQL-only DDL, add a dedicated test MySQL DB and
document that decision here.
"""

import os
import pytest
from unittest.mock import MagicMock, patch

# Force test environment BEFORE the application module is loaded.
os.environ["FLASK_ENV"] = "testing"
# SECRET_KEY must satisfy Config's strength check.
os.environ["SECRET_KEY"] = "ci-test-secret-not-for-production"
# *** Unconditionally set DATABASE_URL to in-memory SQLite so Config loads it directly.
# This must happen BEFORE `from app import app` executes.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

_SAFE_TEST_URI = "sqlite:///:memory:"

# ── Import and re-configure the app ─────────────────────────────────────────
from app import app as _flask_app  # noqa: E402
from model import db as _db        # noqa: E402


def _configure_test_app():
    """Apply test overrides to the Flask application."""
    _flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        # *** Override to isolated in-memory SQLite — never DATABASE_URL ***
        SQLALCHEMY_DATABASE_URI=_SAFE_TEST_URI,
        RATELIMIT_ENABLED=False,
        TALISMAN_FORCE_HTTPS=False,
        SESSION_COOKIE_SECURE=False,
    )


_configure_test_app()


# ── Session-level guard ──────────────────────────────────────────────────────

def pytest_configure(config):
    """
    Abort the ENTIRE session if the database URI is not the expected in-memory
    value. This runs before any test can execute.
    """
    with _flask_app.app_context():
        actual_uri = _flask_app.config.get("SQLALCHEMY_DATABASE_URI", "")
        if actual_uri != _SAFE_TEST_URI:
            pytest.exit(
                reason=(
                    "\n\n"
                    "SAFETY ABORT — test database isolation not confirmed.\n"
                    f"Expected URI : {_SAFE_TEST_URI!r}\n"
                    f"Actual  URI  : {actual_uri!r}\n\n"
                    "Do NOT run tests against a real DATABASE_URL. "
                    "Fix conftest.py before proceeding.\n"
                ),
                returncode=3,
            )


# ── Schema creation (session-scoped) ────────────────────────────────────────

@pytest.fixture(scope="session")
def app():
    """Return the configured Flask application (one per session)."""
    with _flask_app.app_context():
        _db.create_all()
        from model import Organization, OrganizationStatus
        if not _db.session.get(Organization, 1):
            _db.session.add(Organization(id=1, name="Test Org 1", unique_code="ORG1", slug="org-1", status=OrganizationStatus.ACTIVE))
        if not _db.session.get(Organization, 999):
            _db.session.add(Organization(id=999, name="Test Org 999", unique_code="ORG999", slug="org-999", status=OrganizationStatus.ACTIVE))
        _db.session.commit()
        yield _flask_app
        _db.drop_all()


# ── Per-test database isolation via SAVEPOINT rollback ───────────────────────

@pytest.fixture()
def db_session(app):
    """
    Wraps each test in a SAVEPOINT. Any writes made during a test are rolled
    back on teardown so the in-memory database stays clean.

    Usage:
        def test_something(db_session):
            obj = MyModel(name="test")
            db_session.add(obj)
            db_session.flush()
            assert obj.id is not None
    """
    with app.app_context():
        connection = _db.engine.connect()
        transaction = connection.begin()
        _db.session.configure(bind=connection)
        _db.session.begin_nested()  # SAVEPOINT

        yield _db.session

        _db.session.rollback()
        transaction.rollback()
        connection.close()


# ── Flask test client ────────────────────────────────────────────────────────

@pytest.fixture()
def client(app):
    """Plain unauthenticated test client."""
    with app.test_client() as c:
        yield c


# ── Mock user builders ───────────────────────────────────────────────────────

def _build_mock_user(role_value="admin", org_id=1, emp_id=99):
    """
    Returns a MagicMock that satisfies Flask-Login current_user interface.

    Parameters
    ----------
    role_value : str  — "admin", "manager", or "employee"
    org_id     : int  — organization_id used in tenant checks
    emp_id     : int  — employee.id embedded in the mock
    """
    user = MagicMock()
    user.id = emp_id
    user.username = f"test_{role_value}_{emp_id}"
    user.email = f"{role_value}_{emp_id}@example.com"
    user.is_authenticated = True
    user.is_active = True
    user.is_anonymous = False
    user.get_id = lambda: str(emp_id)
    user.organization_id = org_id
    user.two_fa_enabled = False
    user.must_change_password = False

    role = MagicMock()
    role.value = role_value
    user.role = role

    emp = MagicMock()
    emp.id = emp_id
    emp.name = f"Test {role_value.capitalize()}"
    user.employee = emp

    return user


def make_admin(org_id=1, emp_id=1):
    return _build_mock_user("admin", org_id, emp_id)


def make_manager(org_id=1, emp_id=2):
    return _build_mock_user("manager", org_id, emp_id)


def make_employee(org_id=1, emp_id=3):
    return _build_mock_user("employee", org_id, emp_id)


# ── Authenticated client fixtures ─────────────────────────────────────────────

@pytest.fixture()
def admin_client(app):
    """Test client with an admin user; yields (client, mock_user)."""
    mock_user = make_admin()
    with app.test_client() as c:
        with patch("flask_login.utils._get_user", return_value=mock_user):
            yield c, mock_user


@pytest.fixture()
def manager_client(app):
    """Test client with a manager user; yields (client, mock_user)."""
    mock_user = make_manager()
    with app.test_client() as c:
        with patch("flask_login.utils._get_user", return_value=mock_user):
            yield c, mock_user


@pytest.fixture()
def employee_client(app):
    """Test client with a base employee user; yields (client, mock_user)."""
    mock_user = make_employee()
    with app.test_client() as c:
        with patch("flask_login.utils._get_user", return_value=mock_user):
            yield c, mock_user


@pytest.fixture()
def other_org_client(app):
    """Test client whose user belongs to a DIFFERENT organization (org_id=999)."""
    mock_user = make_admin(org_id=999, emp_id=999)
    with app.test_client() as c:
        with patch("flask_login.utils._get_user", return_value=mock_user):
            yield c, mock_user

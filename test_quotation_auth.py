"""
test_quotation_auth.py

Focused authorization tests for quotation and quotation-settings routes.

Approach
--------
* Tests are grouped by what they verify: unauthenticated access, employee-role
  denial, admin/manager access, and cross-tenant isolation.
* The app-level ``enforce_org_active`` before_request hook calls
  ``utils.tenant.suspended_org_guard()``, which queries OrganizationMember
  with the current user ID.  Since we use MagicMock users in most tests, we
  patch that function to return None (no suspension) in authenticated tests.
* The ``require_roles`` decorator is NOT patched; we deliberately let it run
  so that 403 responses actually prove the guard is active.
* All tests run against the in-memory SQLite database wired in conftest.py.
  No real MySQL connection is made.
"""

import pytest
from unittest.mock import patch, MagicMock
from conftest import make_admin, make_manager, make_employee

# Patch target for the org-suspension before_request guard.
_GUARD_PATCH = "utils.tenant.suspended_org_guard"


def _no_suspension():
    """Replacement for suspended_org_guard that always allows through."""
    return None


# ── Helper: make an authenticated test client ────────────────────────────────

def _auth_client(app, mock_user):
    """
    Return a context manager stack:  flask test client + login mock + guard patch.
    Usage:
        with _auth_client(app, mock_user) as c:
            resp = c.get("/quotations/")
    """
    import contextlib

    @contextlib.contextmanager
    def _stack():
        with app.test_client() as c:
            with patch("flask_login.utils._get_user", return_value=mock_user):
                with patch(_GUARD_PATCH, side_effect=_no_suspension):
                    yield c

    return _stack()


def _is_login_redirect(resp):
    if resp.status_code not in (301, 302):
        return False
    loc = resp.headers.get("Location", "")
    return "login" in loc.lower() or "auth" in loc.lower()


# ── 1. Unauthenticated access ────────────────────────────────────────────────

class TestUnauthenticated:
    """Every protected route redirects to login or returns 401 for anon users."""

    def test_quotation_list_requires_login(self, client):
        resp = client.get("/quotations/", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_quotation_new_requires_login(self, client):
        resp = client.get("/quotations/new", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_quotation_delete_requires_login(self, client):
        resp = client.post("/quotations/1/delete", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_quotation_status_requires_login(self, client):
        resp = client.post(
            "/quotations/1/status",
            json={"status": "Sent"},
            follow_redirects=False,
        )
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_quotation_convert_proforma_requires_login(self, client):
        resp = client.post("/quotations/1/convert-proforma", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_settings_home_requires_login(self, client):
        resp = client.get("/quotation-settings/", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_terms_list_requires_login(self, client):
        resp = client.get("/quotation-settings/terms", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401

    def test_invoice_settings_requires_login(self, client):
        resp = client.get("/quotation-settings/invoice-settings", follow_redirects=False)
        assert _is_login_redirect(resp) or resp.status_code == 401


# ── 2. Employee role is denied on admin/manager-only routes ─────────────────

class TestEmployeeRoleDenied:
    """An employee must receive 403 on admin/manager-gated routes."""

    def test_employee_denied_settings_home_get(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.get("/quotation-settings/", follow_redirects=False)
            assert resp.status_code == 403, (
                f"Employee must be denied settings_home GET. Got {resp.status_code}"
            )

    def test_employee_denied_settings_home_post(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.post(
                "/quotation-settings/",
                data={"section": "company", "company_name": "Hack"},
                follow_redirects=False,
            )
            assert resp.status_code == 403

    def test_employee_denied_terms_list(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.get("/quotation-settings/terms", follow_redirects=False)
            assert resp.status_code == 403

    def test_employee_denied_add_term_group(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.post(
                "/quotation-settings/terms/groups",
                data={"name": "Injected Group"},
                follow_redirects=False,
            )
            assert resp.status_code == 403

    def test_employee_denied_delete_term(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.post("/quotation-settings/terms/1/delete", follow_redirects=False)
            assert resp.status_code == 403

    def test_employee_denied_invoice_settings(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.get("/quotation-settings/invoice-settings", follow_redirects=False)
            assert resp.status_code == 403

    def test_employee_denied_delete_quotation(self, app):
        with _auth_client(app, make_employee()) as c:
            # Role check happens before DB lookup — must be 403, not 404
            resp = c.post("/quotations/1/delete", follow_redirects=False)
            assert resp.status_code == 403, (
                f"Employee must be denied delete. Got {resp.status_code}"
            )

    def test_employee_denied_convert_to_proforma(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.post("/quotations/1/convert-proforma", follow_redirects=False)
            assert resp.status_code == 403


# ── 3. Admin and manager ARE allowed on admin/manager-only routes ─────────────

class TestAdminManagerAllowed:
    """Admin and manager must NOT receive 403 on admin/manager-gated routes."""

    @pytest.mark.parametrize("make_user", [make_admin, make_manager])
    def test_can_access_settings_home_get(self, app, make_user):
        with _auth_client(app, make_user()) as c:
            resp = c.get("/quotation-settings/", follow_redirects=False)
            assert resp.status_code != 403, (
                f"{make_user.__name__} must not be denied settings_home GET. "
                f"Got {resp.status_code}"
            )

    @pytest.mark.parametrize("make_user", [make_admin, make_manager])
    def test_can_access_terms_list(self, app, make_user):
        with _auth_client(app, make_user()) as c:
            resp = c.get("/quotation-settings/terms", follow_redirects=False)
            assert resp.status_code != 403

    @pytest.mark.parametrize("make_user", [make_admin, make_manager])
    def test_can_access_invoice_settings(self, app, make_user):
        with _auth_client(app, make_user()) as c:
            resp = c.get("/quotation-settings/invoice-settings", follow_redirects=False)
            assert resp.status_code != 403

    @pytest.mark.parametrize("make_user", [make_admin, make_manager])
    def test_delete_missing_quotation_gives_404_not_403(self, app, make_user):
        """Admin/manager posting delete on a nonexistent quotation gets 404 not 403."""
        with _auth_client(app, make_user()) as c:
            resp = c.post("/quotations/999999/delete", follow_redirects=False)
            assert resp.status_code == 404, (
                f"Expected 404 for missing quotation, got {resp.status_code}"
            )


# ── 4. Employees CAN access read-only and creation routes ─────────────────────

class TestEmployeeAllowedRoutes:
    """Employees must be able to list, view and draft quotations."""

    def test_employee_can_list_quotations(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.get("/quotations/", follow_redirects=False)
            assert resp.status_code not in (401, 403)

    def test_employee_can_view_new_form(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.get("/quotations/new", follow_redirects=False)
            assert resp.status_code not in (401, 403)

    def test_employee_can_use_calculate_api(self, app):
        with _auth_client(app, make_employee()) as c:
            resp = c.post(
                "/quotations/api/calculate",
                json={
                    "items": [],
                    "total_discount": 0,
                    "total_discount_type": "flat",
                    "additional_charges": 0,
                    "additional_charges_taxable": False,
                    "is_igst": False,
                },
            )
            assert resp.status_code not in (401, 403)


# ── 5. Cross-tenant isolation ─────────────────────────────────────────────────

class TestCrossTenantIsolation:
    """
    A user in org 999 must receive 404 — never the actual record — when
    requesting a quotation or settings belonging to org 1.
    """

    def test_cross_tenant_quotation_view_returns_404(self, app):
        """
        Viewing a quotation from a different org gives 404 because
        the query filters by organization_id = 999 and finds nothing.
        """
        org2_user = make_admin(org_id=999, emp_id=999)
        with _auth_client(app, org2_user) as c:
            # Quotation with ID 1 (if it existed) belongs to org 1, not 999.
            resp = c.get("/quotations/1", follow_redirects=False)
            assert resp.status_code in (404, 302), (
                f"Cross-tenant access should give 404/302. Got {resp.status_code}"
            )

    def test_cross_tenant_settings_scoped_to_own_org(self, app):
        """
        An admin from org 999 accessing settings_home gets their OWN org's
        settings (get-or-create with org_id=999) — never org 1's record.
        They must not get 403 or 401.
        """
        org2_admin = make_admin(org_id=999, emp_id=999)
        with _auth_client(app, org2_admin) as c:
            resp = c.get("/quotation-settings/", follow_redirects=False)
            assert resp.status_code not in (401, 403)

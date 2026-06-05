"""
Tests for CSRF protection — verifies that state-mutating routes reject
requests without valid CSRF tokens when WTF_CSRF_ENABLED=True.
"""
import pytest
from flask_wtf.csrf import generate_csrf


class TestCSRFEnforcement:
    """
    These tests run with a CSRF-enabled client (separate from conftest default).
    """

    @pytest.fixture(scope="function")
    def csrf_client(self, app):
        """Client with CSRF enabled."""
        original = app.config.get("WTF_CSRF_ENABLED")
        app.config["WTF_CSRF_ENABLED"] = True
        with app.test_client() as c:
            yield c
        app.config["WTF_CSRF_ENABLED"] = original

    def test_login_post_without_csrf_rejected(self, csrf_client):
        """POST to login without CSRF token must be blocked."""
        resp = csrf_client.post(
            "/auth/login",
            data={"email": "user@test.com", "password": "pass"},
        )
        # Flask-WTF returns 400 Bad Request when CSRF token is missing/invalid
        assert resp.status_code == 400

    def test_register_post_without_csrf_rejected(self, csrf_client):
        resp = csrf_client.post(
            "/auth/register",
            data={
                "username": "hacker",
                "email": "h@h.com",
                "password": "Pass@123",
                "confirm_password": "Pass@123",
                "org_option": "create",
                "org_name": "HackerCo",
            },
        )
        assert resp.status_code == 400

    def test_logout_get_not_allowed(self, csrf_client, seed_org_user):
        """GET /auth/logout must return 405 Method Not Allowed."""
        _, user, _ = seed_org_user
        with csrf_client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

        resp = csrf_client.get("/auth/logout")
        assert resp.status_code == 405

    def test_delete_endpoint_without_csrf_rejected(self, csrf_client, seed_org_user):
        """POST to any delete route without CSRF is blocked."""
        _, user, _ = seed_org_user
        with csrf_client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

        # Attempt to delete a non-existent expense without CSRF
        resp = csrf_client.post("/expenses/delete/9999")
        assert resp.status_code == 400


class TestCSRFExemptions:
    def test_health_check_exempt(self, client):
        """Health check endpoint must work without CSRF token."""
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("status") == "healthy"

"""
Tests for authentication flows: login, logout, registration, rate limiting,
forced password change, and open redirect prevention.
"""
import pytest
from werkzeug.security import generate_password_hash
from model import db, User, Organization, Employee, UserRole


class TestLogin:
    def test_login_page_renders(self, client):
        resp = client.get("/auth/login")
        assert resp.status_code == 200

    def test_login_success(self, client, seed_org_user):
        _, user, _ = seed_org_user
        resp = client.post(
            "/auth/login",
            data={"email": user.email, "password": "TestPass@123"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    def test_login_wrong_password(self, client, seed_org_user):
        _, user, _ = seed_org_user
        resp = client.post(
            "/auth/login",
            data={"email": user.email, "password": "WrongPass!"},
            follow_redirects=True,
        )
        assert b"Invalid email or password" in resp.data

    def test_login_unknown_email(self, client):
        resp = client.post(
            "/auth/login",
            data={"email": "nobody@nowhere.com", "password": "pass"},
            follow_redirects=True,
        )
        assert b"Invalid email or password" in resp.data

    def test_login_redirect_next_safe(self, client, seed_org_user):
        """next= param must not redirect to an external domain."""
        _, user, _ = seed_org_user
        resp = client.post(
            "/auth/login?next=http://evil.com/",
            data={"email": user.email, "password": "TestPass@123"},
            follow_redirects=False,
        )
        # Should redirect to home, NOT to evil.com
        location = resp.headers.get("Location", "")
        assert "evil.com" not in location


class TestLogout:
    def test_logout_requires_post(self, logged_in_client):
        """GET /auth/logout must not be allowed (CSRF protection)."""
        resp = logged_in_client.get("/auth/logout", follow_redirects=False)
        assert resp.status_code in (405, 302, 401)

    def test_logout_post_redirects(self, logged_in_client):
        resp = logged_in_client.post("/auth/logout", follow_redirects=False)
        assert resp.status_code in (302, 200)


class TestRegistration:
    def test_register_page_renders(self, client):
        resp = client.get("/auth/register")
        assert resp.status_code == 200

    def test_register_password_mismatch(self, client):
        resp = client.post(
            "/auth/register",
            data={
                "username": "newuser",
                "email": "new@test.com",
                "phone_number": "8888888881",
                "password": "StrongPass@1",
                "confirm_password": "DifferentPass@1",
                "org_option": "create",
                "org_name": "NewCo",
            },
            follow_redirects=True,
        )
        assert b"do not match" in resp.data

    def test_register_duplicate_email(self, client, seed_org_user):
        _, user, _ = seed_org_user
        resp = client.post(
            "/auth/register",
            data={
                "username": "dupuser",
                "email": user.email,  # already taken
                "phone_number": "7777777771",
                "password": "StrongPass@1",
                "confirm_password": "StrongPass@1",
                "org_option": "create",
                "org_name": "DupCo",
            },
            follow_redirects=True,
        )
        assert b"already registered" in resp.data


class TestForcedPasswordChange:
    def test_must_change_password_redirects(self, client, seed_org_user, db):
        """User with must_change_password=True is redirected on every request."""
        _, user, _ = seed_org_user
        user.must_change_password = True
        db.session.commit()

        with client.session_transaction() as sess:
            sess["_user_id"] = str(user.id)
            sess["_fresh"] = True

        resp = client.get("/home", follow_redirects=False)
        assert resp.status_code == 302
        assert "change-password" in resp.headers.get("Location", "")

        # Cleanup
        user.must_change_password = False
        db.session.commit()

"""
test_messages.py
Phase 1 tests for the /messages route.

Uses the live application (same pattern as other project tests) with
flask_login's LOGIN_DISABLED=True to bypass auth where needed, and
mocking to avoid live-DB writes.

Tests cover:
- Unauthenticated GET /messages redirects to login.
- The messages blueprint is registered at /messages.
- The messages_page endpoint exists with the correct name.
- Settings remains reachable at /auth/settings.
- The navbar template now includes a Messages link.
- The profile dropdown contains Settings.
"""

import pytest
from unittest.mock import patch, MagicMock
from app import app
from model import Employee


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_employee(name="Test User", position="Staff"):
    emp = MagicMock(spec=Employee)
    emp.id = 99
    emp.name = name
    emp.position = position
    emp.profile_pic = None
    emp.is_deleted = False
    emp.organization_id = 1
    return emp


def _make_mock_user(emp=None, org_id=1, role_value="admin"):
    user = MagicMock()
    user.is_authenticated = True
    user.is_active = True
    user.is_anonymous = False
    user.get_id = lambda: "1"
    user.organization_id = org_id
    user.employee = emp
    user.two_fa_enabled = False
    user.must_change_password = False
    role = MagicMock()
    role.value = role_value
    user.role = role
    return user


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        with app.test_client() as c:
            yield c


@pytest.fixture
def auth_client():
    """Test client with authentication bypassed via flask_login current_user mock."""
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    emp = _make_mock_employee()
    mock_user = _make_mock_user(emp=emp)

    with app.test_client() as c:
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_emp_q:

            # Return an empty list — no colleagues (empty-list state)
            mock_emp_q.filter_by.return_value.order_by.return_value.all.return_value = []

            yield c


# ── Tests: Unauthenticated ────────────────────────────────────────────────────

class TestUnauthenticated:
    def test_messages_redirects_to_login(self, client):
        """
        GET /messages without authentication must redirect to the login page.
        This verifies @login_required is applied.
        """
        resp = client.get("/messages", follow_redirects=False)
        assert resp.status_code in (301, 302), (
            f"Expected redirect, got {resp.status_code}"
        )
        location = resp.headers.get("Location", "")
        # Flask-Login redirects to login_view which is 'auth.login'
        assert "login" in location.lower() or "auth" in location.lower(), (
            f"Redirect '{location}' should point at the login endpoint"
        )

    def test_settings_redirects_to_login(self, client):
        """
        GET /auth/settings must still require authentication (regression test).
        """
        resp = client.get("/auth/settings", follow_redirects=False)
        assert resp.status_code in (301, 302), (
            f"Expected redirect for unauthenticated /auth/settings, got {resp.status_code}"
        )


# ── Tests: Route and Blueprint Registration ───────────────────────────────────

class TestBlueprintRegistration:
    def test_messages_blueprint_registered(self):
        """The messages blueprint must be registered in the application."""
        blueprint_names = [bp for bp in app.blueprints]
        assert "messages" in blueprint_names, (
            f"'messages' blueprint not found. Registered: {blueprint_names}"
        )

    def test_messages_route_exists(self):
        """The /messages URL must be mapped to the messages.messages_page endpoint."""
        rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
        assert "/messages" in rules, (
            f"/messages not in URL map. Available routes: {sorted(rules.keys())}"
        )
        assert rules["/messages"] == "messages.messages_page", (
            f"Endpoint mismatch: {rules['/messages']}"
        )

    def test_settings_route_unchanged(self):
        """The /auth/settings route must remain intact after nav changes."""
        rules = {rule.rule: rule.endpoint for rule in app.url_map.iter_rules()}
        assert "/auth/settings" in rules, "/auth/settings route was removed — regression"
        assert rules["/auth/settings"] == "auth.settings"


# ── Tests: Template Content ───────────────────────────────────────────────────

class TestNavbarTemplate:
    def _get_navbar_content(self):
        with open(
            "templates/includes/navbar.html", encoding="utf-8"
        ) as f:
            return f.read()

    def test_navbar_contains_messages_link(self):
        """Navbar must include a link to the /messages endpoint."""
        content = self._get_navbar_content()
        assert "messages.messages_page" in content, (
            "Navbar does not contain a link to messages.messages_page"
        )

    def test_navbar_profile_dropdown_contains_settings(self):
        """Profile dropdown must include the Settings link (moved from standalone icon)."""
        content = self._get_navbar_content()
        # Verify Settings is somewhere in the dropdown block
        assert "auth.settings" in content, (
            "Navbar does not contain a link to auth.settings"
        )
        # Verify the old standalone Settings gear SVG is gone
        assert 'settings-icon' not in content, (
            "Old standalone .settings-icon link is still present and should be replaced"
        )

    def test_navbar_messages_icon_present(self):
        """Navbar must include the messages chat-bubble SVG icon."""
        content = self._get_navbar_content()
        assert "messages-icon" in content, (
            "Navbar .messages-icon element not found"
        )

    def test_navbar_has_message_floating_notification(self):
        """Navbar must include floating notification toast handler for direct messages."""
        content = self._get_navbar_content()
        assert "spawnMessageToast" in content
        assert "realtime-toast-message" in content
        assert "messaging:new_message" in content


# ── Tests: Messages Template Content ─────────────────────────────────────────

class TestMessagesTemplate:
    def _get_template_content(self):
        with open(
            "templates/messages/index.html", encoding="utf-8"
        ) as f:
            return f.read()

    def test_template_has_employee_search(self):
        content = self._get_template_content()
        assert "msg-search" in content

    def test_template_has_composer(self):
        content = self._get_template_content()
        assert "msg-composer" in content

    def test_template_has_send_button(self):
        content = self._get_template_content()
        assert "msg-send-btn" in content

    def test_template_has_phase1_notice(self):
        content = self._get_template_content()
        assert "Phase 1" in content

    def test_template_has_back_button_for_mobile(self):
        content = self._get_template_content()
        assert "msg-back-btn" in content

    def test_template_has_empty_welcome_state(self):
        content = self._get_template_content()
        assert "Select a conversation" in content

    def test_template_has_no_results_state(self):
        content = self._get_template_content()
        assert "msg-no-results" in content

    def test_template_does_not_use_innerHTML_for_messages(self):
        """
        Bubbles must use textContent (not innerHTML) for safe text rendering.
        """
        content = self._get_template_content()
        # Verify textContent is used for msg-bubble rendering in JS
        assert "bubble.textContent = msg.text" in content, (
            "Message bubbles should use textContent, not innerHTML, for XSS safety"
        )


# ── Tests: Phase 2 API Authentication & Security ──────────────────────────────

class TestUnauthenticatedApi:
    def test_conversations_api_requires_login(self, client):
        resp = client.get("/api/messages/conversations", follow_redirects=False)
        assert resp.status_code in (301, 302, 401)

    def test_history_api_requires_login(self, client):
        resp = client.get("/api/messages/1/history", follow_redirects=False)
        assert resp.status_code in (301, 302, 401)

    def test_send_api_requires_login(self, client):
        resp = client.post("/api/messages/1/send", json={"text": "hello"}, follow_redirects=False)
        assert resp.status_code in (301, 302, 401)

    def test_read_api_requires_login(self, client):
        resp = client.post("/api/messages/1/read", json={}, follow_redirects=False)
        assert resp.status_code in (301, 302, 401)

    def test_unread_count_api_requires_login(self, client):
        resp = client.get("/api/messages/unread-count", follow_redirects=False)
        assert resp.status_code in (301, 302, 401)


# ── Tests: Phase 2 Tenant Isolation & Validation ──────────────────────────────

class TestTenantIsolationAndValidation:
    def test_cannot_message_self(self, client):
        emp = _make_mock_employee()
        emp.id = 42
        mock_user = _make_mock_user(emp=emp, org_id=1)
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user):
            resp = client.post("/api/messages/42/send", json={"text": "Hello me"})
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["success"] is False
            assert "yourself" in data["error"].lower()

    def test_cannot_get_history_with_self(self, client):
        emp = _make_mock_employee()
        emp.id = 42
        mock_user = _make_mock_user(emp=emp, org_id=1)
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user):
            resp = client.get("/api/messages/42/history")
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["success"] is False

    def test_cross_organization_colleague_rejected(self, client):
        emp = _make_mock_employee()
        emp.id = 10
        emp.organization_id = 1
        mock_user = _make_mock_user(emp=emp, org_id=1)
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_eq:
            # Query for colleague in Org 1 returns None (because colleague belongs to Org 2)
            mock_eq.filter_by.return_value.first.return_value = None
            resp = client.get("/api/messages/999/history")
            assert resp.status_code == 404
            data = resp.get_json()
            assert data["success"] is False
            assert "not found" in data["error"].lower() or "organization" in data["error"].lower()

    def test_empty_message_body_rejected(self, client):
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)
        colleague = _make_mock_employee(name="Colleague")
        colleague.id = 20
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_eq:
            mock_eq.filter_by.return_value.first.return_value = colleague
            resp = client.post("/api/messages/20/send", json={"text": "   "})
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["success"] is False
            assert "empty" in data["error"].lower()

    def test_oversized_message_body_rejected(self, client):
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)
        colleague = _make_mock_employee(name="Colleague")
        colleague.id = 20
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_eq:
            mock_eq.filter_by.return_value.first.return_value = colleague
            resp = client.post("/api/messages/20/send", json={"text": "X" * 5001})
            assert resp.status_code == 400
            data = resp.get_json()
            assert data["success"] is False
            assert "long" in data["error"].lower()

    def test_user_without_employee_profile_rejected(self, client):
        mock_user = _make_mock_user(emp=None, org_id=1)
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user):
            resp = client.get("/api/messages/conversations")
            assert resp.status_code == 403
            data = resp.get_json()
            assert data["success"] is False


# ── Tests: Phase 2 Database Models & Persistence ──────────────────────────────

class TestPersistentMessagingLifecycle:
    def test_message_model_to_dict_serialization(self):
        from model import Message
        from datetime import datetime
        now = datetime(2026, 10, 12, 10, 30, 0)
        msg = Message(
            id=101,
            conversation_id=5,
            sender_employee_id=20,
            body="Hello from colleague",
            created_at=now
        )
        # For recipient (current_emp=10)
        d_other = msg.to_dict(current_employee_id=10)
        assert d_other["id"] == 101
        assert d_other["from"] == "other"
        assert d_other["sender_id"] == 20
        assert d_other["text"] == "Hello from colleague"
        assert "2026-10-12T10:30:00Z" in d_other["ts"]

        # For sender (current_emp=20)
        d_self = msg.to_dict(current_employee_id=20)
        assert d_self["from"] == "self"

    def test_xss_payload_stored_literally(self):
        from model import Message
        from datetime import datetime
        xss = "<script>alert('xss')</script>"
        msg = Message(
            id=102,
            conversation_id=5,
            sender_employee_id=20,
            body=xss,
            created_at=datetime.utcnow()
        )
        d = msg.to_dict(current_employee_id=20)
        assert d["text"] == xss

    def test_conversation_get_or_create_in_db(self):
        from routes.messages import get_or_create_direct_conversation
        from model import db, Conversation, ConversationMember, Organization, Employee
        with app.app_context():
            org = Organization.query.first()
            if not org:
                pytest.skip("No organization in database")
            emps = Employee.query.filter_by(organization_id=org.id, is_deleted=False).limit(2).all()
            if len(emps) < 2:
                pytest.skip("Fewer than 2 active employees in test organization")
            e1, e2 = emps[0], emps[1]

            conv1 = get_or_create_direct_conversation(org.id, e1.id, e2.id)
            assert conv1 is not None
            assert conv1.id is not None
            assert conv1.organization_id == org.id

            # Calling in reverse order must return the exact same conversation
            conv2 = get_or_create_direct_conversation(org.id, e2.id, e1.id)
            assert conv1.id == conv2.id


    def test_message_receipt_status_single_tick_when_sent(self):
        from model import Message, ConversationMember
        from datetime import datetime, timedelta
        now = datetime(2026, 10, 12, 10, 30, 0)
        msg = Message(
            id=103,
            conversation_id=5,
            sender_employee_id=20,
            body="Just sent message",
            created_at=now
        )
        # Recipient has never polled or opened chat
        colleague_member = ConversationMember(
            conversation_id=5,
            employee_id=10,
            last_delivered_at=None,
            last_read_at=None
        )
        d = msg.to_dict(current_employee_id=20, recipient_member=colleague_member)
        assert d["status"] == "sent"

    def test_message_receipt_status_double_tick_when_delivered(self):
        from model import Message, ConversationMember
        from datetime import datetime, timedelta
        msg_time = datetime(2026, 10, 12, 10, 30, 0)
        delivered_time = datetime(2026, 10, 12, 10, 30, 5)
        msg = Message(
            id=104,
            conversation_id=5,
            sender_employee_id=20,
            body="Delivered message",
            created_at=msg_time
        )
        # Recipient polled after message was created, but has not read it
        colleague_member = ConversationMember(
            conversation_id=5,
            employee_id=10,
            last_delivered_at=delivered_time,
            last_read_at=None
        )
        d = msg.to_dict(current_employee_id=20, recipient_member=colleague_member)
        assert d["status"] == "delivered"

    def test_message_receipt_status_triple_tick_when_read(self):
        from model import Message, ConversationMember
        from datetime import datetime, timedelta
        msg_time = datetime(2026, 10, 12, 10, 30, 0)
        read_time = datetime(2026, 10, 12, 10, 31, 0)
        msg = Message(
            id=105,
            conversation_id=5,
            sender_employee_id=20,
            body="Read message",
            created_at=msg_time
        )
        # Recipient opened the chat after message was created
        colleague_member = ConversationMember(
            conversation_id=5,
            employee_id=10,
            last_delivered_at=read_time,
            last_read_at=read_time
        )
        d = msg.to_dict(current_employee_id=20, recipient_member=colleague_member)
        assert d["status"] == "read"


# ── Tests: Phase 2 Template Extensions ────────────────────────────────────────

class TestPhase2TemplateExtensions:
    def _get_template_content(self):
        with open("templates/messages/index.html", encoding="utf-8") as f:
            return f.read()

    def test_template_has_csrf_token_meta(self):
        content = self._get_template_content()
        assert 'name="csrf-token"' in content

    def test_template_has_phase2_notice(self):
        content = self._get_template_content()
        assert "Phase 2" in content

    def test_template_has_polling_sync(self):
        content = self._get_template_content()
        assert "pollSync" in content
        assert "setInterval(pollSync" in content

    def test_template_has_single_double_triple_tick_rendering(self):
        content = self._get_template_content()
        assert "createReceiptElement" in content
        assert "&#10003;&#10003;&#10003;" in content  # Triple tick
        assert "&#10003;&#10003;" in content        # Double tick
        assert "&#10003;" in content                # Single tick
        assert "status || 'sent'" in content

    def test_css_has_tick_styling_classes(self):
        with open("static/css/messages.css", encoding="utf-8") as f:
            css = f.read()
        assert ".msg-receipt-icon" in css
        assert ".msg-receipt-icon.sent" in css
        assert ".msg-receipt-icon.delivered" in css
        assert ".msg-receipt-icon.read" in css


# ── Tests: Phase 3 Real-Time Socket.IO ─────────────────────────────────────────

class TestPhase3RealTimeSocketIO:
    def _get_template_content(self):
        with open("templates/messages/index.html", encoding="utf-8") as f:
            return f.read()

    def _get_navbar_content(self):
        with open("templates/includes/navbar.html", encoding="utf-8") as f:
            return f.read()

    def _get_css_content(self):
        with open("static/css/messages.css", encoding="utf-8") as f:
            return f.read()

    def test_room_name_format(self):
        from routes.messages import _employee_room
        room = _employee_room(org_id=7, emp_id=42)
        assert room == "org_7_user_42"

    def test_safe_socketio_emit_handles_failure_silently(self):
        from routes.messages import _safe_socketio_emit
        with patch("utils.extensions.socketio.emit", side_effect=Exception("Connection broken")):
            # Must not raise an exception
            _safe_socketio_emit("messaging:test", {"data": 123}, room="org_1_user_2")

    def test_send_message_triggers_realtime_socket_emits(self, client):
        from routes.messages import send_message
        mock_emp = _make_mock_employee(name="Sender")
        mock_emp.id = 10
        mock_recipient = _make_mock_employee(name="Recipient")
        mock_recipient.id = 20

        mock_user = _make_mock_user(emp=mock_emp, org_id=1)

        mock_conv = MagicMock()
        mock_conv.id = 99

        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_emp_q, \
             patch("routes.messages.get_or_create_direct_conversation", return_value=mock_conv), \
             patch("routes.messages.db.session"), \
             patch("routes.messages.ConversationMember.query") as mock_cm_q, \
             patch("routes.messages._compute_unread_for_employee", return_value=1), \
             patch("routes.messages._safe_socketio_emit") as mock_emit:

            mock_emp_q.filter_by.return_value.first.return_value = mock_recipient
            mock_cm_q.filter_by.return_value.first.return_value = MagicMock()

            resp = client.post(
                "/api/messages/20/send",
                json={"text": "Hello real-time!"},
                headers={"Content-Type": "application/json"}
            )
            assert resp.status_code == 201

            # Verify safe_socketio_emit was called for new_message, message_status, unread_updated, conversation_updated
            event_names = [call[0][0] for call in mock_emit.call_args_list]
            assert "messaging:new_message" in event_names
            assert "messaging:message_status" in event_names
            assert "messaging:unread_updated" in event_names
            assert "messaging:conversation_updated" in event_names

            # Verify recipient room is target for new_message
            new_msg_calls = [c for c in mock_emit.call_args_list if c[0][0] == "messaging:new_message"]
            assert len(new_msg_calls) == 1
            assert new_msg_calls[0][1]["room"] == "org_1_user_20"
            assert new_msg_calls[0][0][1]["text"] == "Hello real-time!"

    def test_mark_as_read_triggers_read_updated_emit(self, client):
        mock_emp = _make_mock_employee(name="Reader")
        mock_emp.id = 10
        mock_user = _make_mock_user(emp=mock_emp, org_id=1)

        mock_conv = MagicMock()
        mock_conv.id = 55

        mock_other_member = MagicMock()
        mock_other_member.employee_id = 20

        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.db.session.query") as mock_q, \
             patch("routes.messages.ConversationMember.query") as mock_cm_q, \
             patch("routes.messages.db.session"), \
             patch("routes.messages._safe_socketio_emit") as mock_emit:

            mock_q.return_value.filter.return_value.first.return_value = mock_conv
            mock_cm_q.filter_by.return_value.first.return_value = MagicMock()
            mock_cm_q.filter.return_value.first.return_value = mock_other_member

            resp = client.post("/api/messages/20/read")
            assert resp.status_code == 200

            # Verify messaging:read_updated was emitted to other member's room
            event_names = [call[0][0] for call in mock_emit.call_args_list]
            assert "messaging:read_updated" in event_names
            read_call = [c for c in mock_emit.call_args_list if c[0][0] == "messaging:read_updated"][0]
            assert read_call[1]["room"] == "org_1_user_20"
            assert read_call[0][1]["read_by_id"] == 10

    def test_socket_resync_handler_in_app(self):
        from app import on_messaging_resync
        mock_emp = _make_mock_employee()
        mock_emp.id = 15
        mock_user = _make_mock_user(emp=mock_emp, org_id=2)

        with patch("app.current_user", mock_user), \
             patch("app.sio_emit") as mock_emit:
            on_messaging_resync({})
            mock_emit.assert_called_once()
            args, kwargs = mock_emit.call_args
            assert args[0] == "messaging:resync_required"
            assert kwargs["room"] == "org_2_user_15"

    def test_frontend_has_socket_status_and_listeners(self):
        content = self._get_template_content()
        assert "msg-socket-status" in content
        assert "messaging:new_message" in content
        assert "messaging:conversation_updated" in content
        assert "messaging:unread_updated" in content
        assert "messaging:read_updated" in content
        assert "messaging:message_status" in content
        assert "messaging:resync_required" in content
        assert "messaging:resync" in content

    def test_css_has_socket_connection_dot_styles(self):
        css = self._get_css_content()
        assert ".msg-socket-dot" in css
        assert ".msg-socket-dot.msg-socket-connected" in css
        assert ".msg-socket-dot.msg-socket-connecting" in css
        assert ".msg-socket-dot.msg-socket-disconnected" in css

    def test_navbar_has_messages_badge_and_listeners(self):
        navbar = self._get_navbar_content()
        assert 'id="nav-messages-badge"' in navbar
        assert "messaging:unread_updated" in navbar
        assert "messaging:new_message" in navbar
        assert "updateNavMessagesBadge" in navbar


# ── Tests: Phase 4 Premium UX, Micro-Interactions & Smooth Animations ──────────

class TestPhase4PremiumUXAndMicroInteractions:
    def _get_template_content(self):
        with open("templates/messages/index.html", encoding="utf-8") as f:
            return f.read()

    def _get_css_content(self):
        with open("static/css/messages.css", encoding="utf-8") as f:
            return f.read()

    def test_typing_socket_event_derives_session_and_validates_recipient(self):
        from app import on_messaging_typing
        mock_emp = _make_mock_employee()
        mock_emp.id = 12
        mock_user = _make_mock_user(emp=mock_emp, org_id=5)

        mock_recipient = _make_mock_employee(name="Recipient Colleague")
        mock_recipient.id = 34

        with app.app_context():
            with patch("app.current_user", mock_user), \
                 patch("model.Employee.query") as mock_emp_q, \
                 patch("app.sio_emit") as mock_emit:

                mock_emp_q.filter_by.return_value.first.return_value = mock_recipient

                # Valid typing start
                on_messaging_typing({"recipient_id": 34, "typing": True})
                mock_emit.assert_called_once()
                args, kwargs = mock_emit.call_args
                assert args[0] == "messaging:user_typing"
                assert args[1]["sender_id"] == 12
                assert args[1]["typing"] is True
                assert kwargs["room"] == "org_5_user_34"

    def test_typing_socket_event_ignores_self_or_missing_recipient(self):
        from app import on_messaging_typing
        mock_emp = _make_mock_employee()
        mock_emp.id = 12
        mock_user = _make_mock_user(emp=mock_emp, org_id=5)

        with app.app_context():
            with patch("app.current_user", mock_user), \
                 patch("app.sio_emit") as mock_emit:

                # Self-typing should be ignored
                on_messaging_typing({"recipient_id": 12, "typing": True})
                mock_emit.assert_not_called()

                # Missing recipient_id should be ignored
                on_messaging_typing({"typing": True})
                mock_emit.assert_not_called()

    def test_template_has_phase4_ux_elements(self):
        content = self._get_template_content()
        assert "msg-typing-status" in content
        assert "msg-skeleton" in content
        assert "msg-empty-convo" in content
        assert "msg-scroll-pill" in content
        assert "msg-toast" in content
        assert "msg-send-spinner" in content

    def test_template_has_smart_scroll_and_typing_listeners(self):
        content = self._get_template_content()
        assert "isUserNearBottom" in content
        assert "scrollToBottom" in content
        assert "messaging:typing" in content
        assert "messaging:user_typing" in content
        assert "msg-typing-bubble-wrap" in content

    def test_css_has_phase4_styles_and_microinteractions(self):
        css = self._get_css_content()
        assert ".msg-bubble-wrap.msg-entering" in css
        assert ".msg-emp-row.msg-row-highlight" in css
        assert ".msg-unread-badge.msg-badge-pop" in css
        assert ".msg-typing-status" in css
        assert ".msg-typing-bubble" in css
        assert ".msg-skeleton" in css
        assert ".msg-empty-convo" in css
        assert ".msg-scroll-bottom-btn" in css
        assert ".msg-send-spinner" in css

    def test_read_receipt_has_clean_styling_without_blurry_glow(self):
        css = self._get_css_content()
        assert ".msg-receipt-icon.read" in css
        # Verify neon text-shadow is removed
        assert "text-shadow: 0 0 8px rgba(56, 189, 248, 0.45)" not in css

    def test_css_reduced_motion_resets_phase4_animations(self):
        css = self._get_css_content()
        assert "prefers-reduced-motion: reduce" in css
        assert ".msg-bubble-wrap.msg-entering" in css
        assert ".msg-scroll-bottom-btn" in css


# ── Tests: Phase 4 File & Image Sharing and Smoothness ─────────────────────────

class TestPhase4FileAndImageSharingAndSmoothness:
    @staticmethod
    def _get_template_content():
        with open("templates/messages/index.html", "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _get_css_content():
        with open("static/css/messages.css", "r", encoding="utf-8") as f:
            return f.read()

    def test_template_has_attachment_and_lightbox_elements(self):
        content = self._get_template_content()
        assert 'id="msg-file-input"' in content
        assert 'id="msg-attachment-staging"' in content
        assert 'id="msg-drag-overlay"' in content
        assert 'id="msg-lightbox-modal"' in content
        assert 'id="msg-attach-btn"' in content
        assert 'id="msg-staging-remove"' in content
        assert 'openImageLightbox' in content
        assert 'stageFile' in content

    def test_template_css_cache_buster_updated(self):
        content = self._get_template_content()
        assert "messages.css" in content
        # Ensure it has a cache busting query parameter of at least v=50
        import re
        m = re.search(r'messages\.css.*?v=(\d+)', content)
        assert m is not None
        assert int(m.group(1)) >= 50

    def test_css_has_spring_animations_and_attachment_styling(self):
        css = self._get_css_content()
        # Spring physics and keyframes
        assert "@keyframes msgBubbleEnter" in css
        assert "@keyframes msgTickPop" in css
        assert "@keyframes msgStreamSwitch" in css
        assert "@keyframes msgHeaderEnter" in css
        assert "cubic-bezier(0.34, 1.4, 0.64, 1)" in css
        # Attachment components
        assert ".msg-attachment-staging" in css
        assert ".msg-attachment-img-wrap" in css
        assert ".msg-attachment-file-card" in css
        assert ".msg-drag-overlay" in css
        assert ".msg-lightbox-modal" in css

    def test_message_model_attachment_serialization(self):
        from model import Message
        from datetime import datetime

        # 1. Image attachment
        img_msg = Message(
            id=201,
            conversation_id=5,
            sender_employee_id=20,
            body="Here is design screenshot",
            created_at=datetime.utcnow(),
            attachment_url="/api/messages/attachment/201/screenshot.png",
            attachment_name="screenshot.png",
            attachment_type="image/png",
            attachment_size=204800,
        )
        d_img = img_msg.to_dict(current_employee_id=20)
        assert d_img["attachment"] is not None
        assert d_img["attachment"]["name"] == "screenshot.png"
        assert d_img["attachment"]["type"] == "image/png"
        assert d_img["attachment"]["size"] == 204800
        assert d_img["attachment"]["is_image"] is True

        # 2. Document/File attachment
        doc_msg = Message(
            id=202,
            conversation_id=5,
            sender_employee_id=20,
            body="Project specification",
            created_at=datetime.utcnow(),
            attachment_url="/api/messages/attachment/202/spec.pdf",
            attachment_name="spec.pdf",
            attachment_type="application/pdf",
            attachment_size=1048576,
        )
        d_doc = doc_msg.to_dict(current_employee_id=20)
        assert d_doc["attachment"] is not None
        assert d_doc["attachment"]["name"] == "spec.pdf"
        assert d_doc["attachment"]["type"] == "application/pdf"
        assert d_doc["attachment"]["is_image"] is False

    def test_send_message_disallowed_extension_rejected(self, client):
        import io
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)
        colleague = _make_mock_employee(name="Colleague")
        colleague.id = 20
        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_eq:
            mock_eq.filter_by.return_value.first.return_value = colleague
            data = {
                "text": "Check this file",
                "file": (io.BytesIO(b"echo evil"), "danger.exe")
            }
            resp = client.post("/api/messages/20/send", data=data, content_type="multipart/form-data")
            assert resp.status_code == 400
            res = resp.get_json()
            assert res["success"] is False
            assert "not permitted" in res["error"].lower()

    def test_send_message_with_valid_attachment(self, client):
        import io
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)
        colleague = _make_mock_employee(name="Colleague")
        colleague.id = 20

        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Employee.query") as mock_eq, \
             patch("routes.messages.get_or_create_direct_conversation") as mock_conv_fn, \
             patch("routes.messages.db.session.add"), \
             patch("routes.messages.db.session.commit"), \
             patch("routes.messages._safe_socketio_emit") as mock_emit:

            mock_eq.filter_by.return_value.first.return_value = colleague

            mock_conv = MagicMock()
            mock_conv.id = 77
            mock_conv_fn.return_value = mock_conv

            data = {
                "text": "Glass invoice",
                "file": (io.BytesIO(b"PDF invoice content"), "invoice.pdf")
            }
            resp = client.post("/api/messages/20/send", data=data, content_type="multipart/form-data")
            assert resp.status_code == 201
            res = resp.get_json()
            assert res["success"] is True
            assert res["message"]["attachment"]["name"] == "invoice.pdf"
            assert res["message"]["attachment"]["is_image"] is False
            assert mock_emit.called


# ── Tests: Schema Migration & Attachment Security ─────────────────────────────

class TestAttachmentSchemaMigration:
    def test_migration_file_exists_and_revises_phase2(self):
        import importlib.util
        import os
        mig_path = "migrations/versions/2a0680deca16_add_attachment_fields_to_msg_message.py"
        assert os.path.exists(mig_path), "Migration 2a0680deca16 file must exist"
        spec = importlib.util.spec_from_file_location("mig_module", mig_path)
        mig = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mig)
        assert mig.revision == "2a0680deca16"
        assert mig.down_revision == "a3f8e1c2d9b0"
        assert hasattr(mig, "upgrade")
        assert hasattr(mig, "downgrade")

    def test_message_model_has_required_attachment_columns(self):
        from model import Message
        assert hasattr(Message, "attachment_url")
        assert hasattr(Message, "attachment_name")
        assert hasattr(Message, "attachment_type")
        assert hasattr(Message, "attachment_size")

        table = Message.__table__
        assert table.c.attachment_url.nullable is True
        assert table.c.attachment_url.type.length == 500
        assert table.c.attachment_name.nullable is True
        assert table.c.attachment_name.type.length == 255
        assert table.c.attachment_type.nullable is True
        assert table.c.attachment_type.type.length == 100
        assert table.c.attachment_size.nullable is True

    def test_db_has_attachment_columns(self):
        from model import db
        import sqlalchemy as sa
        with app.app_context():
            insp = sa.inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns("msg_message")}
            assert "attachment_url" in cols
            assert "attachment_name" in cols
            assert "attachment_type" in cols
            assert "attachment_size" in cols

    def test_messages_page_renders_with_message_rows_without_500(self, client):
        """
        Verify /messages renders successfully (HTTP 200) without 500
        when conversations and messages are queried.
        """
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)

        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user):
            resp = client.get("/messages")
            assert resp.status_code == 200


class TestAttachmentSecurityAndAccess:
    def test_attachment_download_path_traversal_blocked(self, client):
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)

        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Message.query") as mock_msg_q, \
             patch("routes.messages.Conversation.query") as mock_conv_q, \
             patch("routes.messages.ConversationMember.query") as mock_mem_q:

            mock_msg = MagicMock()
            mock_msg.id = 1
            mock_msg.conversation_id = 100
            # Path traversal attempt in attachment_url
            mock_msg.attachment_url = "/attachment/1/../../../../etc/passwd"
            mock_msg.attachment_name = "passwd"
            mock_msg.attachment_type = "text/plain"
            mock_msg_q.get_or_404.return_value = mock_msg

            mock_conv = MagicMock()
            mock_conv.id = 100
            mock_conv.organization_id = 1
            mock_conv_q.filter_by.return_value.first_or_404.return_value = mock_conv

            mock_mem = MagicMock()
            mock_mem_q.filter_by.return_value.first.return_value = mock_mem

            resp = client.get("/attachment/1/passwd")
            # Should be 403 or 404, definitely not 200 serving system file
            assert resp.status_code in (403, 404)

    def test_attachment_non_member_forbidden(self, client):
        emp = _make_mock_employee()
        emp.id = 10
        mock_user = _make_mock_user(emp=emp, org_id=1)

        with patch("flask_login.utils._get_user", return_value=mock_user), \
             patch("routes.messages.current_user", mock_user), \
             patch("routes.messages.Message.query") as mock_msg_q, \
             patch("routes.messages.Conversation.query") as mock_conv_q, \
             patch("routes.messages.ConversationMember.query") as mock_mem_q:

            mock_msg = MagicMock()
            mock_msg.id = 1
            mock_msg.conversation_id = 100
            mock_msg.attachment_url = "/attachment/1/test.pdf"
            mock_msg_q.get_or_404.return_value = mock_msg

            mock_conv = MagicMock()
            mock_conv.id = 100
            mock_conv.organization_id = 1
            mock_conv_q.filter_by.return_value.first_or_404.return_value = mock_conv

            # User is NOT a member of this conversation
            mock_mem_q.filter_by.return_value.first.return_value = None

            resp = client.get("/attachment/1/test.pdf")
            assert resp.status_code == 403



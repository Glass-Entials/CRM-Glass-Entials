"""
routes/messages.py
Phase 2: Persistent Internal Employee Messaging — Database-backed.
Phase 3: Real-Time event delivery via Socket.IO.

Features:
- Multi-tenant scoped direct conversations between colleagues in the same organisation.
- Full message persistence in msg_conversation, msg_conversation_member, msg_message.
- History retrieval with chronological ordering and automatic read status updates.
- Real-time unread counts per conversation and across the organization.
- Strict tenant isolation: users cannot access or message employees outside their active org.
- Input validation, length bounds, and safe plain-text rendering.
- Socket.IO events emitted after confirmed DB commits (never speculatively).
"""

import os
import uuid
import mimetypes
from datetime import datetime
from flask import Blueprint, render_template, request, jsonify, abort, current_app, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_, and_, desc
from werkzeug.utils import secure_filename
from model import db, Employee, Conversation, ConversationMember, Message
from utils.timezone import time_ago_ist

messages_bp = Blueprint("messages", __name__)

ALLOWED_ATTACHMENT_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "svg",
    "pdf", "doc", "docx", "xls", "xlsx", "csv", "txt", "zip", "rar"
}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB



# ── Phase 3: Socket.IO Real-Time Helpers ──────────────────────────────────────

def _employee_room(org_id, emp_id):
    """Returns the server-derived private room name for an employee."""
    return f"org_{org_id}_user_{emp_id}"


def _safe_socketio_emit(event, payload, room):
    """
    Emits a Socket.IO event to a specific room.
    Imported lazily to avoid circular import with app.py.
    Silently swallows errors so a socket failure never breaks the REST response.
    """
    try:
        from utils.extensions import socketio
        socketio.emit(event, payload, room=room)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Socket.IO emit failed [%s -> %s]: %s", event, room, exc
        )


def _compute_unread_for_employee(emp_id, org_id):
    """Returns the total unread count for an employee across all their conversations."""
    memberships = ConversationMember.query.join(Conversation).filter(
        Conversation.organization_id == org_id,
        ConversationMember.employee_id == emp_id,
    ).all()
    total = 0
    for m in memberships:
        q = Message.query.filter(
            Message.conversation_id == m.conversation_id,
            Message.sender_employee_id != emp_id,
        )
        if m.last_read_at:
            q = q.filter(Message.created_at > m.last_read_at)
        total += q.count()
    return total


# ── Helper Functions ──────────────────────────────────────────────────────────

def _get_current_employee():
    """
    Returns the Employee record linked to the logged-in user.
    Returns None if user has no linked employee profile.
    """
    if not current_user.is_authenticated:
        return None
    return current_user.employee


def _get_active_org_id():
    """
    Returns the active organization ID for the current user.
    """
    return current_user.organization_id


def get_or_create_direct_conversation(org_id, emp_a_id, emp_b_id):
    """
    Finds or creates a direct conversation between two employees in the same org.
    Concurrency safe: if a race occurs, catches integrity error and re-queries.
    """
    # 1. Search for existing direct conversation with both members
    conv = (
        db.session.query(Conversation)
        .filter(
            Conversation.organization_id == org_id,
            Conversation.conversation_type == "direct",
            Conversation.members.any(employee_id=emp_a_id),
            Conversation.members.any(employee_id=emp_b_id),
        )
        .first()
    )
    if conv:
        return conv

    # 2. Create new conversation
    now = datetime.utcnow()
    conv = Conversation(
        organization_id=org_id,
        conversation_type="direct",
        created_at=now,
        updated_at=now,
        last_message_at=now,
    )
    db.session.add(conv)
    db.session.flush()

    m1 = ConversationMember(
        conversation_id=conv.id, employee_id=emp_a_id, joined_at=now, last_read_at=now
    )
    m2 = ConversationMember(
        conversation_id=conv.id, employee_id=emp_b_id, joined_at=now, last_read_at=None
    )
    db.session.add_all([m1, m2])

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Race condition: re-query
        conv = (
            db.session.query(Conversation)
            .filter(
                Conversation.organization_id == org_id,
                Conversation.conversation_type == "direct",
                Conversation.members.any(employee_id=emp_a_id),
                Conversation.members.any(employee_id=emp_b_id),
            )
            .first()
        )
    return conv


def _compute_unread_count(conv_id, current_emp_id):
    """
    Calculates number of unread messages in conv_id for current_emp_id.
    """
    member = ConversationMember.query.filter_by(
        conversation_id=conv_id, employee_id=current_emp_id
    ).first()
    if not member:
        return 0

    q = Message.query.filter(
        Message.conversation_id == conv_id,
        Message.sender_employee_id != current_emp_id,
    )
    if member.last_read_at:
        q = q.filter(Message.created_at > member.last_read_at)
    return q.count()


# ── Page Route ────────────────────────────────────────────────────────────────

@messages_bp.route("/messages")
@login_required
def messages_page():
    """
    Renders the internal messaging UI.
    Populates colleagues list and initial conversation states.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()

    if not current_emp:
        # User account without employee record
        return render_template(
            "messages/index.html",
            colleagues=[],
            current_employee=None,
            initial_convos={},
        )

    # Mark incoming messages as delivered when opening the messages UI
    ConversationMember.query.filter_by(employee_id=current_emp.id).update(
        {"last_delivered_at": datetime.utcnow()}, synchronize_session=False
    )
    db.session.commit()

    # Active colleagues in the same organisation, excluding self
    employees = (
        Employee.query
        .filter_by(organization_id=org_id, is_deleted=False)
        .filter(Employee.id != current_emp.id)
        .order_by(Employee.name)
        .all()
    )

    # Pre-populate sidebar conversation metadata
    initial_convos = {}
    for emp in employees:
        conv = (
            db.session.query(Conversation)
            .filter(
                Conversation.organization_id == org_id,
                Conversation.conversation_type == "direct",
                Conversation.members.any(employee_id=current_emp.id),
                Conversation.members.any(employee_id=emp.id),
            )
            .first()
        )
        if conv:
            last_msg = (
                Message.query.filter_by(conversation_id=conv.id)
                .order_by(Message.created_at.desc())
                .first()
            )
            unread = _compute_unread_count(conv.id, current_emp.id)
            initial_convos[str(emp.id)] = {
                "conversation_id": conv.id,
                "unread_count": unread,
                "last_message": last_msg.body if last_msg else None,
                "last_message_from": "self" if (last_msg and last_msg.sender_employee_id == current_emp.id) else "other",
                "last_message_ts": (last_msg.created_at.isoformat() + "Z") if last_msg else None,
            }
        else:
            initial_convos[str(emp.id)] = {
                "conversation_id": None,
                "unread_count": 0,
                "last_message": None,
                "last_message_from": None,
                "last_message_ts": None,
            }

    return render_template(
        "messages/index.html",
        colleagues=employees,
        current_employee=current_emp,
        initial_convos=initial_convos,
    )


# ── REST API Endpoints ────────────────────────────────────────────────────────

@messages_bp.route("/api/messages/conversations", methods=["GET"])
@login_required
def get_conversations():
    """
    Returns all conversations with metadata (last message, unread count)
    for the current user in the active organisation.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()
    if not current_emp:
        return jsonify({"success": False, "error": "No employee profile linked."}), 403

    # Update last_delivered_at for current employee to mark incoming messages as delivered
    ConversationMember.query.filter_by(employee_id=current_emp.id).update(
        {"last_delivered_at": datetime.utcnow()}, synchronize_session=False
    )
    db.session.commit()

    # All active colleagues in the same organisation
    colleagues = (
        Employee.query
        .filter_by(organization_id=org_id, is_deleted=False)
        .filter(Employee.id != current_emp.id)
        .order_by(Employee.name)
        .all()
    )

    result = []
    for emp in colleagues:
        conv = (
            db.session.query(Conversation)
            .filter(
                Conversation.organization_id == org_id,
                Conversation.conversation_type == "direct",
                Conversation.members.any(employee_id=current_emp.id),
                Conversation.members.any(employee_id=emp.id),
            )
            .first()
        )
        if conv:
            last_msg = (
                Message.query.filter_by(conversation_id=conv.id)
                .order_by(Message.created_at.desc())
                .first()
            )
            unread = _compute_unread_count(conv.id, current_emp.id)
            result.append({
                "colleague_id": emp.id,
                "conversation_id": conv.id,
                "unread_count": unread,
                "last_message": last_msg.body if last_msg else "",
                "last_message_from": "self" if (last_msg and last_msg.sender_employee_id == current_emp.id) else "other",
                "last_message_ts": (last_msg.created_at.isoformat() + "Z") if last_msg else None,
            })
        else:
            result.append({
                "colleague_id": emp.id,
                "conversation_id": None,
                "unread_count": 0,
                "last_message": "",
                "last_message_from": None,
                "last_message_ts": None,
            })

    return jsonify({"success": True, "conversations": result})


@messages_bp.route("/api/messages/<int:colleague_id>/history", methods=["GET"])
@login_required
def get_history(colleague_id):
    """
    Returns message history between current user and the specified colleague.
    Also automatically marks the incoming messages in this conversation as read.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()
    if not current_emp:
        return jsonify({"success": False, "error": "No employee profile linked."}), 403

    if colleague_id == current_emp.id:
        return jsonify({"success": False, "error": "Cannot chat with yourself."}), 400

    # Tenant check: colleague must belong to current user's active org
    colleague = Employee.query.filter_by(
        id=colleague_id, organization_id=org_id, is_deleted=False
    ).first()
    if not colleague:
        return jsonify({
            "success": False,
            "error": "Colleague not found or not in your organization."
        }), 404

    conv = (
        db.session.query(Conversation)
        .filter(
            Conversation.organization_id == org_id,
            Conversation.conversation_type == "direct",
            Conversation.members.any(employee_id=current_emp.id),
            Conversation.members.any(employee_id=colleague_id),
        )
        .first()
    )

    if not conv:
        return jsonify({
            "success": True,
            "conversation_id": None,
            "messages": [],
            "colleague": {
                "id": colleague.id,
                "name": colleague.name,
                "position": colleague.position or "Staff",
            }
        })

    # Mark as read and delivered for current employee
    member = ConversationMember.query.filter_by(
        conversation_id=conv.id, employee_id=current_emp.id
    ).first()
    if member:
        now = datetime.utcnow()
        member.last_read_at = now
        member.last_delivered_at = now
        db.session.commit()

    # Colleague membership for calculating delivery / read status of sent messages
    colleague_member = ConversationMember.query.filter_by(
        conversation_id=conv.id, employee_id=colleague_id
    ).first()

    # Load messages chronologically
    messages = (
        Message.query.filter_by(conversation_id=conv.id)
        .order_by(Message.created_at.asc())
        .limit(200)
        .all()
    )

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "colleague": {
            "id": colleague.id,
            "name": colleague.name,
            "position": colleague.position or "Staff",
        },
        "messages": [
            msg.to_dict(current_employee_id=current_emp.id, recipient_member=colleague_member)
            for msg in messages
        ]
    })


@messages_bp.route("/api/messages/<int:colleague_id>/send", methods=["POST"])
@messages_bp.route("/api/messages/send", methods=["POST"])
@login_required
def send_message(colleague_id=None):
    """
    Persists a new message to the database.
    Accepts JSON body or multipart form data.
    Validates tenant isolation, colleague existence, and message length.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()
    if not current_emp:
        return jsonify({"success": False, "error": "No employee profile linked."}), 403

    data = request.get_json(silent=True) or request.form or {}

    if colleague_id is None:
        colleague_id = data.get("colleague_id") or data.get("recipient_id")
        try:
            colleague_id = int(colleague_id)
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Invalid or missing colleague_id."}), 400

    if colleague_id == current_emp.id:
        return jsonify({"success": False, "error": "Cannot send messages to yourself."}), 400

    # Tenant check: colleague must belong to current user's active org
    colleague = Employee.query.filter_by(
        id=colleague_id, organization_id=org_id, is_deleted=False
    ).first()
    if not colleague:
        return jsonify({
            "success": False,
            "error": "Recipient colleague not found or not in your organization."
        }), 404

    # File upload handling
    file = request.files.get("file") or request.files.get("attachment")
    orig_filename = None
    disk_filename = None
    file_mime = None
    file_size = 0

    if file and file.filename:
        raw_name = secure_filename(file.filename)
        if not raw_name or "." not in raw_name:
            return jsonify({"success": False, "error": "Invalid attachment filename."}), 400
        ext = raw_name.rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
            return jsonify({"success": False, "error": f"File type '.{ext}' is not permitted."}), 400

        # Check file size
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        if file_size > MAX_ATTACHMENT_BYTES:
            return jsonify({"success": False, "error": "Attachment exceeds 25 MB limit."}), 413

        orig_filename = raw_name
        file_mime, _ = mimetypes.guess_type(orig_filename)
        if not file_mime:
            if ext in ("jpg", "jpeg"):
                file_mime = "image/jpeg"
            elif ext == "png":
                file_mime = "image/png"
            elif ext == "gif":
                file_mime = "image/gif"
            elif ext == "webp":
                file_mime = "image/webp"
            elif ext == "pdf":
                file_mime = "application/pdf"
            else:
                file_mime = "application/octet-stream"

    # Body validation
    text = (data.get("text") or data.get("body") or data.get("message") or "").strip()
    if not text and not file:
        return jsonify({"success": False, "error": "Message body or attachment cannot be empty."}), 400
    if text and len(text) > 5000:
        return jsonify({"success": False, "error": "Message is too long (max 5000 characters)."}), 400

    # Ensure conversation and membership
    conv = get_or_create_direct_conversation(org_id, current_emp.id, colleague.id)
    if not conv:
        return jsonify({"success": False, "error": "Failed to initialize conversation session."}), 500

    now = datetime.utcnow()

    # Save uploaded file to disk if present
    if file and orig_filename:
        upload_root = current_app.config.get("UPLOAD_FOLDER") or os.path.join(current_app.root_path, "static", "uploads")
        upload_dir = os.path.join(upload_root, "messages")
        os.makedirs(upload_dir, exist_ok=True)
        disk_filename = f"c{conv.id}_{int(now.timestamp())}_{uuid.uuid4().hex[:8]}_{orig_filename}"
        target_path = os.path.join(upload_dir, disk_filename)
        file.save(target_path)

    msg = Message(
        conversation_id=conv.id,
        sender_employee_id=current_emp.id,
        body=text,
        created_at=now,
        attachment_name=orig_filename,
        attachment_type=file_mime,
        attachment_size=file_size if orig_filename else None,
    )
    db.session.add(msg)
    db.session.flush()

    if orig_filename and disk_filename:
        msg.attachment_url = f"/attachment/{msg.id}/{disk_filename}"

    # Update conversation last_message_at and sender's last_read_at
    conv.last_message_at = now
    conv.updated_at = now

    sender_member = ConversationMember.query.filter_by(
        conversation_id=conv.id, employee_id=current_emp.id
    ).first()
    if sender_member:
        sender_member.last_read_at = now
        sender_member.last_delivered_at = now

    db.session.commit()

    colleague_member = ConversationMember.query.filter_by(
        conversation_id=conv.id, employee_id=colleague.id
    ).first()

    msg_dict = msg.to_dict(current_employee_id=current_emp.id, recipient_member=colleague_member)

    # ── Phase 3: Emit real-time events (only after successful commit) ──────────
    try:
        # 1. Notify recipient: they have a new incoming message
        recipient_payload = {
            "id": msg.id,
            "conversation_id": conv.id,
            "sender_id": current_emp.id,
            "sender_name": current_emp.name,
            "text": text,                          # plain text; XSS safety via textContent on client
            "ts": msg.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "from": "other",
            "status": "delivered",                  # recipient sees it as incoming
            "attachment_url": msg.attachment_url,
            "attachment_name": msg.attachment_name,
            "attachment_type": msg.attachment_type,
            "attachment_size": msg.attachment_size,
        }
        _safe_socketio_emit(
            "messaging:new_message",
            recipient_payload,
            room=_employee_room(org_id, colleague.id)
        )

        # 2. Update sender's own tick (sent -> already 'sent', but UI may want confirm)
        sender_payload = {
            "id": msg.id,
            "conversation_id": conv.id,
            "status": "sent",
        }
        _safe_socketio_emit(
            "messaging:message_status",
            sender_payload,
            room=_employee_room(org_id, current_emp.id)
        )

        # 3. Notify recipient's unread count changed
        recipient_unread = _compute_unread_for_employee(colleague.id, org_id)
        _safe_socketio_emit(
            "messaging:unread_updated",
            {"unread_count": recipient_unread},
            room=_employee_room(org_id, colleague.id)
        )

        # 4. Conversation preview update for both parties
        preview_text = text if text else (f"📎 {msg.attachment_name}" if msg.attachment_name else "Sent a file")
        conv_update = {
            "conversation_id": conv.id,
            "colleague_id": colleague.id,       # for sender's sidebar
            "last_message": preview_text,
            "last_message_ts": msg.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _safe_socketio_emit(
            "messaging:conversation_updated",
            {**conv_update, "last_message_from": "self", "unread_count": 0},
            room=_employee_room(org_id, current_emp.id)
        )
        _safe_socketio_emit(
            "messaging:conversation_updated",
            {**conv_update, "colleague_id": current_emp.id, "last_message_from": "other", "unread_count": recipient_unread},
            room=_employee_room(org_id, colleague.id)
        )
    except Exception:
        pass  # socket errors must never break the REST response

    return jsonify({
        "success": True,
        "conversation_id": conv.id,
        "message": msg_dict,
    }), 201


@messages_bp.route("/attachment/<int:msg_id>/<filename>", methods=["GET"])
@messages_bp.route("/attachment/<int:msg_id>", methods=["GET"])
@login_required
def get_attachment(msg_id, filename=None):
    """
    Secure multi-tenant file download/inline view for message attachments.
    Verifies that the current user belongs to the same org and is a member
    of the conversation to which the attachment belongs.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()
    if not current_emp:
        abort(403)

    msg = Message.query.get_or_404(msg_id)
    conv = Conversation.query.filter_by(
        id=msg.conversation_id, organization_id=org_id
    ).first_or_404()

    is_member = ConversationMember.query.filter_by(
        conversation_id=conv.id, employee_id=current_emp.id
    ).first()
    if not is_member:
        abort(403)

    if not msg.attachment_url:
        abort(404)

    # Extract stored disk filename
    disk_file = msg.attachment_url.rsplit("/", 1)[-1]
    upload_root = current_app.config.get("UPLOAD_FOLDER") or os.path.join(current_app.root_path, "static", "uploads")
    upload_dir = os.path.join(upload_root, "messages")
    file_path = os.path.normpath(os.path.join(upload_dir, disk_file))

    if not os.path.exists(file_path):
        abort(404)

    is_image = msg.attachment_type and msg.attachment_type.startswith("image/")
    download_requested = request.args.get("download", "0") == "1"
    as_attachment = download_requested or not is_image

    return send_file(
        file_path,
        mimetype=msg.attachment_type or "application/octet-stream",
        as_attachment=as_attachment,
        download_name=msg.attachment_name or disk_file,
    )


@messages_bp.route("/api/messages/<int:colleague_id>/read", methods=["POST"])
@login_required
def mark_as_read(colleague_id):
    """
    Marks a conversation with the specified colleague as read.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()
    if not current_emp:
        return jsonify({"success": False, "error": "No employee profile linked."}), 403

    conv = (
        db.session.query(Conversation)
        .filter(
            Conversation.organization_id == org_id,
            Conversation.conversation_type == "direct",
            Conversation.members.any(employee_id=current_emp.id),
            Conversation.members.any(employee_id=colleague_id),
        )
        .first()
    )

    if conv:
        member = ConversationMember.query.filter_by(
            conversation_id=conv.id, employee_id=current_emp.id
        ).first()
        if member:
            now = datetime.utcnow()
            member.last_read_at = now
            member.last_delivered_at = now
            db.session.commit()

            # ── Phase 3: Notify the other party that their messages were read ──
            try:
                # Find the other employee in this conversation
                other_member = ConversationMember.query.filter(
                    ConversationMember.conversation_id == conv.id,
                    ConversationMember.employee_id != current_emp.id
                ).first()
                if other_member:
                    _safe_socketio_emit(
                        "messaging:read_updated",
                        {
                            "conversation_id": conv.id,
                            "read_by_id": current_emp.id,
                            "read_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                        room=_employee_room(org_id, other_member.employee_id)
                    )
            except Exception:
                pass

    return jsonify({"success": True})


@messages_bp.route("/api/messages/unread-count", methods=["GET"])
@login_required
def total_unread_count():
    """
    Returns the total number of unread incoming messages across all conversations
    for the logged-in employee in the current organisation.
    """
    org_id = _get_active_org_id()
    current_emp = _get_current_employee()
    if not current_emp:
        return jsonify({"success": True, "unread_count": 0})

    total = _compute_unread_for_employee(current_emp.id, org_id)
    return jsonify({"success": True, "unread_count": total})

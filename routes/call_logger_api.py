"""
Call Logger API — secure endpoint for Android Call Logger app.
Authentication: Device Bearer token (SHA-256 hashed in DB).
"""
import hashlib
import re
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app
from flask_login import current_user
from model import (
    db,
    Employee,
    Lead,
    Contact,
)
from model import CallLog, CallDevice, CallType, CallFollowUpStatus, DeviceStatus
from utils.extensions import limiter
from utils.timezone import format_ist

call_logger_api_bp = Blueprint("call_logger_api", __name__, url_prefix="/api/v1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_phone(number: str) -> str:
    """Normalize Indian mobile numbers to +91XXXXXXXXXX format."""
    if not number:
        return ""
    # Strip all non-digit characters except leading +
    cleaned = re.sub(r"[^\d+]", "", number.strip())
    # Remove leading + for processing
    digits = cleaned.lstrip("+")
    # Remove country code 91 if present
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    # Reattach country code
    if len(digits) == 10:
        return f"+91{digits}"
    # Fallback: return original cleaned form
    return cleaned if cleaned.startswith("+") else f"+{cleaned}"


def _authenticate_device(auth_header: str):
    """
    Validate Bearer token from Authorization header.
    Returns (CallDevice, None) on success or (None, error_message) on failure.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return None, "Missing or malformed Authorization header"

    raw_token = auth_header[7:]  # strip "Bearer "
    if not raw_token:
        return None, "Empty token"

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    device = CallDevice.query.filter_by(
        credential_hash=token_hash,
        status=DeviceStatus.ACTIVE,
    ).first()

    if not device:
        return None, "Invalid or revoked device credential"

    return device, None


def _find_lead_by_phone(org_id: int, normalized_number: str):
    """Try to match a phone number to an existing Lead in this org."""
    # Try exact match first
    lead = Lead.query.filter_by(
        organization_id=org_id,
        phone_number=normalized_number,
        is_deleted=False,
    ).first()
    if lead:
        return lead
    # Try last-10-digit match (handles formatting variance)
    last10 = normalized_number[-10:] if len(normalized_number) >= 10 else None
    if last10:
        all_leads = Lead.query.filter_by(
            organization_id=org_id, is_deleted=False
        ).all()
        for l in all_leads:
            if l.phone_number and l.phone_number[-10:] == last10:
                return l
    return None


def _find_contact_by_phone(org_id: int, normalized_number: str):
    """Try to match a phone number to an existing Contact in this org.

    Contact model phone fields (from model.py):
        - phone_number   (primary, required, unique per org)
        - secondary_phone (optional)
        - whatsapp_number (optional)
    """
    # Exact primary-number match first
    contact = Contact.query.filter_by(
        organization_id=org_id,
        phone_number=normalized_number,
        is_deleted=False,
    ).first()
    if contact:
        return contact
    # Last-10-digit fallback: also checks secondary_phone and whatsapp_number
    last10 = normalized_number[-10:] if len(normalized_number) >= 10 else None
    if last10:
        all_contacts = Contact.query.filter_by(
            organization_id=org_id, is_deleted=False
        ).all()
        for c in all_contacts:
            for field in (c.phone_number, c.secondary_phone, c.whatsapp_number):
                if field and field[-10:] == last10:
                    return c
    return None



# ---------------------------------------------------------------------------
# POST /api/v1/call-logs — Receive call from Android app
# ---------------------------------------------------------------------------

@call_logger_api_bp.route("/call-logs", methods=["POST"])
@limiter.limit("120 per minute; 1000 per hour")
def receive_call_log():
    """
    Authenticated endpoint for Android Call Logger app.

    Expected JSON body:
    {
        "caller_number": "+919XXXXXXXXX",
        "call_type": "missed|received|outgoing",
        "call_status": "missed|completed|...",
        "started_at": "2026-08-17T12:00:00",
        "ended_at": "2026-08-17T12:02:31",
        "duration": 151,
        "device_identifier": "...",       -- optional, device info from Android
        "subscription_id": "...",         -- optional, SIM slot identifier
        "client_event_id": "unique-uuid"  -- idempotency key
    }
    """
    # 1. Authenticate device
    auth_header = request.headers.get("Authorization", "")
    device, auth_error = _authenticate_device(auth_header)
    if not device:
        current_app.logger.warning(
            "Call Logger API: authentication failed — %s | IP: %s",
            auth_error,
            request.remote_addr,
        )
        return jsonify({"success": False, "message": auth_error}), 401

    # Update last_seen for device
    device.last_seen = datetime.utcnow()

    # 2. Parse JSON body
    if not request.is_json:
        return jsonify({"success": False, "message": "Content-Type must be application/json"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Invalid JSON body"}), 400

    # 3. Validate required fields
    errors = {}
    caller_number_raw = data.get("caller_number", "")
    if not caller_number_raw:
        errors["caller_number"] = "Required"

    call_type_raw = data.get("call_type", "").lower()
    valid_call_types = {e.value for e in CallType}
    if call_type_raw not in valid_call_types:
        errors["call_type"] = f"Must be one of: {', '.join(valid_call_types)}"

    started_at_raw = data.get("started_at")
    if not started_at_raw:
        errors["started_at"] = "Required"

    if errors:
        return jsonify({"success": False, "message": "Validation error", "errors": errors}), 422

    # 4. Idempotency — deduplicate by client_event_id
    client_event_id = data.get("client_event_id", "").strip() or None
    if client_event_id:
        existing = CallLog.query.filter_by(
            client_event_id=client_event_id,
            organization_id=device.organization_id,
        ).first()
        if existing:
            db.session.commit()  # flush last_seen
            return jsonify({
                "success": True,
                "message": "Already logged (idempotent)",
                "call_id": existing.id,
            }), 200

    # 5. Normalize phone number
    caller_number = _normalize_phone(caller_number_raw)

    # 6. Parse timestamps
    def _parse_dt(raw):
        if not raw:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                return datetime.strptime(str(raw), fmt)
            except ValueError:
                continue
        return None

    started_at = _parse_dt(started_at_raw)
    if not started_at:
        return jsonify({"success": False, "message": "Invalid started_at format. Use ISO 8601."}), 422

    ended_at = _parse_dt(data.get("ended_at"))
    duration = data.get("duration")
    if duration is not None:
        try:
            duration = int(duration)
        except (ValueError, TypeError):
            duration = None

    # 7. Determine call_type
    call_type = CallType(call_type_raw)

    # 8. Match caller to existing Lead / Contact
    org_id = device.organization_id
    lead = _find_lead_by_phone(org_id, caller_number)
    contact = _find_contact_by_phone(org_id, caller_number) if not lead else None

    caller_name_snapshot = None
    lead_id = None
    contact_id = None

    if lead:
        lead_id = lead.id
        caller_name_snapshot = lead.name
    elif contact:
        contact_id = contact.id
        caller_name_snapshot = contact.name

    # 9. Determine initial follow_up_status
    follow_up_status = (
        CallFollowUpStatus.PENDING
        if call_type == CallType.MISSED
        else CallFollowUpStatus.NOT_REQUIRED
    )

    # 10. Create CallLog
    call_log = CallLog(
        organization_id=org_id,
        device_id=device.id,
        employee_id=device.employee_id,
        caller_number=caller_number,
        caller_name_snapshot=caller_name_snapshot,
        lead_id=lead_id,
        contact_id=contact_id,
        call_type=call_type,
        call_status=data.get("call_status", call_type_raw),
        started_at=started_at,
        ended_at=ended_at,
        duration=duration,
        subscription_id=data.get("subscription_id", ""),
        client_event_id=client_event_id,
        follow_up_status=follow_up_status,
    )
    db.session.add(call_log)

    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Call Logger API: DB error saving call log")
        return jsonify({"success": False, "message": "Internal server error"}), 500

    # 11. Emit real-time event via Socket.IO
    try:
        from utils.extensions import socketio
        payload = {
            "id": call_log.id,
            "caller_number": call_log.caller_number,
            "call_type": call_log.call_type.value,
            "call_status": call_log.call_status,
            "started_at": call_log.started_at.isoformat() + "Z" if call_log.started_at else None,
            "started_at_display": format_ist(call_log.started_at) if call_log.started_at else "",
            "ended_at": call_log.ended_at.isoformat() + "Z" if call_log.ended_at else None,
            "duration": call_log.duration,
            "employee": call_log.employee.name if call_log.employee else "Unknown",
            "employee_id": call_log.employee_id,
            "matched_lead": lead.name if lead else None,
            "lead_id": lead.id if lead else None,
            "matched_contact": contact.name if contact else None,
            "contact_id": contact.id if contact else None,
            "created_at": call_log.created_at.isoformat() + "Z" if call_log.created_at else None,
            "follow_up_status": call_log.follow_up_status.value
        }
        
        # Emit to all active employees in this organization using the existing room pattern
        org_employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
        for emp in org_employees:
            room = f"org_{org_id}_user_{emp.id}"
            socketio.emit("call_log_created", payload, room=room)
    except Exception as e:
        current_app.logger.error(f"Socket.IO emit failed for call_logger: {e}")

    return jsonify({
        "success": True,
        "message": "Call log created",
        "call_id": call_log.id,
        "matched_lead": lead.name if lead else None,
        "matched_contact": contact.name if contact else None,
    }), 201


# ---------------------------------------------------------------------------
# GET /api/v1/call-logs/ping — Health / connectivity check for Android app
# ---------------------------------------------------------------------------

@call_logger_api_bp.route("/call-logs/ping", methods=["GET"])
@limiter.limit("60 per minute")
def ping():
    auth_header = request.headers.get("Authorization", "")
    device, auth_error = _authenticate_device(auth_header)
    if not device:
        return jsonify({"success": False, "message": auth_error}), 401

    device.last_seen = datetime.utcnow()
    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Connected",
        "device_name": device.device_name,
        "employee": device.employee.name if device.employee else None,
        "server_time": datetime.utcnow().isoformat(),
    }), 200

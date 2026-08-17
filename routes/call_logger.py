"""
Call Logger & Call Monitoring — browser-side routes.
Provides: dashboard, call detail, settings, device management.
"""
import secrets
import hashlib
from datetime import datetime, date, timedelta

from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    abort,
    jsonify,
    current_app,
)
from flask_login import login_required, current_user
from model import (
    db,
    Employee,
    Lead,
    Contact,
    LeadFollowUp,
    FollowUpMethod,
    FollowUpOutcome,
    LeadSource,
    LeadStatus,
)
from model import CallLog, CallDevice, CallType, CallFollowUpStatus, DeviceStatus
from utils.activity import log_activity

call_logger_bp = Blueprint("call_logger", __name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_admin_or_manager():
    """Abort 403 unless current user is admin or manager."""
    if not current_user.is_authenticated:
        abort(401)
    if current_user.role.value not in ("admin", "manager"):
        abort(403)


def _org_employees():
    return Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()


def _call_query_base():
    """Base query scoped to the current organisation."""
    q = CallLog.query.filter_by(organization_id=current_user.organization_id)
    # Non-admin employees see only their own calls
    if current_user.role.value == "employee" and current_user.employee:
        q = q.filter_by(employee_id=current_user.employee.id)
    return q


# ---------------------------------------------------------------------------
# Call Monitoring Dashboard
# ---------------------------------------------------------------------------

@call_logger_bp.route("/call-monitoring")
@login_required
def dashboard():
    org_id = current_user.organization_id

    # --- Filters ---
    employee_filter = request.args.get("employee_id", "", type=str)
    call_type_filter = request.args.get("call_type", "")
    date_filter = request.args.get("date_range", "this_month")
    status_filter = request.args.get("follow_up_status", "")
    search_q = request.args.get("q", "").strip()

    # --- Date range ---
    today = date.today()
    if date_filter == "today":
        start_date = today
        end_date = today
    elif date_filter == "yesterday":
        start_date = today - timedelta(days=1)
        end_date = today - timedelta(days=1)
    elif date_filter == "this_week":
        start_date = today - timedelta(days=today.weekday())
        end_date = today
    elif date_filter == "this_month":
        start_date = today.replace(day=1)
        end_date = today
    elif date_filter == "custom":
        try:
            start_date = datetime.strptime(request.args.get("start_date", ""), "%Y-%m-%d").date()
            end_date = datetime.strptime(request.args.get("end_date", ""), "%Y-%m-%d").date()
        except ValueError:
            start_date = today.replace(day=1)
            end_date = today
    else:
        start_date = today.replace(day=1)
        end_date = today

    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.max.time())

    # --- Build base query ---
    base = _call_query_base().filter(
        CallLog.started_at >= start_dt,
        CallLog.started_at <= end_dt,
    )

    # --- Apply filters ---
    if employee_filter and employee_filter.isdigit():
        base = base.filter(CallLog.employee_id == int(employee_filter))

    if call_type_filter and call_type_filter in [e.value for e in CallType]:
        base = base.filter(CallLog.call_type == CallType(call_type_filter))

    if status_filter and status_filter in [e.value for e in CallFollowUpStatus]:
        base = base.filter(CallLog.follow_up_status == CallFollowUpStatus(status_filter))

    if search_q:
        pattern = f"%{search_q}%"
        # Search by caller_number or linked lead/contact name
        base = base.filter(
            db.or_(
                CallLog.caller_number.ilike(pattern),
                CallLog.caller_name_snapshot.ilike(pattern),
            )
        )

    # --- Dashboard aggregate counts (for current filter scope) ---
    total_calls = base.count()
    received_calls = base.filter(CallLog.call_type == CallType.RECEIVED).count()
    missed_calls = base.filter(CallLog.call_type == CallType.MISSED).count()
    outgoing_calls = base.filter(CallLog.call_type == CallType.OUTGOING).count()
    pending_followups = base.filter(
        CallLog.follow_up_status == CallFollowUpStatus.PENDING
    ).count()

    # Rebuild after the count (filters were chained — re-run full query for list)
    list_q = _call_query_base().filter(
        CallLog.started_at >= start_dt,
        CallLog.started_at <= end_dt,
    )
    if employee_filter and employee_filter.isdigit():
        list_q = list_q.filter(CallLog.employee_id == int(employee_filter))
    if call_type_filter and call_type_filter in [e.value for e in CallType]:
        list_q = list_q.filter(CallLog.call_type == CallType(call_type_filter))
    if status_filter and status_filter in [e.value for e in CallFollowUpStatus]:
        list_q = list_q.filter(CallLog.follow_up_status == CallFollowUpStatus(status_filter))
    if search_q:
        pattern = f"%{search_q}%"
        list_q = list_q.filter(
            db.or_(
                CallLog.caller_number.ilike(pattern),
                CallLog.caller_name_snapshot.ilike(pattern),
            )
        )

    page = request.args.get("page", 1, type=int)
    per_page = 50
    pagination = list_q.order_by(CallLog.started_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    calls = pagination.items

    employees = _org_employees()

    return render_template(
        "call_logger/dashboard.html",
        calls=calls,
        pagination=pagination,
        employees=employees,
        total_calls=total_calls,
        received_calls=received_calls,
        missed_calls=missed_calls,
        outgoing_calls=outgoing_calls,
        pending_followups=pending_followups,
        # filter state
        employee_filter=employee_filter,
        call_type_filter=call_type_filter,
        date_filter=date_filter,
        status_filter=status_filter,
        search_q=search_q,
        start_date=start_date,
        end_date=end_date,
        CallType=CallType,
        CallFollowUpStatus=CallFollowUpStatus,
    )


# ---------------------------------------------------------------------------
# Call Detail
# ---------------------------------------------------------------------------

@call_logger_bp.route("/call-monitoring/call/<int:call_id>")
@login_required
def call_detail(call_id):
    call = _call_query_base().filter(CallLog.id == call_id).first_or_404()
    employees = _org_employees()
    # Pass leads for the "link existing lead" modal
    leads = Lead.query.filter_by(
        organization_id=current_user.organization_id,
        is_deleted=False,
    ).order_by(Lead.name.asc()).all()
    return render_template(
        "call_logger/call_detail.html",
        call=call,
        employees=employees,
        leads=leads,
        CallType=CallType,
        CallFollowUpStatus=CallFollowUpStatus,
    )


# ---------------------------------------------------------------------------
# Update Follow-up Status (AJAX / form)
# ---------------------------------------------------------------------------

@call_logger_bp.route("/call-monitoring/call/<int:call_id>/follow-up", methods=["POST"])
@login_required
def update_followup(call_id):
    call = _call_query_base().filter(CallLog.id == call_id).first_or_404()

    fu_status = request.form.get("follow_up_status", "")
    fu_date = request.form.get("follow_up_date", "")
    fu_time = request.form.get("follow_up_time", "")
    fu_notes = request.form.get("follow_up_notes", "")
    assigned_emp = request.form.get("assigned_employee_id", type=int)

    if fu_status in [e.value for e in CallFollowUpStatus]:
        call.follow_up_status = CallFollowUpStatus(fu_status)

    if fu_notes:
        call.follow_up_notes = fu_notes

    # If a lead is linked and we have a date, create a LeadFollowUp
    if call.lead_id and fu_date:
        try:
            fu_datetime_str = f"{fu_date} {fu_time}" if fu_time else fu_date
            fmt = "%Y-%m-%d %H:%M" if fu_time else "%Y-%m-%d"
            fu_datetime = datetime.strptime(fu_datetime_str, fmt)

            creator_emp = current_user.employee
            if creator_emp:
                fu = LeadFollowUp(
                    lead_id=call.lead_id,
                    method=FollowUpMethod.CALL,
                    notes=fu_notes or f"Follow-up from missed call — {call.caller_number}",
                    follow_up_date=fu_datetime,
                    organization_id=current_user.organization_id,
                    created_by=creator_emp.id,
                )
                db.session.add(fu)
                call.follow_up_status = CallFollowUpStatus.PENDING
        except ValueError:
            flash("Invalid follow-up date/time format.", "error")
            return redirect(url_for("call_logger.call_detail", call_id=call_id))

    db.session.commit()
    flash("Follow-up updated successfully.", "success")
    return redirect(url_for("call_logger.call_detail", call_id=call_id))


# ---------------------------------------------------------------------------
# Create Lead from Call
# ---------------------------------------------------------------------------

@call_logger_bp.route("/call-monitoring/call/<int:call_id>/create-lead")
@login_required
def create_lead_from_call(call_id):
    call = _call_query_base().filter(CallLog.id == call_id).first_or_404()
    # Pre-populate the add-lead URL with the phone number
    return redirect(
        url_for("leads.add_lead") + f"?phone_number={call.caller_number}&call_id={call_id}"
    )


# ---------------------------------------------------------------------------
# Link existing lead to a call (AJAX)
# ---------------------------------------------------------------------------

@call_logger_bp.route("/call-monitoring/call/<int:call_id>/link-lead", methods=["POST"])
@login_required
def link_lead_to_call(call_id):
    call = _call_query_base().filter(CallLog.id == call_id).first_or_404()
    lead_id = request.form.get("lead_id", type=int)
    if lead_id:
        lead = Lead.query.filter_by(
            id=lead_id,
            organization_id=current_user.organization_id,
            is_deleted=False,
        ).first()
        if lead:
            call.lead_id = lead.id
            call.caller_name_snapshot = lead.name
            db.session.commit()
            flash(f"Call linked to lead: {lead.name}", "success")
        else:
            flash("Lead not found.", "error")
    return redirect(url_for("call_logger.call_detail", call_id=call_id))


# ---------------------------------------------------------------------------
# Settings — Call Logger
# ---------------------------------------------------------------------------

@call_logger_bp.route("/settings/call-logger")
@login_required
def cl_settings():
    org_id = current_user.organization_id
    
    query = CallDevice.query.filter_by(organization_id=org_id)
    if current_user.role.value == "employee" and current_user.employee:
        query = query.filter_by(employee_id=current_user.employee.id)
        employees = [current_user.employee]
    else:
        employees = _org_employees()
        
    devices = query.order_by(CallDevice.created_at.desc()).all()

    # Stats
    active_devices = sum(1 for d in devices if d.status == DeviceStatus.ACTIVE)
    revoked_devices = sum(1 for d in devices if d.status == DeviceStatus.REVOKED)

    return render_template(
        "call_logger/settings.html",
        devices=devices,
        employees=employees,
        active_devices=active_devices,
        revoked_devices=revoked_devices,
        DeviceStatus=DeviceStatus,
        active_tab="call_logger",
    )


# ---------------------------------------------------------------------------
# Add Device
# ---------------------------------------------------------------------------

@call_logger_bp.route("/settings/call-logger/add-device", methods=["POST"])
@login_required
def add_device():
    device_name = request.form.get("device_name", "").strip()
    
    if current_user.role.value == "employee" and current_user.employee:
        employee_id = current_user.employee.id
    else:
        employee_id = request.form.get("employee_id", type=int)

    if not device_name or not employee_id:
        flash("Device name and employee are required.", "error")
        return redirect(url_for("call_logger.cl_settings"))

    emp = Employee.query.filter_by(
        id=employee_id, organization_id=current_user.organization_id, is_deleted=False
    ).first()
    if not emp:
        flash("Employee not found.", "error")
        return redirect(url_for("call_logger.cl_settings"))

    # Generate a secure random token
    raw_token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    device = CallDevice(
        organization_id=current_user.organization_id,
        device_name=device_name,
        employee_id=employee_id,
        credential_hash=token_hash,
        status=DeviceStatus.ACTIVE,
        created_by=current_user.employee.id if current_user.employee else None,
    )
    db.session.add(device)
    db.session.commit()

    log_activity(
        action="call_device_registered",
        entity_type="call_device",
        entity_name=device_name,
        org_id=current_user.organization_id,
        description=f"Device '{device_name}' registered for employee '{emp.name}'",
    )

    # Show token once — pass in session flash; never stored again
    flash(f"DEVICE_TOKEN:{raw_token}:{device.id}", "device_token")
    return redirect(url_for("call_logger.cl_settings"))


# ---------------------------------------------------------------------------
# Revoke Device
# ---------------------------------------------------------------------------

@call_logger_bp.route("/settings/call-logger/revoke-device/<int:device_id>", methods=["POST"])
@login_required
def revoke_device(device_id):
    query = CallDevice.query.filter_by(
        id=device_id, organization_id=current_user.organization_id
    )
    if current_user.role.value == "employee" and current_user.employee:
        query = query.filter_by(employee_id=current_user.employee.id)
        
    device = query.first_or_404()

    device.status = DeviceStatus.REVOKED
    device.revoked_at = datetime.utcnow()
    db.session.commit()

    log_activity(
        action="call_device_revoked",
        entity_type="call_device",
        entity_name=device.device_name,
        org_id=current_user.organization_id,
        description=f"Device '{device.device_name}' revoked",
    )

    flash(f"Device '{device.device_name}' has been revoked.", "success")
    return redirect(url_for("call_logger.cl_settings"))


# ---------------------------------------------------------------------------
# Reactivate Device
# ---------------------------------------------------------------------------

@call_logger_bp.route("/settings/call-logger/activate-device/<int:device_id>", methods=["POST"])
@login_required
def activate_device(device_id):
    query = CallDevice.query.filter_by(
        id=device_id, organization_id=current_user.organization_id
    )
    if current_user.role.value == "employee" and current_user.employee:
        query = query.filter_by(employee_id=current_user.employee.id)
        
    device = query.first_or_404()

    device.status = DeviceStatus.ACTIVE
    device.revoked_at = None
    db.session.commit()

    flash(f"Device '{device.device_name}' has been reactivated.", "success")
    return redirect(url_for("call_logger.cl_settings"))


# ---------------------------------------------------------------------------
# Regenerate Device Credential
# ---------------------------------------------------------------------------

@call_logger_bp.route(
    "/settings/call-logger/regenerate-device/<int:device_id>", methods=["POST"]
)
@login_required
def regenerate_device(device_id):
    query = CallDevice.query.filter_by(
        id=device_id, organization_id=current_user.organization_id
    )
    if current_user.role.value == "employee" and current_user.employee:
        query = query.filter_by(employee_id=current_user.employee.id)
        
    device = query.first_or_404()

    raw_token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    device.credential_hash = token_hash
    device.status = DeviceStatus.ACTIVE
    device.revoked_at = None
    db.session.commit()

    log_activity(
        action="call_device_credential_regenerated",
        entity_type="call_device",
        entity_name=device.device_name,
        org_id=current_user.organization_id,
        description=f"Credential regenerated for device '{device.device_name}'",
    )

    flash(f"DEVICE_TOKEN:{raw_token}:{device.id}", "device_token")
    return redirect(url_for("call_logger.cl_settings"))

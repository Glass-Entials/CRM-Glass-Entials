"""
super_admin/routes.py
All HTTP routes for the Super Admin portal.

Blueprint URL prefix: /admin
Phase 1: Authentication, OTP, Dashboard
Phase 2: Organization management
Phase 2.5: Advanced org management, member CRUD, plans, limits, audit log, export
"""

import logging
import csv
import io
import os
import zipfile
import random
import string
from datetime import datetime

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    send_file,
)
from utils.extensions import limiter
from utils.email_service import send_html_email

from .auth import (
    verify_credentials,
    generate_otp,
    build_otp_session_data,
    validate_otp,
    SUPER_ADMIN_EMAIL,
    OTP_EXPIRY_MINUTES,
)
from .decorators import super_admin_required

logger = logging.getLogger(__name__)

super_admin_bp = Blueprint(
    "super_admin",
    __name__,
    url_prefix="/admin",
    template_folder="templates",
    static_folder="static",
    static_url_path="/super-admin-static",
)

# ---------------------------------------------------------------------------
# Session keys
# ---------------------------------------------------------------------------
_SA_AUTH_KEY = "super_admin_authenticated"
_SA_OTP_KEY = "super_admin_otp_data"
_SA_PENDING_KEY = "super_admin_pending_auth"


# ---------------------------------------------------------------------------
# Helper: send OTP email
# ---------------------------------------------------------------------------
def _send_otp_email(otp: str) -> bool:
    subject = "🔐 Super Admin OTP – GlassEntials"
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; margin: 0; padding: 40px 20px;">
      <div style="max-width: 480px; margin: 0 auto; background: #1e293b; border-radius: 16px; overflow: hidden; box-shadow: 0 25px 50px rgba(0,0,0,0.5);">
        <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); padding: 32px; text-align: center;">
          <h1 style="color: #fff; margin: 0; font-size: 22px; letter-spacing: -0.5px;">GlassEntials Super Admin</h1>
          <p style="color: rgba(255,255,255,0.75); margin: 8px 0 0; font-size: 14px;">Secure Login Verification</p>
        </div>
        <div style="padding: 40px 32px; text-align: center;">
          <p style="color: #94a3b8; font-size: 15px; margin-top: 0;">Your one-time login code is:</p>
          <div style="display: inline-block; background: #0f172a; border: 2px solid #6366f1; border-radius: 12px; padding: 20px 40px; margin: 16px 0;">
            <span style="font-size: 42px; font-weight: 700; letter-spacing: 12px; color: #a5b4fc; font-family: 'Courier New', monospace;">{otp}</span>
          </div>
          <p style="color: #64748b; font-size: 13px; margin-top: 16px;">
            ⏱ Expires in <strong style="color: #94a3b8;">{OTP_EXPIRY_MINUTES} minutes</strong><br>
            🔒 This code is single-use and will be invalidated after verification.
          </p>
        </div>
      </div>
    </body>
    </html>
    """
    text_body = f"Your GlassEntials Super Admin OTP is: {otp}\nExpires in {OTP_EXPIRY_MINUTES} minutes."
    return send_html_email(SUPER_ADMIN_EMAIL, subject, html_body, text_body)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _generate_unique_code(length=6):
    from model import Organization
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        if not Organization.query.filter_by(unique_code=code).first():
            return code


def _audit(org_id, action, details=None, meta=None):
    from services.org_limits import log_audit
    log_audit(org_id, action, "Super Admin", details, meta)


# ---------------------------------------------------------------------------
# Routes — Auth
# ---------------------------------------------------------------------------

@super_admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute; 100 per hour")
def login():
    if session.get(_SA_AUTH_KEY):
        return redirect(url_for("super_admin.dashboard"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            error = "Username and password are required."
        elif verify_credentials(username, password):
            otp = generate_otp()
            session[_SA_OTP_KEY] = build_otp_session_data(otp)
            session[_SA_PENDING_KEY] = True
            session.modified = True
            sent = _send_otp_email(otp)
            if not sent:
                flash("Warning: OTP email could not be sent.", "sa_warning")
            return redirect(url_for("super_admin.verify_otp"))
        else:
            error = "Invalid username or password."

    return render_template("super_admin/login.html", error=error)


@super_admin_bp.route("/otp", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def verify_otp():
    if not session.get(_SA_PENDING_KEY):
        return redirect(url_for("super_admin.login"))

    error = None
    if request.method == "POST":
        action = request.form.get("action", "verify")
        if action == "resend":
            otp = generate_otp()
            session[_SA_OTP_KEY] = build_otp_session_data(otp)
            session.modified = True
            sent = _send_otp_email(otp)
            if sent:
                flash("A new OTP has been sent to your email.", "sa_info")
            else:
                flash("Failed to send OTP email.", "sa_warning")
            return redirect(url_for("super_admin.verify_otp"))

        submitted_otp = request.form.get("otp", "").strip()
        otp_data = session.get(_SA_OTP_KEY)
        valid, reason = validate_otp(otp_data, submitted_otp)
        if valid:
            if otp_data:
                otp_data["otp_used"] = True
                session[_SA_OTP_KEY] = otp_data
            session[_SA_AUTH_KEY] = True
            session[_SA_PENDING_KEY] = False
            session.permanent = True
            session.modified = True
            return redirect(url_for("super_admin.dashboard"))
        else:
            error = reason

    return render_template("super_admin/otp.html", error=error, otp_expiry=OTP_EXPIRY_MINUTES)


@super_admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    return render_template("super_admin/dashboard.html")


@super_admin_bp.route("/logout")
def logout():
    session.pop(_SA_AUTH_KEY, None)
    session.pop(_SA_OTP_KEY, None)
    session.pop(_SA_PENDING_KEY, None)
    session.modified = True
    return redirect(url_for("super_admin.login"))


# ---------------------------------------------------------------------------
# Routes — Organizations List
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations")
@super_admin_required
def organizations():
    from model import Organization, OrganizationStatus, db
    orgs = Organization.query.order_by(Organization.created_at.desc()).all()
    return render_template(
        "super_admin/organizations.html",
        orgs=orgs,
        OrganizationStatus=OrganizationStatus
    )


# ---------------------------------------------------------------------------
# Routes — Organization Detail (Phase 2.5)
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>")
@super_admin_required
def organization_detail(org_id):
    from model import (
        Organization, OrganizationMember, OrgMemberRole, OrganizationStatus,
        Plan, Customer, Project, Task, Employee, User, db
    )
    from sqlalchemy import func

    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    members = (
        OrganizationMember.query
        .filter_by(organization_id=org_id)
        .order_by(OrganizationMember.role, OrganizationMember.joined_at)
        .all()
    )

    plans = Plan.query.filter_by(is_active=True).order_by(Plan.id).all()

    # Usage stats (efficient single queries per entity)
    customer_count = Customer.query.filter_by(organization_id=org_id, is_deleted=False).count()
    project_count = Project.query.filter_by(organization_id=org_id, is_deleted=False).count()
    task_count = Task.query.filter_by(organization_id=org_id, is_deleted=False).count()
    employee_count = Employee.query.filter_by(organization_id=org_id, is_deleted=False).count()

    # Audit log (latest 50)
    audit_logs = (
        org.audit_logs
        .order_by(db.text("created_at DESC"))
        .limit(50)
        .all()
    )

    return render_template(
        "super_admin/org_detail.html",
        org=org,
        members=members,
        plans=plans,
        OrgMemberRole=OrgMemberRole,
        OrganizationStatus=OrganizationStatus,
        customer_count=customer_count,
        project_count=project_count,
        task_count=task_count,
        employee_count=employee_count,
        audit_logs=audit_logs,
    )


# ---------------------------------------------------------------------------
# Routes — Suspend / Activate
# ---------------------------------------------------------------------------

SUSPENSION_REASONS = [
    "Payment overdue",
    "Policy violation",
    "Security issue",
    "Customer requested suspension",
    "Abuse",
    "Other",
]

@super_admin_bp.route("/organizations/<int:org_id>/suspend", methods=["POST"])
@super_admin_required
def suspend_organization(org_id):
    from model import Organization, OrganizationStatus, db
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    reason = request.form.get("reason", "Other").strip()
    note = request.form.get("note", "").strip()

    org.status = OrganizationStatus.SUSPENDED
    org.is_active = False
    org.suspended_at = datetime.utcnow()
    org.suspended_by = "Super Admin"
    org.suspension_reason = reason
    org.suspension_note = note or None

    _audit(org_id, "organization_suspended", f"Suspended: {reason}. {note}",
           {"reason": reason, "note": note})
    db.session.commit()
    flash(f"Organization '{org.name}' has been suspended.", "success")

    # Redirect back to detail page if came from there
    if request.referrer and f"/organizations/{org_id}" in request.referrer:
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))
    return redirect(url_for("super_admin.organizations"))


@super_admin_bp.route("/organizations/<int:org_id>/activate", methods=["POST"])
@super_admin_required
def activate_organization(org_id):
    from model import Organization, OrganizationStatus, db
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    org.status = OrganizationStatus.ACTIVE
    org.is_active = True
    org.suspended_at = None
    org.suspended_by = None
    org.suspension_reason = None
    org.suspension_note = None

    _audit(org_id, "organization_activated", "Organization activated by Super Admin")
    db.session.commit()
    flash(f"Organization '{org.name}' has been activated.", "success")

    if request.referrer and f"/organizations/{org_id}" in request.referrer:
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))
    return redirect(url_for("super_admin.organizations"))


# ---------------------------------------------------------------------------
# Routes — Archive / Delete
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>/archive", methods=["POST"])
@super_admin_required
def archive_organization(org_id):
    from model import Organization, OrganizationStatus, db
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    org.status = OrganizationStatus.ARCHIVED
    org.is_active = False
    _audit(org_id, "organization_archived", "Organization archived by Super Admin")
    db.session.commit()
    flash(f"Organization '{org.name}' has been archived.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


@super_admin_bp.route("/organizations/<int:org_id>/delete", methods=["POST"])
@super_admin_required
def delete_organization(org_id):
    from model import Organization, db
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    confirm_name = request.form.get("confirm_name", "").strip()
    if confirm_name != org.name:
        flash("Organization name did not match. Deletion cancelled.", "error")
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    org_name = org.name
    # Note: cascades will remove members, audit logs etc.
    db.session.delete(org)
    db.session.commit()
    flash(f"Organization '{org_name}' has been permanently deleted.", "success")
    return redirect(url_for("super_admin.organizations"))


# ---------------------------------------------------------------------------
# Routes — Access Code Regeneration
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>/regenerate-code", methods=["POST"])
@super_admin_required
def regenerate_access_code(org_id):
    from model import Organization, db
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    old_code = org.unique_code
    new_code = _generate_unique_code()
    org.unique_code = new_code
    _audit(org_id, "access_code_regenerated",
           f"Access code changed from {old_code} to {new_code}",
           {"old_code": old_code, "new_code": new_code})
    db.session.commit()
    flash(f"Access code regenerated. New code: {new_code}", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


# ---------------------------------------------------------------------------
# Routes — Limits
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>/set-limits", methods=["POST"])
@super_admin_required
def set_org_limits(org_id):
    from model import Organization, Plan, db
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    # Plan assignment
    plan_id = request.form.get("plan_id", "").strip()
    if plan_id:
        plan = db.session.get(Plan, int(plan_id))
        if plan:
            old_plan = org.plan.name if org.plan else "None"
            org.plan_id = plan.id
            _audit(org_id, "plan_changed",
                   f"Plan changed from {old_plan} to {plan.name}",
                   {"old_plan": old_plan, "new_plan": plan.name})

    # Member limit override
    member_limit = request.form.get("member_limit_override", "").strip()
    if member_limit:
        try:
            new_limit = int(member_limit)
            old_limit = org.member_limit_override
            org.member_limit_override = new_limit
            _audit(org_id, "member_limit_changed",
                   f"Member limit override set to {new_limit} (was {old_limit})",
                   {"old": old_limit, "new": new_limit})
        except ValueError:
            flash("Invalid member limit value.", "error")
            return redirect(url_for("super_admin.organization_detail", org_id=org_id))
    elif request.form.get("clear_member_limit"):
        org.member_limit_override = None
        _audit(org_id, "member_limit_cleared", "Member limit override removed")

    # Storage limit override
    storage_limit = request.form.get("storage_limit_override_gb", "").strip()
    if storage_limit:
        try:
            new_storage = float(storage_limit)
            old_storage = org.storage_limit_override_gb
            org.storage_limit_override_gb = new_storage
            _audit(org_id, "storage_limit_changed",
                   f"Storage limit override set to {new_storage} GB (was {old_storage})",
                   {"old": old_storage, "new": new_storage})
        except ValueError:
            flash("Invalid storage limit value.", "error")
            return redirect(url_for("super_admin.organization_detail", org_id=org_id))
    elif request.form.get("clear_storage_limit"):
        org.storage_limit_override_gb = None
        _audit(org_id, "storage_limit_cleared", "Storage limit override removed")

    db.session.commit()
    flash("Organization limits updated successfully.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


# ---------------------------------------------------------------------------
# Routes — Member Management
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>/members/<int:member_id>/role", methods=["POST"])
@super_admin_required
def change_member_role(org_id, member_id):
    from model import OrganizationMember, OrgMemberRole, db
    member = OrganizationMember.query.filter_by(id=member_id, organization_id=org_id).first_or_404()
    new_role_str = request.form.get("role", "").strip().lower()

    try:
        new_role = OrgMemberRole(new_role_str)
    except ValueError:
        flash("Invalid role.", "error")
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    # Prevent removing the last owner
    if member.role == OrgMemberRole.OWNER and new_role != OrgMemberRole.OWNER:
        owners = OrganizationMember.query.filter_by(
            organization_id=org_id, role=OrgMemberRole.OWNER, status='active'
        ).count()
        if owners <= 1:
            flash("Cannot demote the last owner. Transfer ownership first.", "error")
            return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    old_role = member.role.value
    member.role = new_role
    _audit(org_id, "member_role_changed",
           f"Member {member.user.email if member.user else member.user_id} role changed from {old_role} to {new_role.value}",
           {"user_id": member.user_id, "old_role": old_role, "new_role": new_role.value})
    db.session.commit()
    flash("Member role updated.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


@super_admin_bp.route("/organizations/<int:org_id>/members/<int:member_id>/suspend", methods=["POST"])
@super_admin_required
def suspend_member(org_id, member_id):
    from model import OrganizationMember, OrgMemberRole, db
    member = OrganizationMember.query.filter_by(id=member_id, organization_id=org_id).first_or_404()

    if member.role == OrgMemberRole.OWNER:
        flash("Cannot suspend the organization owner. Transfer ownership first.", "error")
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    member.status = 'suspended'
    _audit(org_id, "member_suspended",
           f"Member {member.user.email if member.user else member.user_id} suspended",
           {"user_id": member.user_id})
    db.session.commit()
    flash("Member suspended.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


@super_admin_bp.route("/organizations/<int:org_id>/members/<int:member_id>/activate", methods=["POST"])
@super_admin_required
def activate_member(org_id, member_id):
    from model import OrganizationMember, db
    member = OrganizationMember.query.filter_by(id=member_id, organization_id=org_id).first_or_404()
    member.status = 'active'
    _audit(org_id, "member_activated",
           f"Member {member.user.email if member.user else member.user_id} activated",
           {"user_id": member.user_id})
    db.session.commit()
    flash("Member activated.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


@super_admin_bp.route("/organizations/<int:org_id>/members/<int:member_id>/remove", methods=["POST"])
@super_admin_required
def remove_member(org_id, member_id):
    from model import OrganizationMember, OrgMemberRole, db
    member = OrganizationMember.query.filter_by(id=member_id, organization_id=org_id).first_or_404()

    if member.role == OrgMemberRole.OWNER:
        owners = OrganizationMember.query.filter_by(
            organization_id=org_id, role=OrgMemberRole.OWNER, status='active'
        ).count()
        if owners <= 1:
            flash("Cannot remove the last owner. Transfer ownership first.", "error")
            return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    user_email = member.user.email if member.user else str(member.user_id)
    member.status = 'removed'
    _audit(org_id, "member_removed",
           f"Member {user_email} removed from organization",
           {"user_id": member.user_id})
    db.session.commit()
    flash("Member removed from organization.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


# ---------------------------------------------------------------------------
# Routes — Transfer Ownership
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>/transfer-ownership", methods=["POST"])
@super_admin_required
def transfer_ownership(org_id):
    from model import OrganizationMember, OrgMemberRole, db
    new_owner_member_id = request.form.get("new_owner_member_id", "").strip()
    if not new_owner_member_id:
        flash("Please select a member to transfer ownership to.", "error")
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    new_owner_member = OrganizationMember.query.filter_by(
        id=int(new_owner_member_id), organization_id=org_id, status='active'
    ).first()
    if not new_owner_member:
        flash("Selected member not found or not active.", "error")
        return redirect(url_for("super_admin.organization_detail", org_id=org_id))

    # Demote current owners to admin
    current_owners = OrganizationMember.query.filter_by(
        organization_id=org_id, role=OrgMemberRole.OWNER, status='active'
    ).all()
    old_owner_emails = []
    for owner in current_owners:
        owner.role = OrgMemberRole.ADMIN
        if owner.user:
            old_owner_emails.append(owner.user.email)

    new_owner_member.role = OrgMemberRole.OWNER
    new_owner_email = new_owner_member.user.email if new_owner_member.user else str(new_owner_member.user_id)

    _audit(org_id, "ownership_transferred",
           f"Ownership transferred to {new_owner_email}. Previous owners: {', '.join(old_owner_emails)}",
           {"new_owner_user_id": new_owner_member.user_id, "old_owners": old_owner_emails})
    db.session.commit()
    flash(f"Ownership transferred to {new_owner_email}.", "success")
    return redirect(url_for("super_admin.organization_detail", org_id=org_id))


# ---------------------------------------------------------------------------
# Routes — Data Export
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations/<int:org_id>/export")
@super_admin_required
def export_organization(org_id):
    from model import (
        Organization, OrganizationMember, Customer, Project, Employee, db
    )
    org = db.session.get(Organization, org_id)
    if not org:
        flash("Organization not found", "error")
        return redirect(url_for("super_admin.organizations"))

    _audit(org_id, "data_exported", f"Organization data exported by Super Admin")
    db.session.commit()

    # Build ZIP in memory
    mem_zip = io.BytesIO()
    with zipfile.ZipFile(mem_zip, 'w', zipfile.ZIP_DEFLATED) as zf:

        # Members CSV
        members_buf = io.StringIO()
        w = csv.writer(members_buf)
        w.writerow(["ID", "Name", "Email", "Role", "Status", "Joined"])
        for m in OrganizationMember.query.filter_by(organization_id=org_id).all():
            u = m.user
            w.writerow([
                m.id,
                u.employee.name if u and u.employee else "",
                u.email if u else "",
                m.role.value,
                m.status,
                m.joined_at.strftime("%Y-%m-%d") if m.joined_at else "",
            ])
        zf.writestr("members.csv", members_buf.getvalue())

        # Customers CSV
        cust_buf = io.StringIO()
        w = csv.writer(cust_buf)
        w.writerow(["ID", "Name", "Email", "Phone", "Company", "Status", "City", "Created"])
        for c in Customer.query.filter_by(organization_id=org_id, is_deleted=False).all():
            w.writerow([c.id, c.name, c.email, c.phone_number, c.company or "",
                        c.status.value if c.status else "", c.city or "",
                        c.created_at.strftime("%Y-%m-%d") if c.created_at else ""])
        zf.writestr("customers.csv", cust_buf.getvalue())

        # Projects CSV
        proj_buf = io.StringIO()
        w = csv.writer(proj_buf)
        w.writerow(["ID", "Name", "Status", "Work Type", "Category", "Created"])
        for p in Project.query.filter_by(organization_id=org_id, is_deleted=False).all():
            w.writerow([p.id, p.name, p.status.value if p.status else "",
                        p.work_type.value if p.work_type else "",
                        p.category.value if p.category else "",
                        p.created_at.strftime("%Y-%m-%d") if p.created_at else ""])
        zf.writestr("projects.csv", proj_buf.getvalue())

    mem_zip.seek(0)
    safe_name = org.slug or f"org-{org_id}"
    filename = f"{safe_name}-export-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return send_file(
        mem_zip,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


# ---------------------------------------------------------------------------
# Routes — Audit Log (global view)
# ---------------------------------------------------------------------------

@super_admin_bp.route("/audit-log")
@super_admin_required
def audit_log():
    from model import OrgAuditLog, Organization, db
    page = request.args.get("page", 1, type=int)
    org_filter = request.args.get("org_id", "", type=str)
    action_filter = request.args.get("action", "").strip()

    q = OrgAuditLog.query.order_by(OrgAuditLog.created_at.desc())
    if org_filter:
        q = q.filter_by(organization_id=int(org_filter))
    if action_filter:
        q = q.filter(OrgAuditLog.action.ilike(f"%{action_filter}%"))

    logs = q.paginate(page=page, per_page=50, error_out=False)
    orgs = Organization.query.order_by(Organization.name).all()
    return render_template("super_admin/audit_log.html", logs=logs, orgs=orgs,
                           org_filter=org_filter, action_filter=action_filter)


# ---------------------------------------------------------------------------
# Coming Soon stubs
# ---------------------------------------------------------------------------

@super_admin_bp.route("/users")
@super_admin_required
def users():
    return render_template("super_admin/coming_soon.html", page_title="Users")


@super_admin_bp.route("/plans")
@super_admin_required
def plans():
    return render_template("super_admin/coming_soon.html", page_title="Plans")


@super_admin_bp.route("/modules")
@super_admin_required
def modules():
    return render_template("super_admin/coming_soon.html", page_title="Modules")


@super_admin_bp.route("/analytics")
@super_admin_required
def analytics():
    return render_template("super_admin/coming_soon.html", page_title="Analytics")


@super_admin_bp.route("/settings")
@super_admin_required
def settings():
    return render_template("super_admin/coming_soon.html", page_title="Settings")

"""
routes/org_customers.py
Organisation Customer blueprint — CRUD for org customers, members, coverage.
"""
import re
from datetime import datetime
from flask import (
    Blueprint, render_template, request, flash,
    redirect, url_for, jsonify, current_app,
)
from flask_login import login_required, current_user
from sqlalchemy import or_, func

from model import (
    db, OrgCustomer, OrgCustomerCity, OrgCustomerMember,
    OrgCustomerStatus, OrgCustomerCoverageType,
    OrgCustomerType, OrgCustomerMemberRole,
    Project, Employee, Quotation,
)
from utils.activity import log_activity, build_changes
from utils.security import require_roles

org_customers_bp = Blueprint("org_customers", __name__)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _generate_code(org_id: int) -> str:
    """Generate next ORG-XXXXX code for the tenant."""
    last = (
        OrgCustomer.query
        .filter_by(organization_id=org_id)
        .order_by(OrgCustomer.id.desc())
        .first()
    )
    next_id = (last.id + 1) if last else 1
    return f"ORG-{next_id:05d}"


def _sync_cities(org_customer: OrgCustomer, city_list: list[str]) -> None:
    """Replace city coverage with the provided list (deduped)."""
    seen = set()
    unique = []
    for c in city_list:
        c = c.strip()
        if c and c.lower() not in seen:
            seen.add(c.lower())
            unique.append(c)

    # Remove cities not in new list
    existing = {c.city.lower(): c for c in org_customer.cities.all()}
    keep = {u.lower() for u in unique}
    for city_lower, obj in existing.items():
        if city_lower not in keep:
            db.session.delete(obj)

    # Add new cities
    for city_name in unique:
        if city_name.lower() not in existing:
            db.session.add(OrgCustomerCity(
                org_customer_id=org_customer.id,
                city=city_name,
            ))


def _clear_primary(org_customer_id: int, exclude_member_id: int = None) -> None:
    """Ensure only one primary contact per org customer."""
    q = OrgCustomerMember.query.filter_by(org_customer_id=org_customer_id, is_primary=True)
    if exclude_member_id:
        q = q.filter(OrgCustomerMember.id != exclude_member_id)
    q.update({"is_primary": False}, synchronize_session=False)


# ──────────────────────────────────────────────────────────────────────────────
# List
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers")
@login_required
def org_customers_list():
    org_id = current_user.organization_id
    q = request.args.get("q", "").strip()
    status_f = request.args.get("status", "")
    coverage_f = request.args.get("coverage", "")
    type_f = request.args.get("org_type", "")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 30, type=int)

    query = OrgCustomer.query.filter_by(organization_id=org_id, is_deleted=False)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                OrgCustomer.name.ilike(like),
                OrgCustomer.code.ilike(like),
                OrgCustomer.gstin.ilike(like),
                OrgCustomer.primary_phone.ilike(like),
                OrgCustomer.city.ilike(like),
            )
        )

    if status_f:
        status_map = {e.value: e for e in OrgCustomerStatus}
        if status_f in status_map:
            query = query.filter(OrgCustomer.status == status_map[status_f])

    if coverage_f:
        cov_map = {e.value: e for e in OrgCustomerCoverageType}
        if coverage_f in cov_map:
            query = query.filter(OrgCustomer.coverage_type == cov_map[coverage_f])

    if type_f:
        type_map = {e.value: e for e in OrgCustomerType}
        if type_f in type_map:
            query = query.filter(OrgCustomer.org_type == type_map[type_f])

    pagination = query.order_by(OrgCustomer.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    total = OrgCustomer.query.filter_by(organization_id=org_id, is_deleted=False).count()
    active = OrgCustomer.query.filter_by(
        organization_id=org_id, is_deleted=False, status=OrgCustomerStatus.ACTIVE
    ).count()

    return render_template(
        "org_customers/list.html",
        org_customers=pagination.items,
        pagination=pagination,
        per_page=per_page,
        q=q,
        status_filter=status_f,
        coverage_filter=coverage_f,
        type_filter=type_f,
        total_count=total,
        active_count=active,
        OrgCustomerStatus=OrgCustomerStatus,
        OrgCustomerCoverageType=OrgCustomerCoverageType,
        OrgCustomerType=OrgCustomerType,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Detail
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>")
@login_required
def org_customer_detail(oc_id):
    org_id = current_user.organization_id
    oc = OrgCustomer.query.filter_by(id=oc_id, organization_id=org_id, is_deleted=False).first_or_404()

    members = oc.members.filter_by(is_deleted=False).order_by(
        OrgCustomerMember.is_primary.desc(), OrgCustomerMember.name
    ).all()

    projects = (
        Project.query
        .filter_by(org_customer_id=oc_id, organization_id=org_id, is_deleted=False)
        .order_by(Project.created_at.desc())
        .limit(20).all()
    )

    cities = oc.cities.order_by(OrgCustomerCity.city).all()

    project_ids = [p.id for p in projects]
    quotations = (
        Quotation.query.filter(
            Quotation.project_id.in_(project_ids),
            Quotation.organization_id == org_id,
            Quotation.is_deleted == False,
        ).order_by(Quotation.created_at.desc()).all()
        if project_ids else []
    )

    return render_template(
        "org_customers/detail.html",
        oc=oc,
        members=members,
        projects=projects,
        quotations=quotations,
        cities=cities,
        OrgCustomerMemberRole=OrgCustomerMemberRole,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Add
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/add", methods=["GET", "POST"])
@login_required
def add_org_customer():
    org_id = current_user.organization_id

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Organisation name is required.", "oc_error")
            return _render_form(None)

        coverage_val = request.form.get("coverage_type", OrgCustomerCoverageType.PAN_INDIA.value)
        cov_map = {e.value: e for e in OrgCustomerCoverageType}
        status_map = {e.value: e for e in OrgCustomerStatus}
        type_map = {e.value: e for e in OrgCustomerType}

        oc = OrgCustomer(
            organization_id=org_id,
            name=name,
            legal_name=request.form.get("legal_name", "").strip() or None,
            org_type=type_map.get(request.form.get("org_type", "")),
            website=request.form.get("website", "").strip() or None,
            industry=request.form.get("industry", "").strip() or None,
            gstin=request.form.get("gstin", "").strip().upper() or None,
            pan=request.form.get("pan", "").strip().upper() or None,
            address=(request.form.get("address") or request.form.get("address_line1", "")).strip() or None,
            city=request.form.get("city", "").strip() or None,
            state=request.form.get("state", "").strip() or None,
            pincode=request.form.get("pincode", "").strip() or None,
            country=request.form.get("country", "India").strip() or "India",
            primary_phone=(request.form.get("primary_phone") or request.form.get("phone", "")).strip() or None,
            primary_email=(request.form.get("primary_email") or request.form.get("email", "")).strip().lower() or None,
            coverage_type=cov_map.get(coverage_val, OrgCustomerCoverageType.PAN_INDIA),
            status=status_map.get(request.form.get("status", "Active"), OrgCustomerStatus.ACTIVE),
            notes=request.form.get("notes", "").strip() or None,
            created_by=current_user.employee.id if current_user.employee else None,
        )

        try:
            db.session.add(oc)
            db.session.flush()  # get oc.id

            # Generate code after flush
            oc.code = f"ORG-{oc.id:05d}"

            # Cities
            if oc.coverage_type == OrgCustomerCoverageType.SELECTED_CITIES:
                cities_raw = request.form.get("cities_input") or request.form.get("cities", "")
                city_list = [c.strip() for c in cities_raw.split(",") if c.strip()]
                _sync_cities(oc, city_list)

            db.session.commit()

            log_activity(
                action="create",
                entity_type="org_customer",
                entity_name=oc.name,
                org_id=org_id,
                entity_id=oc.id,
            )
            flash(f'Organisation "{oc.name}" created successfully.', "oc_success")
            return redirect(url_for("org_customers.org_customer_detail", oc_id=oc.id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"OrgCustomer create error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "oc_error")
            return _render_form(None)

    return _render_form(None)


# ──────────────────────────────────────────────────────────────────────────────
# Edit
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>/edit", methods=["GET", "POST"])
@login_required
def edit_org_customer(oc_id):
    org_id = current_user.organization_id
    oc = OrgCustomer.query.filter_by(id=oc_id, organization_id=org_id, is_deleted=False).first_or_404()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Organisation name is required.", "oc_error")
            return _render_form(oc)

        cov_map = {e.value: e for e in OrgCustomerCoverageType}
        status_map = {e.value: e for e in OrgCustomerStatus}
        type_map = {e.value: e for e in OrgCustomerType}

        old_name = oc.name
        old_status = oc.status_display

        oc.name = name
        oc.legal_name = request.form.get("legal_name", "").strip() or None
        oc.org_type = type_map.get(request.form.get("org_type", ""))
        oc.website = request.form.get("website", "").strip() or None
        oc.industry = request.form.get("industry", "").strip() or None
        oc.gstin = request.form.get("gstin", "").strip().upper() or None
        oc.pan = request.form.get("pan", "").strip().upper() or None
        oc.address = (request.form.get("address") or request.form.get("address_line1", "")).strip() or None
        oc.city = request.form.get("city", "").strip() or None
        oc.state = request.form.get("state", "").strip() or None
        oc.pincode = request.form.get("pincode", "").strip() or None
        oc.country = request.form.get("country", "India").strip() or "India"
        oc.primary_phone = (request.form.get("primary_phone") or request.form.get("phone", "")).strip() or None
        oc.primary_email = (request.form.get("primary_email") or request.form.get("email", "")).strip().lower() or None
        oc.coverage_type = cov_map.get(request.form.get("coverage_type"), OrgCustomerCoverageType.PAN_INDIA)
        new_status = status_map.get(request.form.get("status", "Active"), OrgCustomerStatus.ACTIVE)
        oc.status = new_status
        oc.notes = request.form.get("notes", "").strip() or None
        oc.updated_by = current_user.employee.id if current_user.employee else None
        oc.updated_at = datetime.utcnow()

        try:
            db.session.flush()

            # Sync cities
            if oc.coverage_type == OrgCustomerCoverageType.SELECTED_CITIES:
                cities_raw = request.form.get("cities_input") or request.form.get("cities", "")
                city_list = [c.strip() for c in cities_raw.split(",") if c.strip()]
                _sync_cities(oc, city_list)
            else:
                # Pan India — clear all selected cities
                for c in oc.cities.all():
                    db.session.delete(c)

            db.session.commit()

            changes = build_changes([
                ("Name", old_name, oc.name),
                ("Status", old_status, oc.status_display),
            ])
            log_activity(
                action="update",
                entity_type="org_customer",
                entity_name=oc.name,
                org_id=org_id,
                entity_id=oc.id,
                changes=changes,
            )
            flash(f'Organisation "{oc.name}" updated.', "oc_success")
            return redirect(url_for("org_customers.org_customer_detail", oc_id=oc.id))

        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"OrgCustomer edit error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "oc_error")

    return _render_form(oc)


def _render_form(oc):
    cities_str = ""
    if oc and oc.coverage_type == OrgCustomerCoverageType.SELECTED_CITIES:
        cities_str = ", ".join(c.city for c in oc.cities.order_by(OrgCustomerCity.city).all())

    return render_template(
        "org_customers/form.html",
        oc=oc,
        cities_str=cities_str,
        OrgCustomerStatus=OrgCustomerStatus,
        OrgCustomerCoverageType=OrgCustomerCoverageType,
        OrgCustomerType=OrgCustomerType,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Delete (soft)
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>/delete", methods=["POST"])
@login_required
def delete_org_customer(oc_id):
    org_id = current_user.organization_id
    oc = OrgCustomer.query.filter_by(id=oc_id, organization_id=org_id, is_deleted=False).first_or_404()

    # Guard: prevent deletion if linked projects exist
    live_projects = Project.query.filter_by(
        org_customer_id=oc_id, is_deleted=False
    ).count()
    if live_projects:
        flash(
            f'Cannot delete "{oc.name}" — it has {live_projects} active project(s). '
            f'Please reassign or close those projects first.',
            "oc_error",
        )
        return redirect(url_for("org_customers.org_customer_detail", oc_id=oc_id))

    oc.is_deleted = True
    oc.deleted_at = datetime.utcnow()
    try:
        log_activity(
            action="delete",
            entity_type="org_customer",
            entity_name=oc.name,
            org_id=org_id,
            entity_id=oc_id,
        )
        db.session.commit()
        flash(f'Organisation "{oc.name}" deleted.', "oc_success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"OrgCustomer delete error: {e}", exc_info=True)
        flash("Failed to delete organisation.", "oc_error")
    return redirect(url_for("org_customers.org_customers_list"))


# ──────────────────────────────────────────────────────────────────────────────
# Members — Add
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>/members/add", methods=["GET", "POST"])
@login_required
def add_member(oc_id):
    org_id = current_user.organization_id
    oc = OrgCustomer.query.filter_by(id=oc_id, organization_id=org_id, is_deleted=False).first_or_404()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Member name is required.", "oc_error")
            return render_template("org_customers/member_form.html", oc=oc, member=None,
                                   OrgCustomerMemberRole=OrgCustomerMemberRole)

        role_map = {e.value: e for e in OrgCustomerMemberRole}
        is_primary = bool(request.form.get("is_primary"))

        if is_primary:
            _clear_primary(oc_id)

        member = OrgCustomerMember(
            org_customer_id=oc_id,
            organization_id=org_id,
            name=name,
            mobile=request.form.get("mobile", "").strip() or None,
            email=request.form.get("email", "").strip().lower() or None,
            designation=request.form.get("designation", "").strip() or None,
            department=request.form.get("department", "").strip() or None,
            role=role_map.get(request.form.get("role", "")),
            notes=request.form.get("notes", "").strip() or None,
            is_primary=is_primary,
            is_active=True,
            created_by=current_user.employee.id if current_user.employee else None,
        )
        try:
            db.session.add(member)
            db.session.commit()
            flash(f'Member "{name}" added.', "oc_success")
            return redirect(url_for("org_customers.org_customer_detail", oc_id=oc_id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Add member error: {e}", exc_info=True)
            flash("Failed to add member.", "oc_error")

    return render_template("org_customers/member_form.html", oc=oc, member=None,
                           OrgCustomerMemberRole=OrgCustomerMemberRole)


# ──────────────────────────────────────────────────────────────────────────────
# Members — Edit
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>/members/<int:member_id>/edit", methods=["GET", "POST"])
@login_required
def edit_member(oc_id, member_id):
    org_id = current_user.organization_id
    oc = OrgCustomer.query.filter_by(id=oc_id, organization_id=org_id, is_deleted=False).first_or_404()
    member = OrgCustomerMember.query.filter_by(
        id=member_id, org_customer_id=oc_id, organization_id=org_id, is_deleted=False
    ).first_or_404()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Member name is required.", "oc_error")
            return render_template("org_customers/member_form.html", oc=oc, member=member,
                                   OrgCustomerMemberRole=OrgCustomerMemberRole)

        role_map = {e.value: e for e in OrgCustomerMemberRole}
        is_primary = bool(request.form.get("is_primary"))

        if is_primary and not member.is_primary:
            _clear_primary(oc_id, exclude_member_id=member_id)

        member.name = name
        member.mobile = request.form.get("mobile", "").strip() or None
        member.email = request.form.get("email", "").strip().lower() or None
        member.designation = request.form.get("designation", "").strip() or None
        member.department = request.form.get("department", "").strip() or None
        member.role = role_map.get(request.form.get("role", ""))
        member.notes = request.form.get("notes", "").strip() or None
        member.is_primary = is_primary
        member.is_active = bool(request.form.get("is_active", True))
        member.updated_at = datetime.utcnow()

        try:
            db.session.commit()
            flash(f'Member "{name}" updated.', "oc_success")
            return redirect(url_for("org_customers.org_customer_detail", oc_id=oc_id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Edit member error: {e}", exc_info=True)
            flash("Failed to update member.", "oc_error")

    return render_template("org_customers/member_form.html", oc=oc, member=member,
                           OrgCustomerMemberRole=OrgCustomerMemberRole)


# ──────────────────────────────────────────────────────────────────────────────
# Members — Toggle active
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>/members/<int:member_id>/toggle", methods=["POST"])
@login_required
def toggle_member(oc_id, member_id):
    org_id = current_user.organization_id
    member = OrgCustomerMember.query.filter_by(
        id=member_id, org_customer_id=oc_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    member.is_active = not member.is_active
    member.updated_at = datetime.utcnow()
    try:
        db.session.commit()
        state = "activated" if member.is_active else "deactivated"
        flash(f'Member "{member.name}" {state}.', "oc_success")
    except Exception:
        db.session.rollback()
        flash("Failed to update member.", "oc_error")
    return redirect(url_for("org_customers.org_customer_detail", oc_id=oc_id))


# ──────────────────────────────────────────────────────────────────────────────
# Members — Delete (soft)
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/org-customers/<int:oc_id>/members/<int:member_id>/delete", methods=["POST"])
@login_required
def delete_member(oc_id, member_id):
    org_id = current_user.organization_id
    member = OrgCustomerMember.query.filter_by(
        id=member_id, org_customer_id=oc_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    member.is_deleted = True
    member.deleted_at = datetime.utcnow()
    if member.is_primary:
        member.is_primary = False
    try:
        db.session.commit()
        flash(f'Member "{member.name}" removed.', "oc_success")
    except Exception:
        db.session.rollback()
        flash("Failed to remove member.", "oc_error")
    return redirect(url_for("org_customers.org_customer_detail", oc_id=oc_id))


# ──────────────────────────────────────────────────────────────────────────────
# API — Members for a given org customer (used by project form JS)
# ──────────────────────────────────────────────────────────────────────────────

@org_customers_bp.route("/api/org-customers/<int:oc_id>/members")
@login_required
def api_org_customer_members(oc_id):
    org_id = current_user.organization_id
    oc = OrgCustomer.query.filter_by(id=oc_id, organization_id=org_id, is_deleted=False).first()
    if not oc:
        return jsonify([])
    members = oc.members.filter_by(is_active=True, is_deleted=False).order_by(
        OrgCustomerMember.is_primary.desc(), OrgCustomerMember.name
    ).all()
    return jsonify([
        {
            "id": m.id,
            "name": m.name,
            "designation": m.designation or "",
            "role": m.role_display,
            "is_primary": m.is_primary,
        }
        for m in members
    ])

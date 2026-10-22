"""
routes/vendors.py
Vendor Management blueprint — full CRUD, search, categories, documents,
import/export, and activity log. Multi-tenant, CSRF-protected.
"""
import csv
import io
import json
import os
import uuid
from datetime import datetime

from flask import (
    Blueprint, render_template, request, flash, redirect,
    url_for, jsonify, current_app, Response, send_file,
)
from flask_login import login_required, current_user
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from model import (
    db, Vendor, VendorCategory, VendorDocument, VendorActivity,
    VendorStatus, VendorVerificationStatus, VendorBusinessType,
    VendorPaymentTerms, Employee,
)
from utils.security import require_roles, validate_upload

vendors_bp = Blueprint("vendors", __name__)

# ── Allowed upload extensions ─────────────────────────────────────────────────
VENDOR_DOC_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "xls", "xlsx"}

VENDOR_DOCUMENT_TYPES = [
    "GST Certificate", "PAN Card", "Contract", "Quotation",
    "Catalogue", "Quality Certificate", "Bank Details", "Other",
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_org_id():
    return current_user.organization_id


def _get_actor_id():
    emp = current_user.employee
    return emp.id if emp else None


def _generate_vendor_code(org_id: int) -> str:
    """Generate next VEN-XXXXX code per tenant."""
    last = (
        Vendor.query
        .filter_by(organization_id=org_id)
        .order_by(Vendor.id.desc())
        .first()
    )
    next_seq = (last.id + 1) if last else 1
    return f"VEN-{next_seq:05d}"


def _log_activity(vendor: Vendor, action: str, description: str, changes: dict = None):
    org_id = _get_org_id()
    actor_id = _get_actor_id()
    entry = VendorActivity(
        vendor_id=vendor.id,
        action=action,
        description=description,
        changes_json=json.dumps(changes) if changes else None,
        actor_id=actor_id,
        organization_id=org_id,
    )
    db.session.add(entry)


def _build_search_query(org_id, q, status_f, category_f, city_f, verification_f):
    query = Vendor.query.filter_by(organization_id=org_id, is_deleted=False)

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Vendor.company_name.ilike(like),
                Vendor.legal_name.ilike(like),
                Vendor.code.ilike(like),
                Vendor.contact_person.ilike(like),
                Vendor.phone.ilike(like),
                Vendor.email.ilike(like),
                Vendor.gstin.ilike(like),
                Vendor.pan.ilike(like),
                Vendor.city.ilike(like),
                Vendor.products_services.ilike(like),
            )
        )

    if status_f:
        status_map = {e.value: e for e in VendorStatus}
        if status_f in status_map:
            query = query.filter(Vendor.status == status_map[status_f])

    if category_f:
        try:
            query = query.filter(Vendor.category_id == int(category_f))
        except (TypeError, ValueError):
            pass

    if city_f:
        query = query.filter(Vendor.city.ilike(f"%{city_f}%"))

    if verification_f:
        vmap = {e.value: e for e in VendorVerificationStatus}
        if verification_f in vmap:
            query = query.filter(Vendor.verification_status == vmap[verification_f])

    return query


# ── Vendor Directory (List) ───────────────────────────────────────────────────

@vendors_bp.route("/vendors")
@login_required
def vendor_list():
    org_id = _get_org_id()
    q = request.args.get("q", "").strip()
    status_f = request.args.get("status", "")
    category_f = request.args.get("category", "")
    city_f = request.args.get("city", "")
    verification_f = request.args.get("verification", "")
    sort = request.args.get("sort", "newest")
    page = request.args.get("page", 1, type=int)
    per_page = 25

    query = _build_search_query(org_id, q, status_f, category_f, city_f, verification_f)

    if sort == "name":
        query = query.order_by(Vendor.company_name.asc())
    elif sort == "oldest":
        query = query.order_by(Vendor.created_at.asc())
    else:
        query = query.order_by(Vendor.created_at.desc())

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)

    # Dashboard counts
    base = Vendor.query.filter_by(organization_id=org_id, is_deleted=False)
    total_count = base.count()
    active_count = base.filter_by(status=VendorStatus.ACTIVE).count()
    inactive_count = base.filter_by(status=VendorStatus.INACTIVE).count()
    pending_count = base.filter_by(status=VendorStatus.PENDING).count()
    unverified_count = base.filter_by(
        verification_status=VendorVerificationStatus.UNVERIFIED
    ).count()
    recent = base.order_by(Vendor.created_at.desc()).limit(5).all()

    categories = VendorCategory.query.filter_by(
        organization_id=org_id, is_active=True
    ).order_by(VendorCategory.name).all()

    return render_template(
        "vendors/list.html",
        pagination=pagination,
        vendors=pagination.items,
        q=q,
        status_filter=status_f,
        category_filter=category_f,
        city_filter=city_f,
        verification_filter=verification_f,
        sort=sort,
        total_count=total_count,
        active_count=active_count,
        inactive_count=inactive_count,
        pending_count=pending_count,
        unverified_count=unverified_count,
        recent_vendors=recent,
        categories=categories,
        VendorStatus=VendorStatus,
        VendorVerificationStatus=VendorVerificationStatus,
    )


# ── Live Search API ───────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/search")
@login_required
def vendor_search_api():
    """Debounced live search — returns JSON results."""
    org_id = _get_org_id()
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})

    like = f"%{q}%"
    results = (
        Vendor.query
        .filter_by(organization_id=org_id, is_deleted=False)
        .filter(
            or_(
                Vendor.company_name.ilike(like),
                Vendor.code.ilike(like),
                Vendor.contact_person.ilike(like),
                Vendor.phone.ilike(like),
                Vendor.email.ilike(like),
                Vendor.gstin.ilike(like),
                Vendor.city.ilike(like),
                Vendor.products_services.ilike(like),
            )
        )
        .order_by(Vendor.company_name)
        .limit(10)
        .all()
    )

    data = [
        {
            "id": v.id,
            "company_name": v.company_name,
            "code": v.code,
            "contact_person": v.contact_person or "",
            "phone": v.phone or "",
            "city": v.city or "",
            "category": v.category.name if v.category else "",
            "status": v.status_display,
            "url": url_for("vendors.vendor_detail", vendor_id=v.id),
        }
        for v in results
    ]
    return jsonify({"results": data})


# ── Add Vendor ────────────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/add", methods=["GET", "POST"])
@login_required
def add_vendor():
    org_id = _get_org_id()
    categories = VendorCategory.query.filter_by(
        organization_id=org_id, is_active=True
    ).order_by(VendorCategory.name).all()
    employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).order_by(Employee.name).all()

    if request.method == "POST":
        company_name = request.form.get("company_name", "").strip()
        if not company_name:
            flash("Company name is required.", "vendor_error")
            return redirect(url_for("vendors.add_vendor"))

        # Duplicate check
        existing = Vendor.query.filter_by(
            organization_id=org_id,
            company_name=company_name,
            is_deleted=False,
        ).first()
        if existing:
            flash(
                f"A vendor named '{company_name}' already exists "
                f"({existing.code}). Please verify before adding.",
                "vendor_warning",
            )

        code = _generate_vendor_code(org_id)

        btype_val = request.form.get("business_type", "")
        btype_map = {e.value: e for e in VendorBusinessType}
        pt_val = request.form.get("payment_terms", "")
        pt_map = {e.value: e for e in VendorPaymentTerms}
        status_val = request.form.get("status", "Active")
        status_map = {e.value: e for e in VendorStatus}
        verif_val = request.form.get("verification_status", "Unverified")
        verif_map = {e.value: e for e in VendorVerificationStatus}

        cat_id = request.form.get("category_id") or None
        if cat_id:
            cat = VendorCategory.query.filter_by(
                id=int(cat_id), organization_id=org_id
            ).first()
            cat_id = cat.id if cat else None

        assigned_raw = request.form.get("assigned_to") or None
        assigned_id = None
        if assigned_raw:
            emp = Employee.query.filter_by(
                id=int(assigned_raw), organization_id=org_id, is_deleted=False
            ).first()
            assigned_id = emp.id if emp else None

        credit_raw = request.form.get("credit_period_days", "").strip()
        credit_days = int(credit_raw) if credit_raw.isdigit() else None

        vendor = Vendor(
            code=code,
            company_name=company_name,
            legal_name=request.form.get("legal_name", "").strip() or None,
            category_id=cat_id,
            products_services=request.form.get("products_services", "").strip() or None,
            contact_person=request.form.get("contact_person", "").strip() or None,
            designation=request.form.get("designation", "").strip() or None,
            phone=request.form.get("phone", "").strip() or None,
            alt_phone=request.form.get("alt_phone", "").strip() or None,
            email=request.form.get("email", "").strip() or None,
            website=request.form.get("website", "").strip() or None,
            gstin=request.form.get("gstin", "").strip().upper() or None,
            pan=request.form.get("pan", "").strip().upper() or None,
            business_type=btype_map.get(btype_val),
            address=request.form.get("address", "").strip() or None,
            city=request.form.get("city", "").strip() or None,
            state=request.form.get("state", "").strip() or None,
            pincode=request.form.get("pincode", "").strip() or None,
            country=request.form.get("country", "India").strip() or "India",
            warehouse_address=request.form.get("warehouse_address", "").strip() or None,
            payment_terms=pt_map.get(pt_val),
            credit_period_days=credit_days,
            bank_name=request.form.get("bank_name", "").strip() or None,
            bank_account_no=request.form.get("bank_account_no", "").strip() or None,
            bank_ifsc=request.form.get("bank_ifsc", "").strip().upper() or None,
            status=status_map.get(status_val, VendorStatus.ACTIVE),
            verification_status=verif_map.get(verif_val, VendorVerificationStatus.UNVERIFIED),
            notes=request.form.get("notes", "").strip() or None,
            assigned_to=assigned_id,
            organization_id=org_id,
            created_by=_get_actor_id(),
        )

        try:
            db.session.add(vendor)
            db.session.flush()
            _log_activity(vendor, "created", f"Vendor '{company_name}' created.")

            # Handle multiple document uploads
            if "documents" in request.files:
                _save_vendor_docs(request.files.getlist("documents"), vendor, org_id)

            db.session.commit()
            flash(f"Vendor '{company_name}' added successfully ({code}).", "vendor_success")
            return redirect(url_for("vendors.vendor_detail", vendor_id=vendor.id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Add vendor error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "vendor_error")

    return render_template(
        "vendors/form.html",
        categories=categories,
        employees=employees,
        vendor=None,
        VendorStatus=VendorStatus,
        VendorVerificationStatus=VendorVerificationStatus,
        VendorBusinessType=VendorBusinessType,
        VendorPaymentTerms=VendorPaymentTerms,
        VENDOR_DOCUMENT_TYPES=VENDOR_DOCUMENT_TYPES,
    )


# ── Vendor Detail / Profile ───────────────────────────────────────────────────

@vendors_bp.route("/vendors/<int:vendor_id>")
@login_required
def vendor_detail(vendor_id):
    org_id = _get_org_id()
    vendor = Vendor.query.filter_by(
        id=vendor_id, organization_id=org_id, is_deleted=False
    ).first_or_404()

    activities = (
        VendorActivity.query
        .filter_by(vendor_id=vendor_id, organization_id=org_id)
        .order_by(VendorActivity.created_at.desc())
        .limit(50)
        .all()
    )
    documents = vendor.documents.order_by(VendorDocument.uploaded_at.desc()).all()

    return render_template(
        "vendors/detail.html",
        vendor=vendor,
        activities=activities,
        documents=documents,
        VENDOR_DOCUMENT_TYPES=VENDOR_DOCUMENT_TYPES,
    )


# ── Edit Vendor ───────────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/<int:vendor_id>/edit", methods=["GET", "POST"])
@login_required
def edit_vendor(vendor_id):
    org_id = _get_org_id()
    vendor = Vendor.query.filter_by(
        id=vendor_id, organization_id=org_id, is_deleted=False
    ).first_or_404()

    categories = VendorCategory.query.filter_by(
        organization_id=org_id, is_active=True
    ).order_by(VendorCategory.name).all()
    employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).order_by(Employee.name).all()

    if request.method == "POST":
        company_name = request.form.get("company_name", "").strip()
        if not company_name:
            flash("Company name is required.", "vendor_error")
            return redirect(url_for("vendors.edit_vendor", vendor_id=vendor_id))

        # Duplicate check (exclude self)
        existing = Vendor.query.filter(
            Vendor.organization_id == org_id,
            Vendor.company_name == company_name,
            Vendor.is_deleted == False,
            Vendor.id != vendor_id,
        ).first()
        if existing:
            flash(
                f"Another vendor named '{company_name}' already exists ({existing.code}).",
                "vendor_warning",
            )

        btype_map = {e.value: e for e in VendorBusinessType}
        pt_map = {e.value: e for e in VendorPaymentTerms}
        status_map = {e.value: e for e in VendorStatus}
        verif_map = {e.value: e for e in VendorVerificationStatus}

        cat_id = request.form.get("category_id") or None
        if cat_id:
            cat = VendorCategory.query.filter_by(
                id=int(cat_id), organization_id=org_id
            ).first()
            cat_id = cat.id if cat else None

        assigned_raw = request.form.get("assigned_to") or None
        assigned_id = None
        if assigned_raw:
            emp = Employee.query.filter_by(
                id=int(assigned_raw), organization_id=org_id, is_deleted=False
            ).first()
            assigned_id = emp.id if emp else None

        credit_raw = request.form.get("credit_period_days", "").strip()
        credit_days = int(credit_raw) if credit_raw.isdigit() else None

        # Build change log
        changes = {}
        def _track(field, old, new):
            if str(old or "") != str(new or ""):
                changes[field] = {"from": str(old or ""), "to": str(new or "")}

        _track("Company Name", vendor.company_name, company_name)
        _track("Status", vendor.status_display, request.form.get("status", ""))
        _track("City", vendor.city, request.form.get("city", "").strip())

        vendor.company_name = company_name
        vendor.legal_name = request.form.get("legal_name", "").strip() or None
        vendor.category_id = cat_id
        vendor.products_services = request.form.get("products_services", "").strip() or None
        vendor.contact_person = request.form.get("contact_person", "").strip() or None
        vendor.designation = request.form.get("designation", "").strip() or None
        vendor.phone = request.form.get("phone", "").strip() or None
        vendor.alt_phone = request.form.get("alt_phone", "").strip() or None
        vendor.email = request.form.get("email", "").strip() or None
        vendor.website = request.form.get("website", "").strip() or None
        vendor.gstin = (request.form.get("gstin", "").strip().upper() or None)
        vendor.pan = (request.form.get("pan", "").strip().upper() or None)
        vendor.business_type = btype_map.get(request.form.get("business_type", ""))
        vendor.address = request.form.get("address", "").strip() or None
        vendor.city = request.form.get("city", "").strip() or None
        vendor.state = request.form.get("state", "").strip() or None
        vendor.pincode = request.form.get("pincode", "").strip() or None
        vendor.country = request.form.get("country", "India").strip() or "India"
        vendor.warehouse_address = request.form.get("warehouse_address", "").strip() or None
        vendor.payment_terms = pt_map.get(request.form.get("payment_terms", ""))
        vendor.credit_period_days = credit_days
        vendor.bank_name = request.form.get("bank_name", "").strip() or None
        vendor.bank_account_no = request.form.get("bank_account_no", "").strip() or None
        vendor.bank_ifsc = (request.form.get("bank_ifsc", "").strip().upper() or None)
        vendor.status = status_map.get(request.form.get("status", "Active"), VendorStatus.ACTIVE)
        vendor.verification_status = verif_map.get(
            request.form.get("verification_status", "Unverified"),
            VendorVerificationStatus.UNVERIFIED,
        )
        vendor.notes = request.form.get("notes", "").strip() or None
        vendor.assigned_to = assigned_id
        vendor.updated_by = _get_actor_id()

        try:
            _log_activity(vendor, "updated", f"Vendor '{vendor.company_name}' updated.", changes)

            if "documents" in request.files:
                _save_vendor_docs(request.files.getlist("documents"), vendor, org_id)

            db.session.commit()
            flash("Vendor updated successfully.", "vendor_success")
            return redirect(url_for("vendors.vendor_detail", vendor_id=vendor_id))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Edit vendor error: {e}", exc_info=True)
            flash("An error occurred. Please try again.", "vendor_error")

    return render_template(
        "vendors/form.html",
        categories=categories,
        employees=employees,
        vendor=vendor,
        VendorStatus=VendorStatus,
        VendorVerificationStatus=VendorVerificationStatus,
        VendorBusinessType=VendorBusinessType,
        VendorPaymentTerms=VendorPaymentTerms,
        VENDOR_DOCUMENT_TYPES=VENDOR_DOCUMENT_TYPES,
    )


# ── Delete Vendor ─────────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/<int:vendor_id>/delete", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def delete_vendor(vendor_id):
    org_id = _get_org_id()
    vendor = Vendor.query.filter_by(
        id=vendor_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    try:
        _log_activity(vendor, "deleted", f"Vendor '{vendor.company_name}' soft-deleted.")
        vendor.is_deleted = True
        vendor.deleted_at = datetime.utcnow()
        vendor.deleted_by = _get_actor_id()
        db.session.commit()
        flash(f"Vendor '{vendor.company_name}' deleted.", "vendor_success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete vendor error: {e}", exc_info=True)
        flash("Failed to delete vendor.", "vendor_error")
    return redirect(url_for("vendors.vendor_list"))


# ── Document Handling ─────────────────────────────────────────────────────────

def _save_vendor_docs(files, vendor: Vendor, org_id: int):
    """Save multiple uploaded files as VendorDocument records."""
    upload_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], "vendor_docs"
    )
    os.makedirs(upload_path, exist_ok=True)
    actor_id = _get_actor_id()

    for f in files:
        if not f or not f.filename:
            continue
        original = secure_filename(f.filename)
        if "." not in original:
            continue
        ext = original.rsplit(".", 1)[1].lower()
        if ext not in VENDOR_DOC_EXTENSIONS:
            continue
        stored = f"{uuid.uuid4().hex}.{ext}"
        f.save(os.path.join(upload_path, stored))

        doc_type = request.form.get("document_type", "Other")
        description = request.form.get("doc_description", "").strip()

        doc = VendorDocument(
            vendor_id=vendor.id,
            filename=stored,
            original_name=original,
            file_type=ext,
            file_size=os.path.getsize(os.path.join(upload_path, stored)),
            document_type=doc_type,
            description=description or None,
            uploaded_by=actor_id,
            organization_id=org_id,
        )
        db.session.add(doc)


@vendors_bp.route("/vendors/<int:vendor_id>/documents/upload", methods=["POST"])
@login_required
def upload_vendor_document(vendor_id):
    org_id = _get_org_id()
    vendor = Vendor.query.filter_by(
        id=vendor_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    if "documents" in request.files:
        files = request.files.getlist("documents")
        _save_vendor_docs(files, vendor, org_id)
        _log_activity(vendor, "document_uploaded", f"Uploaded documents for vendor '{vendor.company_name}'.")
        db.session.commit()
        flash("Documents uploaded successfully.", "vendor_success")
    else:
        flash("No files provided.", "vendor_warning")
    return redirect(url_for("vendors.vendor_detail", vendor_id=vendor_id))


@vendors_bp.route("/vendors/<int:vendor_id>/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def delete_vendor_document(vendor_id, doc_id):
    org_id = _get_org_id()
    doc = VendorDocument.query.filter_by(
        id=doc_id, vendor_id=vendor_id, organization_id=org_id
    ).first_or_404()
    try:
        upload_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"], "vendor_docs", doc.filename
        )
        if os.path.exists(upload_path):
            os.remove(upload_path)
        db.session.delete(doc)
        db.session.commit()
        flash("Document deleted.", "vendor_success")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Delete vendor doc error: {e}", exc_info=True)
        flash("Failed to delete document.", "vendor_error")
    return redirect(url_for("vendors.vendor_detail", vendor_id=vendor_id))


@vendors_bp.route("/vendors/documents/<int:doc_id>/download")
@login_required
def download_vendor_document(doc_id):
    org_id = _get_org_id()
    doc = VendorDocument.query.filter_by(
        id=doc_id, organization_id=org_id
    ).first_or_404()
    path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], "vendor_docs", doc.filename
    )
    if not os.path.exists(path):
        flash("File not found.", "vendor_error")
        return redirect(url_for("vendors.vendor_list"))
    return send_file(path, as_attachment=True, download_name=doc.original_name)


# ── Categories ────────────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/categories")
@login_required
def vendor_categories():
    org_id = _get_org_id()
    cats = VendorCategory.query.filter_by(
        organization_id=org_id
    ).order_by(VendorCategory.name).all()
    return render_template("vendors/categories.html", categories=cats)


@vendors_bp.route("/vendors/categories/add", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def add_vendor_category():
    org_id = _get_org_id()
    name = request.form.get("name", "").strip()
    if not name:
        flash("Category name is required.", "vendor_error")
        return redirect(url_for("vendors.vendor_categories"))
    existing = VendorCategory.query.filter_by(
        name=name, organization_id=org_id
    ).first()
    if existing:
        flash(f"Category '{name}' already exists.", "vendor_warning")
        return redirect(url_for("vendors.vendor_categories"))
    cat = VendorCategory(
        name=name,
        description=request.form.get("description", "").strip() or None,
        organization_id=org_id,
        created_by=_get_actor_id(),
    )
    db.session.add(cat)
    db.session.commit()
    flash(f"Category '{name}' added.", "vendor_success")
    return redirect(url_for("vendors.vendor_categories"))


@vendors_bp.route("/vendors/categories/<int:cat_id>/toggle", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def toggle_vendor_category(cat_id):
    org_id = _get_org_id()
    cat = VendorCategory.query.filter_by(
        id=cat_id, organization_id=org_id
    ).first_or_404()
    cat.is_active = not cat.is_active
    db.session.commit()
    state = "activated" if cat.is_active else "deactivated"
    flash(f"Category '{cat.name}' {state}.", "vendor_success")
    return redirect(url_for("vendors.vendor_categories"))


# ── Export ────────────────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/export")
@login_required
@require_roles("admin", "manager")
def export_vendors():
    org_id = _get_org_id()
    q = request.args.get("q", "").strip()
    status_f = request.args.get("status", "")
    category_f = request.args.get("category", "")
    city_f = request.args.get("city", "")
    verification_f = request.args.get("verification", "")
    fmt = request.args.get("format", "csv")

    query = _build_search_query(org_id, q, status_f, category_f, city_f, verification_f)
    vendors = query.order_by(Vendor.company_name).all()

    headers = [
        "Code", "Company Name", "Legal Name", "Category", "Contact Person",
        "Phone", "Email", "City", "State", "GSTIN", "PAN",
        "Business Type", "Payment Terms", "Status", "Verification", "Created At",
    ]

    def row(v):
        return {
            "Code": v.code,
            "Company Name": v.company_name,
            "Legal Name": v.legal_name or "",
            "Category": v.category.name if v.category else "",
            "Contact Person": v.contact_person or "",
            "Phone": v.phone or "",
            "Email": v.email or "",
            "City": v.city or "",
            "State": v.state or "",
            "GSTIN": v.gstin or "",
            "PAN": v.pan or "",
            "Business Type": v.business_type_display,
            "Payment Terms": v.payment_terms_display,
            "Status": v.status_display,
            "Verification": v.verification_display,
            "Created At": v.created_at.strftime("%Y-%m-%d") if v.created_at else "",
        }

    rows = [row(v) for v in vendors]

    if fmt == "xlsx":
        import pandas as pd
        from io import BytesIO
        df = pd.DataFrame(rows, columns=headers)
        output = BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="Vendors")
            wb = writer.book
            ws = writer.sheets["Vendors"]
            hfmt = wb.add_format({
                "bold": True, "bg_color": "#D4AF37",
                "font_color": "#FFFFFF", "border": 1,
                "align": "center", "valign": "middle",
            })
            for i, h in enumerate(headers):
                ws.write(0, i, h, hfmt)
                ws.set_column(i, i, 20)
            ws.freeze_panes(1, 0)
        output.seek(0)
        return send_file(
            output, as_attachment=True,
            download_name=f"vendors_{datetime.now().strftime('%Y%m%d')}.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # CSV default
    si = io.StringIO()
    writer = csv.DictWriter(si, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition": f"attachment;filename=vendors_{datetime.now().strftime('%Y%m%d')}.csv"
        },
    )


# ── Import ────────────────────────────────────────────────────────────────────

@vendors_bp.route("/vendors/import-template")
@login_required
def import_template():
    """Download a blank CSV template."""
    headers = [
        "company_name", "legal_name", "category", "contact_person",
        "designation", "phone", "alt_phone", "email", "website",
        "gstin", "pan", "business_type", "address", "city",
        "state", "pincode", "country", "payment_terms", "credit_period_days",
        "products_services", "notes",
    ]
    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(headers)
    writer.writerow([
        "Example Vendor Pvt Ltd", "Example Vendor Pvt Ltd", "Glass Suppliers",
        "Rajesh Kumar", "Sales Manager", "9876543210", "", "rajesh@example.com",
        "https://example.com", "29ABCDE1234F1Z5", "ABCDE1234F", "Manufacturer",
        "123 MG Road", "Bengaluru", "Karnataka", "560001", "India",
        "Net 30", "30", "Glass sheets, Tempered Glass", "Preferred vendor",
    ])
    return Response(
        si.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=vendor_import_template.csv"},
    )


@vendors_bp.route("/vendors/import", methods=["GET", "POST"])
@login_required
@require_roles("admin", "manager")
def import_vendors():
    org_id = _get_org_id()

    if request.method == "GET":
        return render_template("vendors/import.html")

    file = request.files.get("file")
    if not file or not file.filename.endswith(".csv"):
        flash("Please upload a valid CSV file.", "vendor_error")
        return redirect(url_for("vendors.import_vendors"))

    stream = io.StringIO(file.stream.read().decode("utf-8-sig"))
    reader = csv.DictReader(stream)

    btype_map = {e.value: e for e in VendorBusinessType}
    pt_map = {e.value: e for e in VendorPaymentTerms}
    actor_id = _get_actor_id()

    # Build category name→id cache
    cats = {c.name.lower(): c.id for c in VendorCategory.query.filter_by(
        organization_id=org_id, is_active=True
    ).all()}

    results = {"created": 0, "skipped": 0, "errors": []}
    rows_preview = []

    for i, row in enumerate(reader, start=2):
        company_name = (row.get("company_name") or "").strip()
        if not company_name:
            results["errors"].append(f"Row {i}: company_name is required.")
            results["skipped"] += 1
            continue

        # Duplicate check
        exists = Vendor.query.filter_by(
            organization_id=org_id,
            company_name=company_name,
            is_deleted=False,
        ).first()
        if exists:
            results["errors"].append(
                f"Row {i}: '{company_name}' already exists ({exists.code}) — skipped."
            )
            results["skipped"] += 1
            continue

        cat_name = (row.get("category") or "").strip().lower()
        cat_id = cats.get(cat_name)
        if not cat_id and cat_name:
            # Auto-create category
            new_cat = VendorCategory(
                name=cat_name.title(), organization_id=org_id, created_by=actor_id
            )
            db.session.add(new_cat)
            db.session.flush()
            cats[cat_name] = new_cat.id
            cat_id = new_cat.id

        credit_raw = (row.get("credit_period_days") or "").strip()
        credit_days = int(credit_raw) if credit_raw.isdigit() else None

        code = _generate_vendor_code(org_id)
        vendor = Vendor(
            code=code,
            company_name=company_name,
            legal_name=(row.get("legal_name") or "").strip() or None,
            category_id=cat_id,
            contact_person=(row.get("contact_person") or "").strip() or None,
            designation=(row.get("designation") or "").strip() or None,
            phone=(row.get("phone") or "").strip() or None,
            alt_phone=(row.get("alt_phone") or "").strip() or None,
            email=(row.get("email") or "").strip() or None,
            website=(row.get("website") or "").strip() or None,
            gstin=((row.get("gstin") or "").strip().upper()) or None,
            pan=((row.get("pan") or "").strip().upper()) or None,
            business_type=btype_map.get((row.get("business_type") or "").strip()),
            address=(row.get("address") or "").strip() or None,
            city=(row.get("city") or "").strip() or None,
            state=(row.get("state") or "").strip() or None,
            pincode=(row.get("pincode") or "").strip() or None,
            country=(row.get("country") or "India").strip() or "India",
            payment_terms=pt_map.get((row.get("payment_terms") or "").strip()),
            credit_period_days=credit_days,
            products_services=(row.get("products_services") or "").strip() or None,
            notes=(row.get("notes") or "").strip() or None,
            status=VendorStatus.ACTIVE,
            verification_status=VendorVerificationStatus.UNVERIFIED,
            organization_id=org_id,
            created_by=actor_id,
        )
        db.session.add(vendor)
        db.session.flush()
        _log_activity(vendor, "imported", f"Vendor '{company_name}' imported via CSV.")
        results["created"] += 1
        rows_preview.append({"code": code, "name": company_name, "city": vendor.city or ""})

    try:
        db.session.commit()
        flash(
            f"Import complete: {results['created']} created, {results['skipped']} skipped.",
            "vendor_success",
        )
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Import vendors error: {e}", exc_info=True)
        flash("Import failed. Please check your file.", "vendor_error")
        return redirect(url_for("vendors.import_vendors"))

    return render_template(
        "vendors/import_result.html",
        results=results,
        rows_preview=rows_preview,
    )

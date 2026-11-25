"""
routes/orders.py
Unified Order Cockpit & Lifecycle Management Blueprint.
Provides full dashboard, detail cockpit, multi-vendor allocation,
procurement, deliveries, site fitting visits, and financial reconciliation endpoints.
"""
from decimal import Decimal
from datetime import datetime
from typing import Optional
import io
import re
from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    jsonify,
    current_app,
    send_file,
)
from flask_login import login_required, current_user
from sqlalchemy import or_, desc, func
from model import (
    db,
    OrderMaster,
    OrderItem,
    Customer,
    Lead,
    Vendor,
    VendorCategory,
    VendorBusinessType,
    VendorPaymentTerms,
    LeadSource,
    LeadStatus,
    CustomerStatus,
    Product,
    Project,
    Employee,
    Payment,
    Invoice,
    OrderDelivery,
    OrderConsignment,
    OrderConsignmentStatus,
    OrderDocument,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    OrderPaymentAllocationType,
    OrderClosureType,
    OrderDocumentType,
    VendorDocument,
)
from services.oms import (
    OrderMasterService,
    ProcurementService,
    DeliveryService,
    FinancialReconciliationService,
    OMSDocumentService,
    OMSNotificationService,
    OMSReportingService,
    OMSExportService,
    OMSError,
    OMSValidationError,
    OMSTransitionError,
    OMSInvariantError,
    OMSTenantIsolationError,
)
from services.oms.order_master_service import detect_lead_customer
from utils.security import require_roles
orders_bp = Blueprint("orders", __name__, url_prefix="/orders")


def _get_org_id() -> int:
    return current_user.organization_id



def _get_actor_id() -> Optional[int]:
    if hasattr(current_user, "employee") and current_user.employee:
        return current_user.employee.id
    return None



@orders_bp.route("/", methods=["GET"])
@login_required
def list_orders():
    """
    Premium Order Tracking Dashboard view.
    Information-rich order tracking with horizontal workflow indicators,
    multi-supplier allocations, QC tracking, financial summaries, and latest milestones.
    """
    org_id = _get_org_id()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 15, type=int)
    search_query = request.args.get("q", "").strip()
    status_filter = request.args.get("status")
    procurement_filter = request.args.get("procurement_status")
    delivery_filter = request.args.get("delivery_status")
    payment_filter = request.args.get("customer_payment_status")
    vendor_filter = request.args.get("vendor_id", type=int)
    pm_filter = request.args.get("pm_id", type=int)
    due_date_filter = request.args.get("due_date")
    sort_by = request.args.get("sort_by", "newest")
    scope = request.args.get("scope", "active")

    res = OrderMasterService.list_orders(
        org_id=org_id,
        page=page,
        per_page=per_page,
        search_query=search_query if search_query else None,
        status_filter=status_filter if status_filter else None,
        procurement_status_filter=procurement_filter if procurement_filter else None,
        delivery_status_filter=delivery_filter if delivery_filter else None,
        customer_payment_status_filter=payment_filter if payment_filter else None,
        vendor_id=vendor_filter,
        assigned_to_id=pm_filter,
        due_date_filter=due_date_filter,
        sort_by=sort_by,
        scope=scope,
    )

    # Aggregated metrics for cockpit header cards
    total_rev = float(
        db.session.query(func.coalesce(func.sum(OrderMaster.net_order_amount), Decimal("0.00")))
        .filter(OrderMaster.organization_id == org_id, OrderMaster.is_deleted == False)
        .scalar()
    )
    total_paid = float(
        db.session.query(func.coalesce(func.sum(OrderMaster.total_customer_paid), Decimal("0.00")))
        .filter(OrderMaster.organization_id == org_id, OrderMaster.is_deleted == False)
        .scalar()
    )

    from services.oms.status_engine import ACTIVE_MASTER_STATUSES

    metrics = {
        "total_active_orders": OrderMaster.query.filter(
            OrderMaster.organization_id == org_id,
            OrderMaster.is_deleted == False,
            OrderMaster.status.in_(ACTIVE_MASTER_STATUSES),
        ).count(),
        "total_orders": OrderMaster.query.filter_by(organization_id=org_id, is_deleted=False).count(),
        "draft_orders": OrderMaster.query.filter_by(
            organization_id=org_id,
            status=MasterOrderStatus.DRAFT.value,
            is_deleted=False
        ).count(),
        "in_procurement": OrderMaster.query.filter(
            OrderMaster.organization_id == org_id,
            OrderMaster.is_deleted == False,
            OrderMaster.status.in_([MasterOrderStatus.IN_PROCUREMENT.value, MasterOrderStatus.PARTIALLY_RECEIVED.value])
        ).count(),
        "in_delivery": OrderMaster.query.filter(
            OrderMaster.organization_id == org_id,
            OrderMaster.is_deleted == False,
            OrderMaster.status.in_([MasterOrderStatus.IN_DELIVERY.value, MasterOrderStatus.PARTIALLY_DELIVERED.value])
        ).count(),
        "in_fitting": OrderMaster.query.filter_by(
            organization_id=org_id,
            status=MasterOrderStatus.IN_FITTING.value,
            is_deleted=False
        ).count(),
        "completed": OrderMaster.query.filter(
            OrderMaster.organization_id == org_id,
            OrderMaster.is_deleted == False,
            OrderMaster.status.in_([MasterOrderStatus.COMPLETED.value, MasterOrderStatus.CLOSED.value])
        ).count(),
        "total_revenue": total_rev,
        "total_collected": total_paid,
        "total_outstanding": max(0.0, total_rev - total_paid),
    }

    # Fetch lookup data for dashboard filters
    vendors = Vendor.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Vendor.company_name.asc()).all()
    project_managers = Employee.query.filter_by(organization_id=org_id).order_by(Employee.name.asc()).all()

    if request.is_json or request.headers.get("Accept") == "application/json":
        serialized_orders = [
            {
                "id": o.id,
                "master_order_number": o.master_order_number,
                "customer_name": o.customer.name if o.customer else None,
                "lead_id": o.lead_id,
                "lead_ref": getattr(o, "tracking_card", {}).get("lead_ref"),
                "status": o.status,
                "procurement_status": o.procurement_status,
                "delivery_status": o.delivery_status,
                "fitting_status": o.fitting_status,
                "customer_payment_status": o.customer_payment_status,
                "net_order_amount": float(o.net_order_amount),
                "total_customer_paid": float(o.total_customer_paid),
                "order_date": o.order_date.strftime("%Y-%m-%d") if o.order_date else None,
                "tracking_card": getattr(o, "tracking_card", {}),
            }
            for o in res.items
        ]
        return jsonify({
            "success": True,
            "metrics": metrics,
            "orders": serialized_orders,
            "pagination": {
                "page": res.page,
                "per_page": res.per_page,
                "total": res.total,
                "pages": res.pages,
            }
        })

    return render_template(
        "orders/list.html",
        orders=res["items"],
        pagination=res,
        metrics=metrics,
        search_query=search_query,
        status_filter=status_filter,
        procurement_filter=procurement_filter,
        delivery_filter=delivery_filter,
        payment_filter=payment_filter,
        vendor_filter=vendor_filter,
        pm_filter=pm_filter,
        due_date_filter=due_date_filter,
        sort_by=sort_by,
        scope=scope,
        vendors=vendors,
        project_managers=project_managers,
        master_statuses=[s.value for s in MasterOrderStatus],
    )


@orders_bp.route("/api/leads/<int:lead_id>/confirm-to-order", methods=["POST"])
@login_required
def confirm_lead_to_order(lead_id: int):
    """
    Idempotent integration endpoint: Converts confirmed Lead into an OMS Master Order.
    Can be invoked by Lead Management upon reaching 'Order Confirmed'.
    """
    org_id = _get_org_id()
    actor_id = _get_employee_id()
    data = request.get_json(silent=True) or {}

    try:
        order = OrderMasterService.create_order_from_confirmed_lead(
            org_id=org_id,
            lead_id=lead_id,
            actor_id=actor_id,
            quotation_id=data.get("quotation_id"),
            project_id=data.get("project_id"),
            order_title=data.get("order_title"),
            notes=data.get("notes"),
            items_data=data.get("items"),
            selected_vendor_ids=data.get("selected_vendor_ids"),
            is_explicit_confirmation=data.get("is_explicit_confirmation", True),
        )
        return jsonify({
            "success": True,
            "order_id": order.id,
            "master_order_number": order.master_order_number,
            "status": order.status,
            "lead_id": order.lead_id,
            "customer_id": order.customer_id,
            "message": f"Master Order {order.master_order_number} successfully initialized from Lead #{lead_id}.",
        }), 200
    except (OMSValidationError, OMSTenantIsolationError) as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.exception(f"Unexpected error in confirm_lead_to_order for lead #{lead_id}: {e}")
        return jsonify({"success": False, "error": "Internal server error."}), 500



def _generate_vendor_code(org_id: int) -> str:
    """Generate next sequential VEN-XXXXX code per tenant."""
    last = (
        Vendor.query
        .filter_by(organization_id=org_id)
        .order_by(Vendor.id.desc())
        .first()
    )
    next_seq = (last.id + 1) if last else 1
    return f"VEN-{next_seq:05d}"


# ── LIVE AJAX APIS FOR CUSTOMER & VENDOR ONBOARDING ─────────────────────────

def _safe_customer_url(cust_id):
    try:
        return url_for("customers.view_customer", customer_id=cust_id)
    except Exception:
        return f"/customers/{cust_id}"


def _safe_lead_url(lead_id):
    try:
        return url_for("leads.view_lead", lead_id=lead_id)
    except Exception:
        return f"/lead/{lead_id}"


def _safe_vendor_url(vendor_id):
    try:
        return url_for("vendors.vendor_detail", vendor_id=vendor_id)
    except Exception:
        return f"/vendors/{vendor_id}"


@orders_bp.route("/api/leads/search", methods=["GET"])
@login_required
def api_search_leads():
    """Live search leads by name, company, phone, email, or city within tenant."""
    org_id = _get_org_id()
    q = request.args.get("q", "").strip()
    query = Lead.query.filter_by(organization_id=org_id, is_deleted=False)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Lead.name.ilike(like),
                Lead.company.ilike(like),
                Lead.phone_number.ilike(like),
                Lead.email.ilike(like),
                Lead.city.ilike(like),
                Lead.gst_number.ilike(like),
            )
        )
    limit = request.args.get("limit", 100, type=int)
    leads = query.order_by(Lead.name.asc()).limit(min(limit, 200)).all()
    results = [
        {
            "id": l.id,
            "name": l.name,
            "company": l.company or "",
            "phone_number": l.phone_number or "",
            "email": l.email or "",
            "city": l.city or "",
            "address": l.address or "",
            "state": l.state or "",
            "pincode": l.pincode or "",
            "gst_number": l.gst_number or "",
            "status": l.status.value if hasattr(l.status, "value") else str(l.status or ""),
        }
        for l in leads
    ]
    return jsonify({"success": True, "results": results})


@orders_bp.route("/api/leads/<int:lead_id>", methods=["GET"])
@login_required
def api_get_lead_detail(lead_id: int):
    """Fetch complete saved lead details and detect whether already associated with a Customer."""
    org_id = _get_org_id()
    try:
        l = Lead.query.filter_by(id=lead_id, organization_id=org_id, is_deleted=False).first()
        if not l:
            return jsonify({"success": False, "error": f"Lead #{lead_id} not found."}), 404

        detected_cust = detect_lead_customer(org_id, l)
        match_reason = None
        if detected_cust:
            if getattr(detected_cust, "lead_id", None) == l.id or getattr(l, "converted_customer", None) == detected_cust:
                match_reason = "Direct Lead Conversion Record"
            else:
                l_phone = "".join(filter(str.isdigit, str(getattr(l, "phone_number", "") or "")))
                c_phone = "".join(filter(str.isdigit, str(getattr(detected_cust, "phone_number", "") or "")))
                if len(l_phone) >= 10 and len(c_phone) >= 10 and l_phone[-10:] == c_phone[-10:]:
                    match_reason = "Matching Verified Mobile Number"
                else:
                    match_reason = "Matching Verified Email"

        return jsonify({
            "success": True,
            "lead": {
                "id": l.id,
                "name": l.name,
                "company": l.company or "",
                "phone_number": l.phone_number or "",
                "email": l.email or "",
                "address": l.address or "",
                "billing_address": l.address or "",
                "shipping_address": l.address or "",
                "site_address": l.address or "",
                "city": l.city or "",
                "state": l.state or "",
                "pincode": l.pincode or "",
                "gst_number": l.gst_number or "",
                "status": l.status.value if hasattr(l.status, "value") else str(l.status or ""),
                "notes": l.notes or "",
                "profile_url": _safe_lead_url(l.id),
            },
            "is_already_customer": bool(detected_cust),
            "associated_customer": {
                "id": detected_cust.id,
                "name": detected_cust.name,
                "company": detected_cust.company or "",
                "phone_number": detected_cust.phone_number or "",
                "email": detected_cust.email or "",
                "match_reason": match_reason,
                "profile_url": _safe_customer_url(detected_cust.id),
            } if detected_cust else None,
        })
    except Exception as e:
        current_app.logger.exception(f"Error retrieving lead #{lead_id} details")
        return jsonify({"success": False, "error": f"Failed to load lead details: {str(e)}"}), 500


@orders_bp.route("/api/customers/search", methods=["GET"])
@login_required
def api_search_customers():
    """Live search customers by name, company, phone, email, or ID within tenant."""
    org_id = _get_org_id()
    q = request.args.get("q", "").strip()
    query = Customer.query.filter_by(organization_id=org_id, is_deleted=False)
    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(
                Customer.name.ilike(like),
                Customer.company.ilike(like),
                Customer.phone_number.ilike(like),
                Customer.email.ilike(like),
                Customer.city.ilike(like),
                Customer.gst_number.ilike(like),
            )
        )
    limit = request.args.get("limit", 100, type=int)
    customers = query.order_by(Customer.name.asc()).limit(min(limit, 200)).all()
    results = [
        {
            "id": c.id,
            "name": c.name,
            "company": c.company or "",
            "phone_number": c.phone_number or "",
            "email": c.email or "",
            "city": c.city or "",
            "address": c.address or "",
            "state": c.state or "",
            "pincode": c.pincode or "",
            "gst_number": c.gst_number or "",
            "gst_status": c.gst_status or "",
            "business_type": c.business_type or "",
            "notes": c.notes or "",
        }
        for c in customers
    ]
    return jsonify({"success": True, "results": results})


@orders_bp.route("/api/customers/<int:customer_id>", methods=["GET"])
@login_required
def api_get_customer_detail(customer_id: int):
    """Fetch complete saved customer details for auto-populating order fields."""
    org_id = _get_org_id()
    c = Customer.query.filter_by(id=customer_id, organization_id=org_id, is_deleted=False).first()
    if not c:
        return jsonify({"success": False, "error": f"Customer #{customer_id} not found."}), 404

    return jsonify({
        "success": True,
        "customer": {
            "id": c.id,
            "name": c.name,
            "company": c.company or "",
            "phone_number": c.phone_number or "",
            "email": c.email or "",
            "address": c.address or "",
            "billing_address": c.address or "",
            "shipping_address": c.address or "",
            "site_address": c.address or "",
            "city": c.city or "",
            "state": c.state or "",
            "pincode": c.pincode or "",
            "business_type": c.business_type or "",
            "gst_status": c.gst_status or "",
            "gst_number": c.gst_number or "",
            "notes": c.notes or "",
            "profile_url": _safe_customer_url(c.id),
        }
    })


@orders_bp.route("/api/customers/inline", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def api_create_customer_inline():
    """Inline customer creation within the Master Order form."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1
    data = request.get_json() if request.is_json else request.form

    name = data.get("name", "").strip()
    email = data.get("email", "").strip()
    raw_phone = data.get("phone_number", "")
    phone_number = re.sub(r"\D", "", str(raw_phone))

    if not name:
        msg = "Customer name is required."
        return jsonify({"success": False, "error": msg, "message": msg}), 400
    if not phone_number or len(phone_number) != 10:
        msg = "Valid 10-digit mobile number is required."
        return jsonify({"success": False, "error": msg, "message": msg}), 400
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        msg = "A valid email address is required."
        return jsonify({"success": False, "error": msg, "message": msg}), 400

    # Duplicate checks in tenant
    existing_phone = Customer.query.filter_by(phone_number=phone_number, organization_id=org_id, is_deleted=False).first()
    if existing_phone:
        msg = f"Customer with phone '{phone_number}' already exists: '{existing_phone.name}'."
        return jsonify({"success": False, "error": msg, "message": msg}), 409

    if email:
        existing_email = Customer.query.filter_by(email=email, organization_id=org_id, is_deleted=False).first()
        if existing_email:
            msg = f"Customer with email '{email}' already exists: '{existing_email.name}'."
            return jsonify({"success": False, "error": msg, "message": msg}), 409

    gst_num = data.get("gst_number", "").strip().upper() or None
    billing_addr = data.get("billing_address") or data.get("address", "").strip() or None

    new_cust = Customer(
        name=name,
        company=data.get("company", "").strip() or None,
        phone_number=phone_number,
        email=email,
        address=billing_addr,
        city=data.get("city", "").strip() or None,
        state=data.get("state", "").strip() or None,
        pincode=data.get("pincode", "").strip() or None,
        business_type=data.get("business_type", "").strip() or None,
        gst_status=data.get("gst_status", "").strip() or None,
        gst_number=gst_num,
        notes=data.get("notes", "").strip() or None,
        organization_id=org_id,
        created_by=actor_id,
    )
    db.session.add(new_cust)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Customer '{new_cust.name}' created successfully.",
        "customer": {
            "id": new_cust.id,
            "name": new_cust.name,
            "company": new_cust.company or "",
            "phone_number": new_cust.phone_number,
            "email": new_cust.email,
            "address": new_cust.address or "",
            "billing_address": new_cust.address or "",
            "shipping_address": new_cust.address or "",
            "site_address": new_cust.address or "",
            "city": new_cust.city or "",
            "state": new_cust.state or "",
            "pincode": new_cust.pincode or "",
            "gst_number": new_cust.gst_number or "",
            "gst_status": new_cust.gst_status or "",
            "business_type": new_cust.business_type or "",
            "profile_url": _safe_customer_url(new_cust.id),
        }
    }), 201


@orders_bp.route("/api/leads/inline", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def api_create_lead_inline():
    """Inline Lead creation within the Master Order form."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1
    data = request.get_json() if request.is_json else request.form

    name = data.get("name", "").strip()
    email = data.get("email", "").strip() or None
    raw_phone = data.get("phone_number", "")
    phone_digits = re.sub(r"\D", "", str(raw_phone))
    if len(phone_digits) == 12 and phone_digits.startswith("91"):
        phone_digits = phone_digits[2:]
    phone_number = phone_digits or None

    if not name:
        msg = "Lead name is required."
        return jsonify({"success": False, "error": msg, "message": msg}), 400
    if not phone_number and not email:
        msg = "Please provide at least a Phone Number or Email."
        return jsonify({"success": False, "error": msg, "message": msg}), 400
    if phone_number and len(phone_number) != 10:
        msg = "Phone number must be exactly 10 digits."
        return jsonify({"success": False, "error": msg, "message": msg}), 400
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        msg = "A valid email address is required."
        return jsonify({"success": False, "error": msg, "message": msg}), 400

    # Duplicate checks in tenant (active leads)
    if phone_number:
        existing_phone = Lead.query.filter_by(phone_number=phone_number, organization_id=org_id, is_deleted=False).first()
        if existing_phone:
            msg = f"A lead with phone '{phone_number}' already exists in your organization: '{existing_phone.name}'."
            return jsonify({"success": False, "error": msg, "message": msg}), 409

    if email:
        existing_email = Lead.query.filter_by(email=email, organization_id=org_id, is_deleted=False).first()
        if existing_email:
            msg = f"A lead with email '{email}' already exists in your organization: '{existing_email.name}'."
            return jsonify({"success": False, "error": msg, "message": msg}), 409

    source_val = data.get("source", "Other")
    source_map = {e.value: e for e in LeadSource}
    lead_source = source_map.get(source_val, LeadSource.OTHER)

    status_val = data.get("status", "New")
    status_map = {e.value: e for e in LeadStatus}
    lead_status = status_map.get(status_val, LeadStatus.NEW)

    gst_num = data.get("gst_number", "").strip().upper() or None
    addr = data.get("address", "").strip() or None

    new_lead = Lead(
        name=name,
        company=data.get("company", "").strip() or None,
        phone_number=phone_number,
        email=email,
        address=addr,
        city=data.get("city", "").strip() or None,
        state=data.get("state", "").strip() or None,
        pincode=data.get("pincode", "").strip() or None,
        gst_number=gst_num,
        notes=data.get("notes", "").strip() or None,
        source=lead_source,
        status=lead_status,
        organization_id=org_id,
        created_by=actor_id,
    )
    db.session.add(new_lead)
    db.session.commit()

    # Detect if this new lead matches an existing Customer in the CRM
    detected_cust = detect_lead_customer(org_id, new_lead)
    match_reason = None
    if detected_cust:
        match_reason = "Matching Verified Mobile Number or Email"

    return jsonify({
        "success": True,
        "message": f"Lead '{new_lead.name}' created successfully.",
        "lead": {
            "id": new_lead.id,
            "name": new_lead.name,
            "company": new_lead.company or "",
            "phone_number": new_lead.phone_number or "",
            "email": new_lead.email or "",
            "address": new_lead.address or "",
            "billing_address": new_lead.address or "",
            "shipping_address": new_lead.address or "",
            "site_address": new_lead.address or "",
            "city": new_lead.city or "",
            "state": new_lead.state or "",
            "pincode": new_lead.pincode or "",
            "gst_number": new_lead.gst_number or "",
            "status": new_lead.status.value if hasattr(new_lead.status, "value") else str(new_lead.status or ""),
            "notes": new_lead.notes or "",
            "profile_url": _safe_lead_url(new_lead.id),
        },
        "is_already_customer": bool(detected_cust),
        "associated_customer": {
            "id": detected_cust.id,
            "name": detected_cust.name,
            "company": detected_cust.company or "",
            "phone_number": detected_cust.phone_number or "",
            "email": detected_cust.email or "",
            "match_reason": match_reason,
            "profile_url": _safe_customer_url(detected_cust.id),
        } if detected_cust else None,
    }), 201


@orders_bp.route("/api/vendors/search", methods=["GET"])
@login_required
def api_search_vendors():
    """Live search vendors by company name, code, contact, phone, email, GSTIN, city."""
    org_id = _get_org_id()
    q = request.args.get("q", "").strip()
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
                Vendor.city.ilike(like),
                Vendor.products_services.ilike(like),
            )
        )
    limit = request.args.get("limit", 100, type=int)
    vendors = query.order_by(Vendor.company_name.asc()).limit(min(limit, 200)).all()
    results = [
        {
            "id": v.id,
            "code": v.code,
            "company_name": v.company_name,
            "contact_person": v.contact_person or "",
            "phone": v.phone or "",
            "email": v.email or "",
            "city": v.city or "",
            "gstin": v.gstin or "",
            "category": v.category.name if v.category else "",
            "status": v.status_display,
        }
        for v in vendors
    ]
    return jsonify({"success": True, "results": results})


@orders_bp.route("/api/vendors/<int:vendor_id>", methods=["GET"])
@login_required
def api_get_vendor_detail(vendor_id: int):
    """Fetch complete saved vendor details for displaying in the order selection."""
    org_id = _get_org_id()
    v = Vendor.query.filter_by(id=vendor_id, organization_id=org_id, is_deleted=False).first()
    if not v:
        return jsonify({"success": False, "error": f"Vendor #{vendor_id} not found."}), 404

    is_privileged = (current_user.is_authenticated and current_user.role and current_user.role.value in ["admin", "manager"])
    if is_privileged:
        bank_data = {
            "bank_name": v.bank_name or "",
            "bank_account_no": v.bank_account_no or "",
            "bank_ifsc": v.bank_ifsc or "",
        }
    else:
        bank_data = {
            "bank_name": "••••••••" if v.bank_name else "",
            "bank_account_no": "••••••••" if v.bank_account_no else "",
            "bank_ifsc": "••••••••" if v.bank_ifsc else "",
        }

    return jsonify({
        "success": True,
        "vendor": {
            "id": v.id,
            "code": v.code,
            "company_name": v.company_name,
            "legal_name": v.legal_name or "",
            "category_id": v.category_id,
            "category": v.category.name if v.category else "",
            "contact_person": v.contact_person or "",
            "designation": v.designation or "",
            "phone": v.phone or "",
            "alt_phone": v.alt_phone or "",
            "email": v.email or "",
            "website": v.website or "",
            "gstin": v.gstin or "",
            "pan": v.pan or "",
            "business_type": v.business_type.value if v.business_type else "",
            "address": v.address or "",
            "city": v.city or "",
            "state": v.state or "",
            "pincode": v.pincode or "",
            "country": v.country or "India",
            "warehouse_address": v.warehouse_address or "",
            "payment_terms": v.payment_terms.value if v.payment_terms else "",
            "notes": v.notes or "",
            "profile_url": _safe_vendor_url(v.id),
            **bank_data,
        }
    })


@orders_bp.route("/api/vendors/inline", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def api_create_vendor_inline():
    """Inline vendor creation within the Master Order form."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1
    data = request.get_json() if request.is_json else request.form

    company_name = data.get("company_name", "").strip()
    if not company_name:
        msg = "Vendor / Business Name is required."
        return jsonify({"success": False, "error": msg, "message": msg}), 400

    existing = Vendor.query.filter_by(organization_id=org_id, company_name=company_name, is_deleted=False).first()
    if existing:
        msg = f"Vendor '{company_name}' already exists ({existing.code})."
        return jsonify({"success": False, "error": msg, "message": msg}), 409

    code = _generate_vendor_code(org_id)
    btype_val = data.get("business_type", "")
    btype_map = {e.value: e for e in VendorBusinessType}
    pt_val = data.get("payment_terms", "")
    pt_map = {e.value: e for e in VendorPaymentTerms}

    cat_id = data.get("category_id")
    if cat_id:
        try:
            cat_id = int(cat_id)
            c_check = VendorCategory.query.filter_by(id=cat_id, organization_id=org_id).first()
            if not c_check:
                cat_id = None
        except (ValueError, TypeError):
            cat_id = None

    is_privileged = (current_user.is_authenticated and current_user.role and current_user.role.value in ["admin", "manager"])
    bank_name = data.get("bank_name", "").strip() or None if is_privileged else None
    bank_account_no = data.get("bank_account_no", "").strip() or None if is_privileged else None
    bank_ifsc = data.get("bank_ifsc", "").strip().upper() or None if is_privileged else None

    new_vend = Vendor(
        code=code,
        company_name=company_name,
        legal_name=data.get("legal_name", "").strip() or None,
        category_id=cat_id,
        products_services=data.get("products_services", "").strip() or None,
        contact_person=data.get("contact_person", "").strip() or None,
        designation=data.get("designation", "").strip() or None,
        phone=data.get("phone", "").strip() or None,
        alt_phone=data.get("alt_phone", "").strip() or None,
        email=data.get("email", "").strip() or None,
        website=data.get("website", "").strip() or None,
        gstin=data.get("gstin", "").strip().upper() or None,
        pan=data.get("pan", "").strip().upper() or None,
        business_type=btype_map.get(btype_val),
        address=data.get("address", "").strip() or None,
        city=data.get("city", "").strip() or None,
        state=data.get("state", "").strip() or None,
        pincode=data.get("pincode", "").strip() or None,
        country=data.get("country", "India").strip() or "India",
        warehouse_address=data.get("warehouse_address", "").strip() or None,
        payment_terms=pt_map.get(pt_val),
        bank_name=bank_name,
        bank_account_no=bank_account_no,
        bank_ifsc=bank_ifsc,
        notes=data.get("notes", "").strip() or None,
        organization_id=org_id,
        created_by=actor_id,
    )
    db.session.add(new_vend)
    db.session.commit()

    return jsonify({
        "success": True,
        "message": f"Vendor '{new_vend.company_name}' ({new_vend.code}) created successfully.",
        "vendor": {
            "id": new_vend.id,
            "code": new_vend.code,
            "company_name": new_vend.company_name,
            "contact_person": new_vend.contact_person or "",
            "phone": new_vend.phone or "",
            "email": new_vend.email or "",
            "city": new_vend.city or "",
            "gstin": new_vend.gstin or "",
            "category": new_vend.category.name if new_vend.category else "",
            "profile_url": _safe_vendor_url(new_vend.id),
        }
    }), 201


@orders_bp.route("/new", methods=["GET"])
@login_required
def new_order():
    """Form to create a new Master Order with full onboarding workflow."""
    org_id = _get_org_id()
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Customer.name.asc()).all()
    leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Lead.name.asc()).all()
    vendors = Vendor.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Vendor.company_name.asc()).all()
    vendor_categories = VendorCategory.query.filter_by(organization_id=org_id, is_active=True).order_by(VendorCategory.name.asc()).all()
    products = Product.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Product.name.asc()).all()
    projects = Project.query.filter_by(organization_id=org_id).order_by(Project.name.asc()).all()
    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Employee.name.asc()).all()
    next_order_number = OrderMasterService.generate_order_number(org_id)
    is_privileged = (current_user.is_authenticated and current_user.role and current_user.role.value in ["admin", "manager"])

    return render_template(
        "orders/create.html",
        customers=customers,
        leads=leads,
        vendors=vendors,
        vendor_categories=vendor_categories,
        products=products,
        projects=projects,
        employees=employees,
        next_order_number=next_order_number,
        is_privileged=is_privileged,
        vendor_business_types=[e.value for e in VendorBusinessType],
        vendor_payment_terms=[e.value for e in VendorPaymentTerms],
    )


@orders_bp.route("/", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def create_order():
    """Create a new Master Order with 7-section data, multiple vendors and line items."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    if request.is_json:
        data = request.get_json() or {}
        if "entity_mode" not in data:
            data["entity_mode"] = "lead" if ("lead_id" in data and not data.get("customer_id")) else "customer"
    else:
        # Form submission: parse all 7 sections
        entity_mode = request.form.get("entity_mode", "customer").strip().lower()
        lead_id = request.form.get("selected_lead_id", type=int) or request.form.get("lead_id", type=int)
        confirmed_override = request.form.get("confirmed_lead_override") in ["true", "True", "1", True, "yes"]
        data = {
            "entity_mode": entity_mode,
            "customer_id": request.form.get("customer_id", type=int) if request.form.get("customer_id") else None,
            "lead_id": lead_id,
            "confirmed_lead_override": confirmed_override,
            "order_title": request.form.get("order_title", "").strip(),
            "action_type": request.form.get("action_type", "save_draft").strip(),
            "project_id": request.form.get("project_id", type=int) or None,
            "quotation_id": request.form.get("quotation_id", type=int) or None,
            "assigned_to_id": request.form.get("assigned_to_id", type=int) or None,
            "operations_employee_id": request.form.get("operations_employee_id", type=int) or None,
            "priority": request.form.get("priority", "Normal").strip(),
            "shipping_address": request.form.get("shipping_address", "").strip() or None,
            "billing_address": request.form.get("billing_address", "").strip() or None,
            "site_address": request.form.get("site_address", "").strip() or None,
            "notes": request.form.get("notes", "").strip() or None,
            "order_description": request.form.get("order_description", "").strip() or None,
            "selected_vendor_ids": [int(v) for v in request.form.getlist("selected_vendor_ids") if str(v).isdigit()],
            "commercial_details": {
                "currency": request.form.get("currency", "INR").strip(),
                "payment_terms": request.form.get("payment_terms", "").strip(),
                "advance_required": request.form.get("advance_required") in ["true", "True", "1", True, "on", "yes"],
                "advance_percentage": request.form.get("advance_percentage", type=float) or 0.0,
                "advance_amount": request.form.get("advance_amount", type=float) or 0.0,
                "overall_discount": request.form.get("overall_discount", type=float) or 0.0,
                "additional_charges": request.form.get("additional_charges", type=float) or 0.0,
                "freight_charges": request.form.get("freight_charges", type=float) or 0.0,
                "gst_treatment": request.form.get("gst_treatment", "INTRA_STATE").strip(),
                "terms_and_conditions": request.form.get("terms_and_conditions", "").strip(),
            },
            "delivery_details": {
                "delivery_required": request.form.get("delivery_required") in ["true", "True", "1", True, "on", "yes"],
                "delivery_type": request.form.get("delivery_type", "DOORSTEP").strip(),
                "expected_delivery_date": request.form.get("expected_delivery_date", "").strip() or None,
                "delivery_instructions": request.form.get("delivery_instructions", "").strip() or None,
                "delivery_contact_person": request.form.get("delivery_contact_person", "").strip() or None,
                "delivery_contact_phone": request.form.get("delivery_contact_phone", "").strip() or None,
            },
            "installation_details": {
                "installation_required": request.form.get("installation_required") in ["true", "True", "1", True, "on", "yes"],
                "installation_address": request.form.get("installation_address", "").strip() or None,
                "site_contact_person": request.form.get("site_contact_person", "").strip() or None,
                "site_contact_phone": request.form.get("site_contact_phone", "").strip() or None,
                "site_readiness_status": request.form.get("site_readiness_status", "READY").strip(),
                "expected_fitting_date": request.form.get("expected_fitting_date", "").strip() or None,
                "installation_notes": request.form.get("installation_notes", "").strip() or None,
            },
            "items": []
        }

        # Parse items from indexed form fields
        item_indices = set()
        for k in request.form.keys():
            if k.startswith("item_description_"):
                idx = k.split("_")[-1]
                item_indices.add(idx)

        for idx in sorted(item_indices, key=lambda x: int(x) if x.isdigit() else 0):
            desc = request.form.get(f"item_description_{idx}", "").strip()
            qty = request.form.get(f"quantity_{idx}", 0)
            price = request.form.get(f"unit_price_{idx}", 0)
            tax = request.form.get(f"tax_rate_{idx}", 0)
            prod_id = request.form.get(f"product_id_{idx}", type=int) or None
            sku = request.form.get(f"sku_{idx}", "").strip() or None
            uom = request.form.get(f"uom_{idx}", "").strip() or None
            disc_type = request.form.get(f"discount_type_{idx}", "none").strip()
            disc_val = request.form.get(f"discount_val_{idx}", 0)
            disc_amt = request.form.get(f"discount_amount_{idx}", 0)
            alloc_vend = request.form.get(f"allocated_vendor_id_{idx}", type=int) or None
            it_notes = request.form.get(f"item_notes_{idx}", "").strip() or None

            specs_parts = []
            if sku: specs_parts.append(f"SKU: {sku}")
            if uom: specs_parts.append(f"UOM: {uom}")
            if it_notes: specs_parts.append(it_notes)
            combined_specs = " | ".join(specs_parts) if specs_parts else None

            if desc:
                data["items"].append({
                    "item_description": desc,
                    "product_id": prod_id,
                    "quantity": qty,
                    "unit_price": price,
                    "tax_rate": tax,
                    "discount_amount": disc_amt or disc_val,
                    "specifications": combined_specs,
                    "allocated_vendor_id": alloc_vend,
                })

    # Parse date strings if present
    order_date = None
    if data.get("order_date"):
        try:
            order_date = datetime.strptime(data["order_date"][:10], "%Y-%m-%d")
        except Exception:
            order_date = None

    exp_deliv_date = None
    deliv_date_str = data.get("expected_delivery_date") or data.get("delivery_details", {}).get("expected_delivery_date")
    if deliv_date_str:
        try:
            exp_deliv_date = datetime.strptime(deliv_date_str[:10], "%Y-%m-%d")
        except Exception:
            exp_deliv_date = None

    exp_fit_date = None
    fit_date_str = data.get("expected_fitting_date") or data.get("installation_details", {}).get("expected_fitting_date")
    if fit_date_str:
        try:
            exp_fit_date = datetime.strptime(fit_date_str[:10], "%Y-%m-%d")
        except Exception:
            exp_fit_date = None

    try:
        order = OrderMasterService.create_order(
            org_id=org_id,
            customer_id=data.get("customer_id"),
            lead_id=data.get("lead_id"),
            confirmed_lead_override=data.get("confirmed_lead_override", False),
            entity_mode=data.get("entity_mode", "customer"),
            order_title=data.get("order_title"),
            items_data=data.get("items", []),
            project_id=data.get("project_id"),
            quotation_id=data.get("quotation_id"),
            assigned_to_id=data.get("assigned_to_id"),
            shipping_address=data.get("shipping_address"),
            billing_address=data.get("billing_address"),
            site_address=data.get("site_address"),
            notes=data.get("notes") or data.get("order_description"),
            actor_id=actor_id,
            selected_vendor_ids=data.get("selected_vendor_ids"),
            commercial_details=data.get("commercial_details"),
            delivery_details=data.get("delivery_details"),
            installation_details=data.get("installation_details"),
            expected_delivery_date=exp_deliv_date,
            expected_fitting_date=exp_fit_date,
            action_type=data.get("action_type", "save_draft"),
            order_date=order_date,
            create_initial_revision=True,
        )

        # Handle attached documents if any
        if not request.is_json and "order_documents" in request.files:
            files = request.files.getlist("order_documents")
            for uploaded_file in files:
                if uploaded_file and uploaded_file.filename:
                    try:
                        OMSDocumentService.save_order_document(
                            org_id=org_id,
                            order_id=order.id,
                            file=uploaded_file,
                            document_type=OrderDocumentType.OTHER.value,
                            title=uploaded_file.filename,
                            uploaded_by_id=actor_id,
                        )
                    except Exception as doc_err:
                        current_app.logger.warning(f"Error saving order document: {doc_err}")

        # Handle Vendor Proforma Invoices attached in Section 02 (Vendor Details)
        if not request.is_json:
            for v_id in data.get("selected_vendor_ids", []):
                pi_key = f"vendor_pi_{v_id}"
                if pi_key in request.files:
                    v_file = request.files.get(pi_key)
                    if v_file and v_file.filename:
                        v_obj = Vendor.query.filter_by(id=v_id, organization_id=org_id).first()
                        v_name = v_obj.company_name if v_obj else f"Vendor #{v_id}"
                        try:
                            saved_pi = OMSDocumentService.save_order_document(
                                org_id=org_id,
                                order_id=order.id,
                                file=v_file,
                                document_type=OrderDocumentType.VENDOR_PROFORMA_INVOICE.value,
                                title=f"Vendor PI: {v_name} - {v_file.filename}",
                                uploaded_by_id=actor_id,
                            )
                            if v_obj:
                                v_doc = VendorDocument(
                                    vendor_id=v_obj.id,
                                    filename=os.path.basename(saved_pi.file_path),
                                    original_name=v_file.filename,
                                    file_type=v_file.filename.rsplit(".", 1)[-1].lower() if "." in v_file.filename else None,
                                    file_size=saved_pi.file_size,
                                    document_type="Proforma Invoice",
                                    description=f"Master Order {order.master_order_number} Proforma Invoice",
                                    uploaded_by=actor_id,
                                    organization_id=org_id,
                                )
                                db.session.add(v_doc)
                        except Exception as pi_err:
                            current_app.logger.warning(f"Error saving vendor PI for vendor #{v_id}: {pi_err}")

            if "vendor_pi_additional" in request.files:
                for add_file in request.files.getlist("vendor_pi_additional"):
                    if add_file and add_file.filename:
                        try:
                            OMSDocumentService.save_order_document(
                                org_id=org_id,
                                order_id=order.id,
                                file=add_file,
                                document_type=OrderDocumentType.VENDOR_PROFORMA_INVOICE.value,
                                title=f"Vendor PI - {add_file.filename}",
                                uploaded_by_id=actor_id,
                            )
                        except Exception as pi_err:
                            current_app.logger.warning(f"Error saving additional vendor PI: {pi_err}")

        # Handle Delivery Challan uploaded in Section 06 (Transit & Delivery Parameters)
        if not request.is_json and "delivery_challan_file" in request.files:
            delv_file = request.files.get("delivery_challan_file")
            if delv_file and delv_file.filename:
                try:
                    c_num = request.form.get("delivery_challan_number", "").strip() or None
                    c_date_raw = request.form.get("delivery_challan_date", "").strip()
                    c_date = datetime.strptime(c_date_raw, "%Y-%m-%d") if c_date_raw else datetime.utcnow()
                    doc_title = f"Delivery Challan - {c_num}" if c_num else f"Delivery Challan — {delv_file.filename}"
                    saved_dc = OMSDocumentService.save_order_document(
                        org_id=org_id,
                        order_id=order.id,
                        file=delv_file,
                        document_type=OrderDocumentType.DELIVERY_CHALLAN.value,
                        title=doc_title,
                        uploaded_by_id=actor_id,
                    )
                    delv_number = DeliveryService.generate_delivery_number(org_id)
                    cust_snap = (order.initial_snapshot.get("customer_snapshot", {}) if getattr(order, "initial_snapshot", None) else {}) or {}
                    rec_name = (
                        request.form.get("delivery_contact_person", "").strip()
                        or cust_snap.get("contact_person")
                        or (order.customer.name if order.customer else "Site Recipient")
                    )
                    rec_phone = (
                        request.form.get("delivery_contact_phone", "").strip()
                        or cust_snap.get("mobile")
                        or (order.customer.phone_number if order.customer else None)
                    )
                    delv_rec = OrderDelivery(
                        delivery_number=delv_number,
                        order_id=order.id,
                        organization_id=org_id,
                        delivery_date=c_date,
                        recipient_name=rec_name,
                        recipient_phone=rec_phone,
                        delivery_address=order.shipping_address or order.site_address,
                        challan_number=c_num or delv_number,
                        status=OrderDeliveryStatus.DELIVERED.value,
                        delivered_by_id=actor_id,
                        proof_of_delivery_document_id=saved_dc.id,
                        notes=f"Uploaded at order creation: {delv_file.filename}",
                    )
                    db.session.add(delv_rec)
                    order.delivery_status = OrderDeliveryStatus.DELIVERED.value
                    for it in order.items:
                        if not it.is_deleted:
                            it.delivered_quantity = it.quantity
                except Exception as dc_err:
                    current_app.logger.warning(f"Error saving order delivery challan: {dc_err}")

        db.session.commit()

        status_msg = "confirmed" if order.status == MasterOrderStatus.CONFIRMED.value else "saved as draft"
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({
                "success": True,
                "message": f"Order {order.master_order_number} {status_msg} successfully.",
                "order_id": order.id,
                "master_order_number": order.master_order_number,
                "status": order.status,
            }), 201

        flash(f"Order {order.master_order_number} {status_msg} successfully.", "order_success")
        return redirect(url_for("orders.detail_order", order_id=order.id))

    except OMSTenantIsolationError as e:
        db.session.rollback()
        msg = str(e)
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"success": False, "error": msg, "message": msg}), 403
        flash(f"Permission denied: {msg}", "order_error")
        return redirect(url_for("orders.new_order"))
    except OMSError as e:
        db.session.rollback()
        msg = str(e)
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"success": False, "error": msg, "message": msg}), 400
        flash(f"Failed to create order: {msg}", "order_error")
        return redirect(url_for("orders.new_order"))
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Unexpected error creating order")
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"An unexpected error occurred: {str(e)}", "order_error")
        return redirect(url_for("orders.new_order"))



@orders_bp.route("/<int:order_id>", methods=["GET"])
@login_required
def detail_order(order_id: int):
    """
    Unified Order Cockpit Single-Page Hub.
    Aggregates items, multi-vendor allocations, purchase orders, consignments,
    deliveries, site fitting visits, cash allocations, bills, and immutable history.
    """
    org_id = _get_org_id()
    try:
        cockpit_data = OrderMasterService.get_order_cockpit_details(org_id, order_id)
    except OMSError as e:
        flash(str(e), "order_error")
        return redirect(url_for("orders.list_orders"))

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify(cockpit_data)

    vendors = Vendor.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Vendor.company_name.asc()).all()
    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Employee.name.asc()).all()
    # Payments available for allocation (customer payments)
    order_customer_id = cockpit_data["order"]["customer"]["id"] if cockpit_data["order"]["customer"] else None
    available_payments = Payment.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Payment.id.desc()).limit(50).all()

    from services.oms.order_master_service import VALID_MASTER_TRANSITIONS
    current_status = cockpit_data["order"]["status"]
    if hasattr(current_status, "value"):
        current_status = current_status.value
    else:
        current_status = str(current_status or "")

    LIFECYCLE_ORDER = [
        "CONFIRMED",
        "IN_PROCUREMENT",
        "PARTIALLY_RECEIVED",
        "RECEIVED",
        "IN_DELIVERY",
        "PARTIALLY_DELIVERED",
        "DELIVERED",
        "IN_FITTING",
        "COMPLETED",
        "CLOSED",
        "ON_HOLD",
        "CANCELLED",
        "DRAFT",
    ]
    raw_valid = VALID_MASTER_TRANSITIONS.get(current_status, set())
    valid_transitions = [s for s in LIFECYCLE_ORDER if s in raw_valid]
    for s in raw_valid:
        if s not in valid_transitions:
            valid_transitions.append(s)

    master_statuses = [s for s in LIFECYCLE_ORDER if s in [m.value for m in MasterOrderStatus]]

    status_labels_map = {
        "DRAFT": "Stage 04: Order Drafted (Pending Confirmation)",
        "CONFIRMED": "Stage 04: Order Confirmed (Ready for Procurement)",
        "IN_PROCUREMENT": "Stage 05: Vendor & QC (In Procurement / PO Issued)",
        "PARTIALLY_RECEIVED": "Stage 05: Vendor & QC (Partially Received from Fabricator)",
        "RECEIVED": "Stage 05: Vendor & QC (Fabrication Complete / QC Passed)",
        "IN_DELIVERY": "Stage 06: Transport (Out for Delivery / In Transit)",
        "PARTIALLY_DELIVERED": "Stage 06: Transport (Partially Delivered to Site)",
        "DELIVERED": "Stage 07: Unloading & Fitting (Site Delivered / POD Signed)",
        "IN_FITTING": "Stage 07: Unloading & Fitting (Installation Active)",
        "COMPLETED": "Stage 07: Unloading & Fitting (Project Completed & Handover)",
        "CLOSED": "Stage 07: Closed & Reconciled",
        "ON_HOLD": "⏸ Put On Hold (Site / Client Delay)",
        "CANCELLED": "⛔ Cancel Order",
    }

    return render_template(
        "orders/detail.html",
        cockpit=cockpit_data,
        vendors=vendors,
        employees=employees,
        available_payments=available_payments,
        master_statuses=[s.value for s in MasterOrderStatus],
        valid_transitions=valid_transitions,
        status_labels_map=status_labels_map,
    )



@orders_bp.route("/<int:order_id>/status", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def transition_status(order_id: int):
    """Transition master order status via validated state machine."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    to_status = data.get("to_status", "").strip()
    remarks = data.get("remarks", "").strip()
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"

    try:
        prev_order = OrderMaster.query.filter_by(
            id=order_id, organization_id=org_id, is_deleted=False
        ).first()
        prev_status = prev_order.status if prev_order else None

        order = OrderMasterService.transition_order_status(
            org_id=org_id,
            order_id=order_id,
            to_status=to_status,
            remarks=remarks if remarks else None,
            actor_id=actor_id,
        )
        # Trigger real-time notification
        try:
            OMSNotificationService.notify_status_transition(
                org_id=org_id,
                order=order,
                from_status=prev_status,
                to_status=order.status,
                actor_id=actor_id,
            )
        except Exception:
            current_app.logger.warning("Failed to emit order status transition notification")

        db.session.commit()

        if wants_json:
            return jsonify({"success": True, "status": order.status, "message": f"Status updated to {order.status}."})
        flash(f"Order status updated to {order.status}.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Status transition failed: {str(e)}", "order_error")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error in status transition")
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Unexpected error: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))


@orders_bp.route("/<int:order_id>/update-box", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def update_order_box(order_id: int):
    """
    Update information from individual boxes on the Order 360° Profile page.
    Supports: identity, customer, next_action, requirement, financials, vendor_progress, transport.
    """
    org_id = _get_org_id()
    actor_id = _get_actor_id()
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"
    data = request.get_json(silent=True) or request.form

    order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id, is_deleted=False).first()
    if not order:
        if wants_json:
            return jsonify({"success": False, "error": f"Order #{order_id} not found."}), 404
        flash("Order not found.", "order_error")
        return redirect(url_for("orders.list_orders"))

    box_name = data.get("box_name", "").strip()
    section_label = "Order details"

    try:
        if box_name == "identity":
            section_label = "Order Header Identity"
            if "priority" in data and data["priority"]:
                order.priority = str(data["priority"]).strip().upper()
            if "shipping_address" in data:
                order.shipping_address = data["shipping_address"].strip()
            if "billing_address" in data:
                order.billing_address = data["billing_address"].strip()
            if "site_address" in data:
                order.site_address = data["site_address"].strip()
            if "notes" in data:
                order.notes = data["notes"].strip()
            if "expected_delivery_date" in data and data["expected_delivery_date"]:
                try:
                    order.expected_delivery_date = datetime.strptime(data["expected_delivery_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass
            if "net_order_amount" in data and data["net_order_amount"]:
                try:
                    val = Decimal(str(data["net_order_amount"]).replace(",", "").strip())
                    order.net_order_amount = val
                    order.total_ordered_amount = val
                except Exception:
                    pass
            if "assigned_to_id" in data and data["assigned_to_id"]:
                try:
                    order.assigned_to_id = int(data["assigned_to_id"])
                except (ValueError, TypeError):
                    pass

        elif box_name == "customer":
            section_label = "Customer & Lead Details"
            c_name = data.get("customer_name", "").strip()
            c_phone = data.get("customer_phone", "").strip()
            c_email = data.get("customer_email", "").strip()
            c_company = data.get("customer_company", "").strip()
            c_address = data.get("customer_address", "").strip()

            if order.customer:
                if c_name: order.customer.name = c_name
                if c_phone: order.customer.phone_number = c_phone
                if c_email: order.customer.email = c_email
                if c_company: order.customer.company = c_company
                if c_address: order.customer.address = c_address
            if order.lead:
                if c_name: order.lead.name = c_name
                if c_phone: order.lead.phone_number = c_phone
                if c_email: order.lead.email = c_email
                if c_company: order.lead.company = c_company
                if c_address: order.lead.address = c_address
                if "lead_source" in data and data["lead_source"]:
                    order.lead.source = data["lead_source"].strip()

            if "assigned_to_id" in data and data["assigned_to_id"]:
                try:
                    order.assigned_to_id = int(data["assigned_to_id"])
                except (ValueError, TypeError):
                    pass

        elif box_name == "next_action":
            section_label = "Next Action & Follow-up"
            if "next_action_notes" in data and data["next_action_notes"]:
                note_text = data["next_action_notes"].strip()
                if order.notes:
                    order.notes = f"{order.notes}\n[Follow-up]: {note_text}"
                else:
                    order.notes = f"[Follow-up]: {note_text}"
            if "expected_delivery_date" in data and data["expected_delivery_date"]:
                try:
                    order.expected_delivery_date = datetime.strptime(data["expected_delivery_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass
            to_status = data.get("to_status", "").strip()
            if to_status and to_status != order.status:
                remarks = data.get("status_remarks", f"Updated next action to {to_status}")
                from services.oms.status_engine import OMSStatusEngine
                OMSStatusEngine.transition_master_status(
                    org_id=org_id,
                    order_id=order.id,
                    to_status=to_status,
                    remarks=remarks,
                    actor_id=actor_id,
                )

        elif box_name == "requirement":
            section_label = "Requirement & Quotation"
            glass_cat = data.get("glass_category", "").strip()
            est_area = data.get("est_area", "").strip()
            if (glass_cat or est_area) and order.items:
                first_item = next((it for it in order.items if not it.is_deleted), None)
                if first_item:
                    if glass_cat:
                        first_item.item_description = glass_cat
                    if est_area:
                        try:
                            first_item.quantity = Decimal(est_area.replace(",", ""))
                        except Exception:
                            pass
            if order.quotation:
                if "customer_decision" in data and data["customer_decision"]:
                    order.quotation.customer_decision = data["customer_decision"].strip()
                if "quotation_value" in data and data["quotation_value"]:
                    try:
                        val = float(str(data["quotation_value"]).replace(",", "").strip())
                        order.quotation.subtotal = val
                        order.net_order_amount = Decimal(str(val))
                        order.total_ordered_amount = Decimal(str(val))
                    except Exception:
                        pass
                if "quotation_status" in data and data["quotation_status"]:
                    order.quotation.status = data["quotation_status"].strip()

            if "requirement_notes" in data and data["requirement_notes"]:
                req_notes = data["requirement_notes"].strip()
                order.notes = f"{order.notes}\n[Requirement]: {req_notes}" if order.notes else req_notes

        elif box_name == "financials":
            section_label = "Order & Financials"
            if "customer_po_number" in data:
                order.customer_po_number = data["customer_po_number"].strip() or None
            if "customer_po_date" in data and data["customer_po_date"]:
                try:
                    order.customer_po_date = datetime.strptime(data["customer_po_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass
            if "expected_delivery_date" in data and data["expected_delivery_date"]:
                try:
                    order.expected_delivery_date = datetime.strptime(data["expected_delivery_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass
            if "net_order_amount" in data and data["net_order_amount"]:
                try:
                    val = Decimal(str(data["net_order_amount"]).replace(",", "").strip())
                    order.net_order_amount = val
                    order.total_ordered_amount = val
                except Exception:
                    pass
            if "customer_payment_status" in data and data["customer_payment_status"]:
                order.customer_payment_status = data["customer_payment_status"].strip()
            if "assigned_to_id" in data and data["assigned_to_id"]:
                try:
                    order.assigned_to_id = int(data["assigned_to_id"])
                except (ValueError, TypeError):
                    pass

        elif box_name == "vendor_progress":
            section_label = "Vendor & Site Progress"
            if "procurement_status" in data and data["procurement_status"]:
                order.procurement_status = data["procurement_status"].strip()
            if "delivery_status" in data and data["delivery_status"]:
                order.delivery_status = data["delivery_status"].strip()
            if "fitting_status" in data and data["fitting_status"]:
                order.fitting_status = data["fitting_status"].strip()
            if "milestones_notes" in data and data["milestones_notes"]:
                m_notes = data["milestones_notes"].strip()
                order.notes = f"{order.notes}\n[Milestone]: {m_notes}" if order.notes else m_notes
            if "vendor_ready_date" in data and data["vendor_ready_date"]:
                try:
                    order.expected_delivery_date = datetime.strptime(data["vendor_ready_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass

            from services.oms.status_engine import OMSStatusEngine
            OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor_id)

        elif box_name == "transport":
            section_label = "Transport & Delivery"
            cons = next((c for c in order.consignments if not c.is_deleted), None) if order.consignments else None
            if not cons:
                now_str = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                cons = OrderConsignment(
                    order_id=order.id,
                    organization_id=org_id,
                    consignment_number=f"GLS-TRP-{now_str}",
                    status=OrderConsignmentStatus.DISPATCHED.value,
                )
                db.session.add(cons)
                db.session.flush()

            if "transporter_name" in data:
                cons.transporter_name = data["transporter_name"].strip()
            if "transit_status" in data and data["transit_status"]:
                cons.status = data["transit_status"].strip()
            if "vehicle_number" in data:
                cons.vehicle_number = data["vehicle_number"].strip()
            if "driver_name" in data:
                cons.driver_name = data["driver_name"].strip()
            if "driver_contact" in data:
                cons.driver_contact = data["driver_contact"].strip()
            if "pickup_location" in data:
                cons.pickup_location = data["pickup_location"].strip()
            if "shipping_cost" in data and data["shipping_cost"]:
                try:
                    cons.shipping_cost = Decimal(str(data["shipping_cost"]).replace(",", "").strip())
                except Exception:
                    pass
            if "dispatch_date" in data and data["dispatch_date"]:
                try:
                    cons.dispatch_date = datetime.strptime(data["dispatch_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass
            if "expected_arrival_date" in data and data["expected_arrival_date"]:
                try:
                    cons.expected_arrival_date = datetime.strptime(data["expected_arrival_date"].strip(), "%Y-%m-%d")
                except ValueError:
                    pass
            if "site_receiver_name" in data:
                cons.site_receiver_name = data["site_receiver_name"].strip()
            if "site_receiver_contact" in data:
                cons.site_receiver_contact = data["site_receiver_contact"].strip()
            if "packing_verified" in data:
                cons.packing_verified = data["packing_verified"] in ("1", "true", "True", True, "on")
            if "pod_signed" in data:
                is_signed = data["pod_signed"] in ("1", "true", "True", True, "on")
                cons.pod_signed = is_signed
                if is_signed and not cons.pod_signed_at:
                    cons.pod_signed_at = datetime.utcnow()
            if "logistics_notes" in data and data["logistics_notes"]:
                cons.notes = data["logistics_notes"].strip()

            from services.oms.status_engine import OMSStatusEngine
            OMSStatusEngine.recalculate_delivery_status(order, actor_id=actor_id)
            OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor_id)

        else:
            raise OMSError(f"Unknown box section: '{box_name}'")

        db.session.commit()
        if wants_json:
            return jsonify({"success": True, "message": f"{section_label} updated successfully."})
        flash(f"{section_label} updated successfully.", "order_success")

    except OMSError as e:
        db.session.rollback()
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Update failed: {str(e)}", "order_error")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception(f"Error updating box '{box_name}' on order {order_id}")
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"An unexpected error occurred: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))


@orders_bp.route("/<int:order_id>/documents/<int:doc_id>/preview", methods=["GET"])
@login_required
def preview_order_document(order_id: int, doc_id: int):
    """In-browser preview for PDF or image order documents."""
    org_id = _get_org_id()
    try:
        doc, file_path = OMSDocumentService.get_document_for_download(
            org_id=org_id,
            order_id=order_id,
            doc_id=doc_id,
        )
        return send_file(
            file_path,
            as_attachment=False,
            mimetype=doc.mime_type or "application/octet-stream",
        )
    except OMSError as e:
        flash(str(e), "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def delete_order_document(order_id: int, doc_id: int):
    """Soft delete an order document."""
    org_id = _get_org_id()
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"
    try:
        OMSDocumentService.soft_delete_document(
            org_id=org_id,
            order_id=order_id,
            doc_id=doc_id,
        )
        linked_delvs = OrderDelivery.query.filter_by(proof_of_delivery_document_id=doc_id, organization_id=org_id).all()
        for ld in linked_delvs:
            ld.is_deleted = True
        db.session.commit()
        if wants_json:
            return jsonify({"success": True, "message": "Document deleted."})
        flash("Document deleted successfully.", "order_success")
    except OMSError as e:
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Deletion failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/dashboard", methods=["GET"])
@login_required
def orders_dashboard():
    """Executive OMS Dashboard with bottleneck analytics and margins."""
    org_id = _get_org_id()
    from_date_str = request.args.get("from_date")
    to_date_str = request.args.get("to_date")
    customer_id = request.args.get("customer_id", type=int)
    status = request.args.get("status")

    from_date = None
    to_date = None
    if from_date_str:
        try:
            from_date = datetime.strptime(from_date_str, "%Y-%m-%d")
        except ValueError:
            pass
    if to_date_str:
        try:
            to_date = datetime.strptime(to_date_str, "%Y-%m-%d")
        except ValueError:
            pass

    metrics = OMSReportingService.get_dashboard_metrics(
        org_id=org_id,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
        status=status,
    )

    overdue_orders = OMSReportingService.get_overdue_orders_list(org_id)

    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({
            "metrics": metrics,
            "overdue_orders": overdue_orders,
        })

    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Customer.name.asc()).all()
    return render_template(
        "orders/dashboard.html",
        metrics=metrics,
        overdue_orders=overdue_orders,
        customers=customers,
        current_status=status,
        from_date=from_date_str,
        to_date=to_date_str,
    )



@orders_bp.route("/overdue", methods=["GET"])
@login_required
def overdue_orders():
    """List overdue orders and optionally trigger notification alerts."""
    org_id = _get_org_id()
    trigger_alerts = request.args.get("alert", "false").lower() == "true"

    if trigger_alerts:
        notified = OMSNotificationService.check_and_notify_overdue_orders(org_id)
        flash(f"Overdue scan completed: {notified} alerts dispatched.", "order_info")

    overdue_list = OMSReportingService.get_overdue_orders_list(org_id)
    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"overdue_orders": overdue_list})

    return redirect(url_for("orders.orders_dashboard"))



@orders_bp.route("/export/csv", methods=["GET"])
@login_required
def export_orders_csv():
    """Export filtered orders to Excel-compatible UTF-8 CSV."""
    org_id = _get_org_id()
    search = request.args.get("q")
    status = request.args.get("status")
    from_date_str = request.args.get("from_date")
    to_date_str = request.args.get("to_date")
    customer_id = request.args.get("customer_id", type=int)

    from_date = datetime.strptime(from_date_str, "%Y-%m-%d") if from_date_str else None
    to_date = datetime.strptime(to_date_str, "%Y-%m-%d") if to_date_str else None

    csv_bytes = OMSExportService.export_csv(
        org_id=org_id,
        search=search,
        status=status,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
    )

    filename = f"orders_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    return send_file(
        io.BytesIO(csv_bytes),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )



@orders_bp.route("/export/excel", methods=["GET"])
@login_required
def export_orders_excel():
    """Export filtered orders to styled Excel workbook (.xlsx)."""
    org_id = _get_org_id()
    search = request.args.get("q")
    status = request.args.get("status")
    from_date_str = request.args.get("from_date")
    to_date_str = request.args.get("to_date")
    customer_id = request.args.get("customer_id", type=int)

    from_date = datetime.strptime(from_date_str, "%Y-%m-%d") if from_date_str else None
    to_date = datetime.strptime(to_date_str, "%Y-%m-%d") if to_date_str else None

    excel_bytes = OMSExportService.export_excel(
        org_id=org_id,
        search=search,
        status=status,
        from_date=from_date,
        to_date=to_date,
        customer_id=customer_id,
    )

    filename = f"orders_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        io.BytesIO(excel_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )



@orders_bp.route("/<int:order_id>/pdf", methods=["GET"])
@login_required
def export_order_pdf(order_id: int):
    """Generate and download executive order summary PDF."""
    org_id = _get_org_id()
    try:
        pdf_bytes = OMSExportService.generate_order_summary_pdf(
            org_id=org_id,
            order_id=order_id,
        )
        filename = f"Order_Summary_{order_id}_{datetime.utcnow().strftime('%Y%m%d')}.pdf"
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        current_app.logger.error(f"PDF generation failed for order {order_id}: {e}", exc_info=True)
        flash(f"PDF generation failed: {str(e)}", "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/items/<int:item_id>/allocate", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def allocate_vendors(order_id: int, item_id: int):
    """Allocate one or multiple vendors to a specific order line item."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    if request.is_json:
        allocations_data = request.get_json().get("allocations", [])
    else:
        vendor_id = request.form.get("vendor_id", type=int)
        alloc_qty = request.form.get("allocated_quantity", 0)
        rate = request.form.get("purchase_rate", 0)
        exp_date_str = request.form.get("expected_supply_date")
        exp_date = datetime.strptime(exp_date_str, "%Y-%m-%d") if exp_date_str else None
        allocations_data = [{
            "vendor_id": vendor_id,
            "allocated_quantity": alloc_qty,
            "purchase_rate": rate,
            "expected_supply_date": exp_date,
            "notes": request.form.get("notes")
        }]

    try:
        created_allocations = OrderMasterService.allocate_item_vendors(
            org_id=org_id,
            order_id=order_id,
            order_item_id=item_id,
            allocations_data=allocations_data,
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "count": len(created_allocations), "message": "Allocations saved successfully."})
        flash("Vendor allocation recorded successfully.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Allocation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/procurements", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def create_procurement(order_id: int):
    """Issue a Purchase Order to a vendor from allocated items."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    if request.is_json:
        data = request.get_json() or {}
    else:
        data = {
            "vendor_id": request.form.get("vendor_id", type=int),
            "expected_delivery_date": request.form.get("expected_delivery_date"),
            "notes": request.form.get("notes"),
            "items": []
        }
        # Form checkboxes or multi-row items
        alloc_ids = request.form.getlist("allocation_id")
        for a_id in alloc_ids:
            qty = request.form.get(f"proc_qty_{a_id}")
            rate = request.form.get(f"proc_rate_{a_id}")
            data["items"].append({
                "allocation_id": int(a_id),
                "quantity": qty,
                "unit_rate": rate
            })

    try:
        exp_date = None
        exp_val = data.get("expected_delivery_date")
        if isinstance(exp_val, str) and exp_val.strip():
            exp_date = datetime.strptime(exp_val.strip(), "%Y-%m-%d")
        elif isinstance(exp_val, datetime):
            exp_date = exp_val

        po = ProcurementService.create_purchase_order(
            org_id=org_id,
            order_id=order_id,
            vendor_id=data.get("vendor_id"),
            items_data=data.get("items", []),
            expected_delivery_date=exp_date,
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        try:
            order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
            if order:
                OMSNotificationService.notify_procurement_po_issued(
                    org_id=org_id,
                    order=order,
                    po_number=po.po_number,
                    vendor_name=po.vendor.company_name if po.vendor else "Vendor",
                    amount=str(po.net_procurement_amount),
                    actor_id=actor_id,
                )
        except Exception:
            current_app.logger.warning("Failed to emit PO notification")
        db.session.commit()

        if request.is_json:
            return jsonify({"success": True, "po_id": po.id, "po_number": po.po_number, "message": f"PO {po.po_number} issued."})
        flash(f"Purchase Order {po.po_number} created successfully.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"PO creation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/consignments", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def record_consignment(order_id: int):
    """Record transporter consignment for an order's Purchase Order."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    vendor_id = data.get("vendor_id", type=int) if not request.is_json else data.get("vendor_id")
    procurement_id = data.get("procurement_id", type=int) if not request.is_json else data.get("procurement_id")

    notes = data.get("notes") or ""
    if vendor_id:
        v = Vendor.query.filter_by(id=vendor_id, organization_id=org_id).first()
        if v:
            vendor_tag = f"[Vendor: {v.company_name or v.name}]"
            notes = f"{notes} {vendor_tag}".strip()

    items_data = data.get("items", []) if request.is_json else []
    if not items_data and not request.is_json:
        # Auto-include line items from form fields
        for k in request.form.keys():
            if k.startswith("cons_qty_"):
                proc_item_id = int(k.replace("cons_qty_", ""))
                qty = request.form.get(k)
                if qty and Decimal(str(qty)) > 0:
                    items_data.append({"procurement_item_id": proc_item_id, "shipped_quantity": qty})

    try:
        cons = ProcurementService.record_consignment(
            org_id=org_id,
            order_id=order_id,
            procurement_id=procurement_id,
            items_data=items_data,
            transporter_name=data.get("transporter_name"),
            tracking_number=data.get("tracking_number"),
            vehicle_number=data.get("vehicle_number"),
            driver_contact=data.get("driver_contact"),
            lr_number=data.get("lr_number"),
            eway_bill_number=data.get("eway_bill_number"),
            notes=notes or None,
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "consignment_number": cons.consignment_number, "message": f"Consignment {cons.consignment_number} recorded."})
        flash(f"Consignment {cons.consignment_number} recorded.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Consignment recording failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/consignments/<int:cons_id>/qc", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def record_consignment_qc(order_id: int, cons_id: int):
    """Record arrival inspection & QC for a consignment."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    items_qc = data.get("items_qc", []) if request.is_json else []
    if not items_qc and not request.is_json:
        for k in request.form.keys():
            if k.startswith("qc_accepted_"):
                c_item_id = int(k.replace("qc_accepted_", ""))
                acc = request.form.get(k, 0)
                dam = request.form.get(f"qc_damaged_{c_item_id}", 0)
                rej = request.form.get(f"qc_rejected_{c_item_id}", 0)
                rem = request.form.get(f"qc_remarks_{c_item_id}")
                items_qc.append({
                    "consignment_item_id": c_item_id,
                    "accepted_quantity": acc,
                    "damaged_quantity": dam,
                    "rejected_quantity": rej,
                    "qc_remarks": rem,
                })

    try:
        cons = ProcurementService.record_consignment_qc_arrival(
            org_id=org_id,
            consignment_id=cons_id,
            items_qc=items_qc,
            received_by_id=actor_id,
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        try:
            order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
            if order:
                tot_acc = sum(float(it.get("accepted_quantity", 0)) for it in items_qc)
                tot_rej = sum(float(it.get("rejected_quantity", 0)) for it in items_qc)
                OMSNotificationService.notify_consignment_arrival(
                    org_id=org_id,
                    order=order,
                    consignment_number=cons.consignment_number,
                    accepted_qty=tot_acc,
                    rejected_qty=tot_rej,
                    actor_id=actor_id,
                )
        except Exception:
            current_app.logger.warning("Failed to emit consignment QC notification")
        db.session.commit()

        if request.is_json:
            return jsonify({"success": True, "status": cons.status, "message": f"QC recorded for {cons.consignment_number}."})
        flash(f"QC recorded for {cons.consignment_number}.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"QC failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/deliveries", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def create_delivery(order_id: int):
    """Create a customer delivery challan."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    items_data = data.get("items", []) if request.is_json else []
    if not items_data and not request.is_json:
        for k in request.form.keys():
            if k.startswith("delv_qty_"):
                o_item_id = int(k.replace("delv_qty_", ""))
                qty = request.form.get(k)
                if qty and Decimal(str(qty)) > 0:
                    items_data.append({"order_item_id": o_item_id, "delivered_quantity": qty})

    try:
        delv = DeliveryService.create_delivery_challan(
            org_id=org_id,
            order_id=order_id,
            items_data=items_data,
            recipient_name=data.get("recipient_name"),
            recipient_phone=data.get("recipient_phone"),
            delivery_address=data.get("delivery_address"),
            challan_number=data.get("challan_number"),
            delivered_by_id=data.get("delivered_by_id", type=int) if not request.is_json else data.get("delivered_by_id"),
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "delivery_number": delv.delivery_number, "message": f"Delivery challan {delv.delivery_number} created."})
        flash(f"Delivery challan {delv.delivery_number} created.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Delivery challan creation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/deliveries/<int:delv_id>/confirm", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def confirm_delivery(order_id: int, delv_id: int):
    """Confirm site receipt of a delivery challan."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    items_conf = data.get("items_confirmation") if request.is_json else None

    try:
        delv = DeliveryService.confirm_delivery(
            org_id=org_id,
            delivery_id=delv_id,
            items_confirmation=items_conf,
            proof_doc_id=data.get("proof_doc_id"),
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        try:
            order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
            if order:
                OMSNotificationService.notify_delivery_dispatched(
                    org_id=org_id,
                    order=order,
                    delivery_number=delv.delivery_number,
                    actor_id=actor_id,
                )
        except Exception:
            current_app.logger.warning("Failed to emit delivery notification")
        db.session.commit()

        if request.is_json:
            return jsonify({"success": True, "message": f"Delivery {delv.delivery_number} confirmed."})
        flash(f"Delivery {delv.delivery_number} confirmed.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Delivery confirmation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/upload-delivery-challan", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def upload_delivery_challan(order_id: int):
    """Upload physical/signed customer delivery challan document."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"

    file = request.files.get("file")
    if not file or not file.filename:
        if wants_json:
            return jsonify({"success": False, "error": "No delivery challan file selected"}), 400
        flash("Please select a delivery challan document to upload.", "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))

    try:
        order = OrderMasterService.lock_order(org_id, order_id)
        challan_number = request.form.get("challan_number", "").strip() or None
        delivery_date_raw = request.form.get("delivery_date", "").strip()
        delivery_date = datetime.strptime(delivery_date_raw, "%Y-%m-%d") if delivery_date_raw else datetime.utcnow()
        recipient_name = request.form.get("recipient_name", "").strip() or (order.customer.name if order.customer else "Site Recipient")
        recipient_phone = request.form.get("recipient_phone", "").strip() or None
        delivery_address = request.form.get("delivery_address", "").strip() or order.shipping_address or order.site_address
        status_val = request.form.get("status", "").strip() or OrderDeliveryStatus.DELIVERED.value
        notes = request.form.get("notes", "").strip() or None

        doc_title = f"Delivery Challan - {challan_number}" if challan_number else f"Delivery Challan — {file.filename}"
        saved_doc = OMSDocumentService.save_order_document(
            org_id=org_id,
            order_id=order_id,
            file=file,
            document_type=OrderDocumentType.DELIVERY_CHALLAN.value,
            title=doc_title,
            uploaded_by_id=actor_id,
        )

        delv_number = DeliveryService.generate_delivery_number(org_id)
        delv = OrderDelivery(
            delivery_number=delv_number,
            order_id=order_id,
            organization_id=org_id,
            delivery_date=delivery_date,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
            delivery_address=delivery_address,
            challan_number=challan_number or delv_number,
            status=status_val,
            delivered_by_id=actor_id,
            proof_of_delivery_document_id=saved_doc.id,
            notes=notes,
        )
        db.session.add(delv)

        if status_val == OrderDeliveryStatus.DELIVERED.value:
            for it in order.items:
                if not it.is_deleted:
                    it.delivered_quantity = it.quantity

        from services.oms.status_engine import OMSStatusEngine
        OMSStatusEngine.recalculate_delivery_status(order, actor_id=actor_id)
        OMSStatusEngine.synchronize_order_master_status(order, actor_id=actor_id)

        try:
            OMSNotificationService.notify_delivery_dispatched(
                org_id=org_id,
                order=order,
                delivery_number=delv.delivery_number,
                actor_id=actor_id,
            )
        except Exception:
            current_app.logger.warning("Failed to emit delivery dispatch notification")

        db.session.commit()

        if wants_json:
            return jsonify({
                "success": True,
                "message": f"Delivery challan {delv.challan_number} uploaded successfully.",
                "delivery_id": delv.id,
                "document_id": saved_doc.id,
            })
        flash(f"Delivery challan {delv.challan_number} uploaded successfully.", "order_success")

    except OMSError as e:
        db.session.rollback()
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Failed to upload delivery challan: {str(e)}", "order_error")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Error uploading delivery challan")
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"An unexpected error occurred: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/deliveries/<int:delv_id>/delete", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def delete_delivery(order_id: int, delv_id: int):
    """Delete / soft-delete a delivery challan."""
    org_id = _get_org_id()
    doc_id = request.form.get("doc_id", type=int)
    if delv_id and delv_id > 0:
        delv = OrderDelivery.query.filter_by(id=delv_id, order_id=order_id, organization_id=org_id, is_deleted=False).first()
        if delv:
            delv.is_deleted = True
            if delv.proof_of_delivery_document_id:
                doc = OrderDocument.query.filter_by(id=delv.proof_of_delivery_document_id, organization_id=org_id).first()
                if doc:
                    doc.is_deleted = True
    elif doc_id:
        doc = OrderDocument.query.filter_by(id=doc_id, order_id=order_id, organization_id=org_id).first()
        if doc:
            doc.is_deleted = True

    db.session.commit()
    flash("Delivery challan removed successfully.", "order_success")
    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/fitting-visits", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def schedule_fitting_visit(order_id: int):
    """Schedule a site installation/fitting visit."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    date_val = data.get("scheduled_date")
    if isinstance(date_val, str) and date_val.strip():
        sched_date = datetime.strptime(date_val.strip(), "%Y-%m-%d")
    elif isinstance(date_val, datetime):
        sched_date = date_val
    else:
        sched_date = datetime.utcnow()

    try:
        visit = DeliveryService.schedule_fitting_visit(
            org_id=org_id,
            order_id=order_id,
            scheduled_date=sched_date,
            technician_id=data.get("technician_id", type=int) if not request.is_json else data.get("technician_id"),
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "visit_number": visit.visit_number, "message": f"Fitting visit {visit.visit_number} scheduled."})
        flash(f"Fitting visit {visit.visit_number} scheduled.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Scheduling visit failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/fitting-visits/<int:visit_id>/complete", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def complete_fitting_visit(order_id: int, visit_id: int):
    """Complete a site installation visit and record client signoff."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    signoff = True if data.get("client_signoff") in (True, "true", "on", "1") else False

    try:
        visit = DeliveryService.complete_fitting_visit(
            org_id=org_id,
            visit_id=visit_id,
            client_signoff=signoff,
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        try:
            order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
            if order:
                OMSNotificationService.notify_fitting_completed(
                    org_id=org_id,
                    order=order,
                    visit_number=visit.visit_number,
                    actor_id=actor_id,
                )
        except Exception:
            current_app.logger.warning("Failed to emit fitting completion notification")
        db.session.commit()

        if request.is_json:
            return jsonify({"success": True, "status": visit.status, "message": f"Visit {visit.visit_number} completed."})
        flash(f"Fitting visit {visit.visit_number} completed.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Completing visit failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/payments/allocate", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def allocate_payment(order_id: int):
    """Allocate customer payment to order ledger."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    payment_id = data.get("payment_id", type=int) if not request.is_json else data.get("payment_id")
    amount = data.get("allocated_amount")
    alloc_type = data.get("allocation_type", OrderPaymentAllocationType.DIRECT_PAYMENT.value)

    try:
        alloc = FinancialReconciliationService.allocate_customer_payment(
            org_id=org_id,
            order_id=order_id,
            payment_id=payment_id,
            allocated_amount=amount,
            allocation_type=alloc_type,
            invoice_id=data.get("invoice_id", type=int) if not request.is_json else data.get("invoice_id"),
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "allocation_id": alloc.id, "message": "Payment allocated successfully."})
        flash("Payment allocated successfully.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Payment allocation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/vendor-bills", methods=["POST"])
@orders_bp.route("/<int:order_id>/vendor-bills", methods=["POST"], endpoint="record_vendor_bill")
@login_required
@require_roles("admin", "manager", "employee")
def create_vendor_bill(order_id: int):
    """Create 3-way matched vendor bill."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    procurement_id = data.get("procurement_id", type=int) if not request.is_json else data.get("procurement_id")
    bill_number = data.get("bill_number", "").strip()
    bill_date_val = data.get("bill_date")
    if isinstance(bill_date_val, str) and bill_date_val.strip():
        bill_date = datetime.strptime(bill_date_val.strip(), "%Y-%m-%d")
    elif isinstance(bill_date_val, datetime):
        bill_date = bill_date_val
    else:
        bill_date = datetime.utcnow()

    items_data = data.get("items", []) if request.is_json else []
    if not items_data and not request.is_json:
        for k in request.form.keys():
            if k.startswith("bill_qty_"):
                po_item_id = int(k.replace("bill_qty_", ""))
                qty = request.form.get(k)
                rate = request.form.get(f"bill_rate_{po_item_id}")
                tax = request.form.get(f"bill_tax_{po_item_id}", 0)
                if qty and Decimal(str(qty)) > 0:
                    items_data.append({
                        "procurement_item_id": po_item_id,
                        "billed_quantity": qty,
                        "unit_rate": rate,
                        "tax_amount": tax,
                    })

    try:
        bill = FinancialReconciliationService.create_vendor_bill(
            org_id=org_id,
            order_id=order_id,
            procurement_id=procurement_id,
            bill_number=bill_number,
            bill_date=bill_date,
            items_data=items_data,
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "bill_id": bill.id, "bill_number": bill.bill_number, "message": f"Bill {bill.bill_number} created."})
        flash(f"Vendor bill {bill.bill_number} created successfully.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Vendor bill creation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/vendor-payments", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def record_vendor_payment(order_id: int):
    """Record payment payout against a vendor bill."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    bill_id = data.get("bill_id", type=int) if not request.is_json else data.get("bill_id")
    amount = data.get("amount")
    mode = data.get("payment_mode", "NEFT/RTGS")

    try:
        vpay = FinancialReconciliationService.record_vendor_payment(
            org_id=org_id,
            bill_id=bill_id,
            amount=amount,
            payment_mode=mode,
            payment_reference=data.get("payment_reference"),
            transaction_reference=data.get("transaction_reference"),
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "payment_reference": vpay.payment_reference, "message": f"Vendor payment {vpay.payment_reference} recorded."})
        flash(f"Vendor payment {vpay.payment_reference} recorded.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Vendor payment failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/financials", methods=["GET"])
@login_required
@require_roles("admin", "manager", "employee")
def order_financials(order_id: int):
    """Get full financial reconciliation metrics and profit margins."""
    org_id = _get_org_id()
    try:
        data = FinancialReconciliationService.reconcile_order_financials(org_id, order_id)
        return jsonify({"success": True, "financials": data})
    except OMSError as e:
        return jsonify({"success": False, "error": str(e)}), 400



@orders_bp.route("/<int:order_id>/close", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def exceptional_close(order_id: int):
    """Exceptional closure (force cancel / terminate) of an order."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    closure_type = data.get("closure_type", OrderClosureType.CANCELLED.value)
    reason = data.get("closure_reason", "").strip()
    impact = data.get("financial_impact_notes")

    try:
        closure = OrderMasterService.exceptional_close_order(
            org_id=org_id,
            order_id=order_id,
            closure_type=closure_type,
            closure_reason=reason,
            financial_impact_notes=impact,
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "closure_type": closure.closure_type, "message": f"Order closed as {closure_type}."})
        flash(f"Order closed as {closure_type}.", "order_warning")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Closure failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/revisions", methods=["POST"])
@login_required
@require_roles("admin", "manager")
def create_revision(order_id: int):
    """Create an immutable snapshot revision of the order."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    data = request.get_json() if request.is_json else request.form
    reason = data.get("change_reason", "").strip()

    try:
        rev = OrderMasterService.create_order_revision(
            org_id=org_id,
            order_id=order_id,
            change_reason=reason,
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "revision_number": rev.revision_number, "message": f"Revision {rev.revision_number} generated."})
        flash(f"Revision {rev.revision_number} generated.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if request.is_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Revision creation failed: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/documents/upload", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def upload_order_document(order_id: int):
    """Securely upload one or more order documents with tenant isolation and quota checks."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    uploaded_files = (
        request.files.getlist("files")
        or request.files.getlist("files[]")
        or request.files.getlist("file")
    )
    valid_files = [f for f in uploaded_files if f and getattr(f, "filename", None) and f.filename.strip()]
    doc_type = request.form.get("document_type", OrderDocumentType.OTHER.value)
    title = (request.form.get("title") or "").strip()
    is_cust_visible = request.form.get("is_customer_visible") in ("true", "1", "True", True)
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"

    if not valid_files:
        msg = "Please select at least one document to upload."
        if wants_json:
            return jsonify({"success": False, "error": msg}), 400
        flash(msg, "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))

    saved_docs = []
    errors = []

    for f in valid_files:
        if len(valid_files) == 1:
            doc_title = title if title else f.filename
        else:
            doc_title = f"{title} - {f.filename}" if title else f.filename

        try:
            doc = OMSDocumentService.save_order_document(
                org_id=org_id,
                order_id=order_id,
                file=f,
                document_type=doc_type,
                title=doc_title,
                is_customer_visible=is_cust_visible,
                uploaded_by_id=actor_id,
            )
            saved_docs.append(doc)
        except OMSError as e:
            errors.append(f"{f.filename}: {str(e)}")
        except Exception as e:
            current_app.logger.exception(f"Unexpected error uploading document {f.filename}")
            errors.append(f"{f.filename}: {str(e)}")

    if not saved_docs and errors:
        err_msg = "Upload failed: " + "; ".join(errors)
        if wants_json:
            return jsonify({"success": False, "error": err_msg}), 400
        flash(err_msg, "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))

    success_msg = f"{len(saved_docs)} document(s) uploaded successfully."
    if errors:
        success_msg += f" Note: {len(errors)} file(s) failed: " + "; ".join(errors)

    if wants_json:
        resp_payload = {
            "success": True,
            "uploaded_count": len(saved_docs),
            "documents": [
                {
                    "document_id": d.id,
                    "title": d.title,
                    "document_type": d.document_type,
                    "file_size": d.file_size,
                }
                for d in saved_docs
            ],
            "message": success_msg,
        }
        if len(saved_docs) == 1:
            resp_payload["document_id"] = saved_docs[0].id
        return jsonify(resp_payload), 201

    flash(success_msg, "order_success" if not errors else "order_warning")
    return redirect(url_for("orders.detail_order", order_id=order_id))



@orders_bp.route("/<int:order_id>/documents/<int:doc_id>/download", methods=["GET"])
@login_required
def download_order_document(order_id: int, doc_id: int):
    """Download an order document with strict tenant verification."""
    org_id = _get_org_id()
    try:
        doc, file_path = OMSDocumentService.get_document_for_download(
            org_id=org_id,
            order_id=order_id,
            doc_id=doc_id,
        )
        download_name = doc.title
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        if ext and not download_name.lower().endswith(f".{ext}"):
            download_name = f"{download_name}.{ext}"

        return send_file(
            file_path,
            as_attachment=True,
            download_name=download_name,
            mimetype=doc.mime_type or "application/octet-stream",
        )
    except OMSError as e:
        flash(str(e), "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))


@orders_bp.route("/<int:order_id>/vendor-proforma-invoice", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def upload_vendor_proforma_invoice(order_id: int):
    """Upload a Vendor Proforma Invoice in the Order Cockpit Procurement tab."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"

    order = OrderMaster.query.filter_by(id=order_id, organization_id=org_id).first()
    if not order:
        if wants_json:
            return jsonify({"success": False, "error": "Order not found."}), 404
        flash("Order not found.", "order_error")
        return redirect(url_for("orders.list_orders"))

    vendor_id = request.form.get("vendor_id", type=int)
    if not vendor_id:
        if wants_json:
            return jsonify({"success": False, "error": "Please select a supplier / vendor."}), 400
        flash("Please select a vendor.", "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))

    vendor = Vendor.query.filter_by(id=vendor_id, organization_id=org_id, is_deleted=False).first()
    if not vendor:
        if wants_json:
            return jsonify({"success": False, "error": f"Vendor #{vendor_id} not found."}), 404
        flash("Vendor not found.", "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))

    file = request.files.get("file")
    if not file or not file.filename:
        if wants_json:
            return jsonify({"success": False, "error": "Please select a proforma invoice file to upload."}), 400
        flash("Please select a proforma invoice file to upload.", "order_error")
        return redirect(url_for("orders.detail_order", order_id=order_id))

    pi_number = request.form.get("pi_number", "").strip()
    notes = request.form.get("notes", "").strip()
    title_suffix = f" - {pi_number}" if pi_number else f" - {file.filename}"
    doc_title = f"Vendor PI: {vendor.company_name}{title_suffix}"

    try:
        saved_doc = OMSDocumentService.save_order_document(
            org_id=org_id,
            order_id=order.id,
            file=file,
            document_type=OrderDocumentType.VENDOR_PROFORMA_INVOICE.value,
            title=doc_title,
            is_customer_visible=False,
            uploaded_by_id=actor_id,
        )

        # Synchronize with VendorDocument master
        v_doc = VendorDocument(
            vendor_id=vendor.id,
            filename=os.path.basename(saved_doc.file_path),
            original_name=file.filename,
            file_type=file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else None,
            file_size=saved_doc.file_size,
            document_type="Proforma Invoice",
            description=f"Master Order {order.master_order_number} {f'(Ref: {pi_number})' if pi_number else ''} {f'- {notes}' if notes else ''}".strip(),
            uploaded_by=actor_id,
            organization_id=org_id,
        )
        db.session.add(v_doc)
        db.session.commit()

        if wants_json:
            return jsonify({
                "success": True,
                "message": f"Proforma invoice for {vendor.company_name} uploaded successfully.",
                "document_id": saved_doc.id,
                "title": saved_doc.title,
            }), 201

        flash(f"Vendor Proforma Invoice for {vendor.company_name} uploaded successfully.", "order_success")
    except OMSError as e:
        db.session.rollback()
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Upload failed: {str(e)}", "order_error")
    except Exception as e:
        db.session.rollback()
        current_app.logger.exception("Unexpected error uploading vendor proforma invoice")
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"An unexpected error occurred: {str(e)}", "order_error")

    return redirect(url_for("orders.detail_order", order_id=order_id))


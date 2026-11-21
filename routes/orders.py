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
    Vendor,
    Product,
    Project,
    Employee,
    Payment,
    Invoice,
    MasterOrderStatus,
    OrderProcurementStatus,
    OrderDeliveryStatus,
    OrderFittingStatus,
    OrderCustomerPaymentStatus,
    OrderVendorPaymentStatus,
    OrderPaymentAllocationType,
    OrderClosureType,
    OrderDocumentType,
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
    Unified Order Cockpit list view.
    Filters by search query, status, procurement_status, delivery_status, payment_status.
    """
    org_id = _get_org_id()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 20, type=int)
    search_query = request.args.get("q", "").strip()
    status_filter = request.args.get("status")
    procurement_filter = request.args.get("procurement_status")
    delivery_filter = request.args.get("delivery_status")
    payment_filter = request.args.get("customer_payment_status")

    res = OrderMasterService.list_orders(
        org_id=org_id,
        page=page,
        per_page=per_page,
        search_query=search_query if search_query else None,
        status_filter=status_filter if status_filter else None,
        procurement_status_filter=procurement_filter if procurement_filter else None,
        delivery_status_filter=delivery_filter if delivery_filter else None,
        customer_payment_status_filter=payment_filter if payment_filter else None,
    )

    # Aggregated metrics for cockpit header cards
    metrics = {
        "total_orders": OrderMaster.query.filter_by(organization_id=org_id, is_deleted=False).count(),
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
        "total_revenue": float(
            db.session.query(func.coalesce(func.sum(OrderMaster.net_order_amount), Decimal("0.00")))
            .filter(OrderMaster.organization_id == org_id, OrderMaster.is_deleted == False)
            .scalar()
        ),
        "total_collected": float(
            db.session.query(func.coalesce(func.sum(OrderMaster.total_customer_paid), Decimal("0.00")))
            .filter(OrderMaster.organization_id == org_id, OrderMaster.is_deleted == False)
            .scalar()
        ),
    }

    if request.is_json or request.headers.get("Accept") == "application/json":
        serialized_orders = [
            {
                "id": o.id,
                "master_order_number": o.master_order_number,
                "customer_name": o.customer.name if o.customer else None,
                "status": o.status,
                "procurement_status": o.procurement_status,
                "delivery_status": o.delivery_status,
                "fitting_status": o.fitting_status,
                "customer_payment_status": o.customer_payment_status,
                "net_order_amount": float(o.net_order_amount),
                "total_customer_paid": float(o.total_customer_paid),
                "order_date": o.order_date.strftime("%Y-%m-%d") if o.order_date else None,
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
        master_statuses=[s.value for s in MasterOrderStatus],
    )



@orders_bp.route("/new", methods=["GET"])
@login_required
def new_order():
    """Form to create a new Master Order."""
    org_id = _get_org_id()
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Customer.name.asc()).all()
    products = Product.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Product.name.asc()).all()
    projects = Project.query.filter_by(organization_id=org_id).order_by(Project.name.asc()).all()
    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Employee.name.asc()).all()

    return render_template(
        "orders/create.html",
        customers=customers,
        products=products,
        projects=projects,
        employees=employees,
    )



@orders_bp.route("/", methods=["POST"])
@login_required
@require_roles("admin", "manager", "employee")
def create_order():
    """Create a new Master Order with line items."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    if request.is_json:
        data = request.get_json() or {}
    else:
        # Form submission: parse form and dynamic line items
        data = {
            "customer_id": request.form.get("customer_id", type=int),
            "order_title": request.form.get("order_title", "").strip(),
            "project_id": request.form.get("project_id", type=int) or None,
            "quotation_id": request.form.get("quotation_id", type=int) or None,
            "assigned_to_id": request.form.get("assigned_to_id", type=int) or None,
            "shipping_address": request.form.get("shipping_address", "").strip() or None,
            "billing_address": request.form.get("billing_address", "").strip() or None,
            "site_address": request.form.get("site_address", "").strip() or None,
            "notes": request.form.get("notes", "").strip() or None,
            "items": []
        }
        # Parse items from indexed form fields e.g. item_description_0, quantity_0, etc.
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
            if desc:
                data["items"].append({
                    "item_description": desc,
                    "product_id": prod_id,
                    "quantity": qty,
                    "unit_price": price,
                    "tax_rate": tax,
                })

    try:
        order = OrderMasterService.create_order(
            org_id=org_id,
            customer_id=data.get("customer_id"),
            order_title=data.get("order_title"),
            items_data=data.get("items", []),
            project_id=data.get("project_id"),
            quotation_id=data.get("quotation_id"),
            assigned_to_id=data.get("assigned_to_id"),
            shipping_address=data.get("shipping_address"),
            billing_address=data.get("billing_address"),
            site_address=data.get("site_address"),
            notes=data.get("notes"),
            actor_id=actor_id,
        )
        db.session.commit()

        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({
                "success": True,
                "message": f"Order {order.master_order_number} created successfully.",
                "order_id": order.id,
                "master_order_number": order.master_order_number,
            }), 201

        flash(f"Order {order.master_order_number} created successfully.", "order_success")
        return redirect(url_for("orders.detail_order", order_id=order.id))

    except OMSError as e:
        db.session.rollback()
        if request.is_json or request.headers.get("Accept") == "application/json":
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Failed to create order: {str(e)}", "order_error")
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

    return render_template(
        "orders/detail.html",
        cockpit=cockpit_data,
        vendors=vendors,
        employees=employees,
        available_payments=available_payments,
        master_statuses=[s.value for s in MasterOrderStatus],
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
                from_status=None,
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
    procurement_id = data.get("procurement_id", type=int) if not request.is_json else data.get("procurement_id")

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
            notes=data.get("notes"),
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
        order = OrderMasterService.exceptional_close_order(
            org_id=org_id,
            order_id=order_id,
            closure_type=closure_type,
            closure_reason=reason,
            financial_impact_notes=impact,
            actor_id=actor_id,
        )
        db.session.commit()
        if request.is_json:
            return jsonify({"success": True, "status": order.status, "message": f"Order closed as {closure_type}."})
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
    """Securely upload an order document with tenant isolation and quota checks."""
    org_id = _get_org_id()
    actor_id = _get_actor_id() or 1

    file = request.files.get("file")
    doc_type = request.form.get("document_type", OrderDocumentType.OTHER.value)
    title = request.form.get("title")
    is_cust_visible = request.form.get("is_customer_visible") in ("true", "1", "True", True)
    wants_json = request.is_json or request.headers.get("Accept") == "application/json"

    try:
        doc = OMSDocumentService.save_order_document(
            org_id=org_id,
            order_id=order_id,
            file=file,
            document_type=doc_type,
            title=title,
            is_customer_visible=is_cust_visible,
            uploaded_by_id=actor_id,
        )
        if wants_json:
            return jsonify({
                "success": True,
                "document_id": doc.id,
                "title": doc.title,
                "document_type": doc.document_type,
                "file_size": doc.file_size,
                "message": f"Document '{doc.title}' uploaded successfully.",
            }), 201
        flash(f"Document '{doc.title}' uploaded successfully.", "order_success")
    except OMSError as e:
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 400
        flash(f"Upload failed: {str(e)}", "order_error")
    except Exception as e:
        current_app.logger.exception("Unexpected error uploading order document")
        if wants_json:
            return jsonify({"success": False, "error": str(e)}), 500
        flash(f"Upload failed: {str(e)}", "order_error")

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


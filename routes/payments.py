from flask import (
    Blueprint, render_template, request, flash,
    redirect, url_for, jsonify, current_app, send_from_directory
)
from flask_login import login_required, current_user
import os
import uuid
from werkzeug.utils import secure_filename
from model import (
    db, Payment, PaymentRemark, PaymentStatus,
    PaymentPriority, PaymentMode, Employee, UserRole,
    PaymentDocument, Notification
)
from datetime import datetime, date
from utils.security import tenant_record_id

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _next_payment_number(org_id):
    """Generate sequential payment number like PAY-00042."""
    last = (
        Payment.query
        .filter_by(organization_id=org_id)
        .order_by(Payment.id.desc())
        .first()
    )
    seq = (last.id + 1) if last else 1
    return f"PAY-{seq:05d}"


def _get_employees(org_id):
    return Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()

def _allowed_file(filename):
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "pdf", "doc", "docx", "xls", "xlsx"}
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─────────────────────────────────────────────────────────────────────────────
# DUE PAYMENTS
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/due")
@login_required
def due_payments():
    org_id = current_user.organization_id
    q = Payment.query.filter_by(organization_id=org_id, is_deleted=False,
                                 status=PaymentStatus.PENDING)

    # Filters
    search = request.args.get("search", "").strip()
    employee_id = request.args.get("employee_id", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if search:
        like = f"%{search}%"
        q = q.filter(
            db.or_(
                Payment.customer_name.ilike(like),
                Payment.company_name.ilike(like),
                Payment.mobile.ilike(like),
                Payment.payment_number.ilike(like),
            )
        )
    if employee_id:
        q = q.filter(Payment.assigned_to == int(employee_id))
    if date_from:
        q = q.filter(Payment.due_date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        q = q.filter(Payment.due_date <= datetime.strptime(date_to, "%Y-%m-%d").date())

    payments = q.order_by(Payment.due_date.asc()).all()

    # Summary stats
    today = date.today()
    total_due_amount = sum(float(p.amount) for p in payments)
    total_due_count = len(payments)
    assigned_count = sum(1 for p in payments if p.assigned_to)
    overdue_count = sum(1 for p in payments if p.due_date < today)

    employees = _get_employees(org_id)
    priority_choices = [(p.value, p.value) for p in PaymentPriority]
    payment_modes = [(m.value, m.value) for m in PaymentMode]

    return render_template(
        "payments/due_payments.html",
        payments=payments,
        employees=employees,
        priority_choices=priority_choices,
        payment_modes=payment_modes,
        total_due_amount=total_due_amount,
        total_due_count=total_due_count,
        assigned_count=assigned_count,
        overdue_count=overdue_count,
        today=today,
        filters=dict(search=search, employee_id=employee_id,
                     date_from=date_from, date_to=date_to),
    )


# ─────────────────────────────────────────────────────────────────────────────
# ADD DUE PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/add", methods=["GET", "POST"])
@login_required
def add_payment():
    org_id = current_user.organization_id
    employees = _get_employees(org_id)

    if request.method == "POST":
        try:
            due_date_str = request.form.get("due_date")
            due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else date.today()
            assigned_emp_id = request.form.get("assigned_to") or None
            priority_val = request.form.get("priority", PaymentPriority.MEDIUM.value)

            payment = Payment(
                payment_number=_next_payment_number(org_id),
                customer_name=request.form["customer_name"].strip(),
                company_name=request.form.get("company_name", "").strip() or None,
                mobile=request.form["mobile"].strip(),
                email=request.form.get("email", "").strip() or None,
                amount=float(request.form["amount"]),
                due_date=due_date,
                assigned_to=int(assigned_emp_id) if assigned_emp_id else None,
                priority=PaymentPriority(priority_val),
                status=PaymentStatus.PENDING,
                notes=request.form.get("notes", "").strip() or None,
                organization_id=org_id,
                created_by=current_user.id,
            )
            db.session.add(payment)
            db.session.flush() # To get payment.id

            # Handle File Uploads
            files = request.files.getlist("documents")
            for file in files:
                if file and file.filename != "" and _allowed_file(file.filename):
                    ext = file.filename.rsplit(".", 1)[1].lower()
                    unique_name = f"{uuid.uuid4().hex}.{ext}"
                    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "receipts")
                    if not os.path.exists(upload_dir):
                        os.makedirs(upload_dir)
                    file.save(os.path.join(upload_dir, unique_name))
                    
                    doc = PaymentDocument(
                        payment_id=payment.id,
                        filename=unique_name,
                        original_name=secure_filename(file.filename),
                        file_type=ext,
                        uploaded_by=current_user.id
                    )
                    db.session.add(doc)

            # Notification
            if payment.assigned_to and payment.assigned_to != current_user.employee.id if current_user.employee else True:
                notif = Notification(
                    title="Due Payment Assigned",
                    message=f"Payment {payment.payment_number} for {payment.customer_name} (₹{payment.amount:,.0f}) has been assigned to you.",
                    recipient_id=payment.assigned_to,
                    sender_id=current_user.employee.id if current_user.employee else None,
                    organization_id=org_id,
                    link=url_for('payments.view_payment', payment_id=payment.id)
                )
                db.session.add(notif)

            db.session.commit()
            flash(f"Payment {payment.payment_number} created successfully.", "paymentsuccess")
            return redirect(url_for("payments.due_payments"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating payment: {str(e)}", "paymenterror")

    return render_template("payments/add_payment.html", employees=employees,
                           priorities=PaymentPriority, today=date.today().isoformat())


# ─────────────────────────────────────────────────────────────────────────────
# EDIT PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/edit/<int:payment_id>", methods=["GET", "POST"])
@login_required
def edit_payment(payment_id):
    org_id = current_user.organization_id
    payment = Payment.query.filter_by(id=payment_id, organization_id=org_id, is_deleted=False).first_or_404()
    employees = _get_employees(org_id)

    if request.method == "POST":
        try:
            due_date_str = request.form.get("due_date")
            assigned_emp_id = request.form.get("assigned_to") or None
            priority_val = request.form.get("priority", PaymentPriority.MEDIUM.value)
            
            old_assigned_to = payment.assigned_to
            new_assigned_to = int(assigned_emp_id) if assigned_emp_id else None

            payment.customer_name = request.form["customer_name"].strip()
            payment.company_name = request.form.get("company_name", "").strip() or None
            payment.mobile = request.form["mobile"].strip()
            payment.email = request.form.get("email", "").strip() or None
            payment.amount = float(request.form["amount"])
            payment.due_date = datetime.strptime(due_date_str, "%Y-%m-%d").date() if due_date_str else payment.due_date
            payment.assigned_to = new_assigned_to
            payment.priority = PaymentPriority(priority_val)
            payment.notes = request.form.get("notes", "").strip() or None

            # Handle File Uploads
            files = request.files.getlist("documents")
            for file in files:
                if file and file.filename != "" and _allowed_file(file.filename):
                    ext = file.filename.rsplit(".", 1)[1].lower()
                    unique_name = f"{uuid.uuid4().hex}.{ext}"
                    upload_dir = os.path.join(current_app.root_path, "static", "uploads", "receipts")
                    if not os.path.exists(upload_dir):
                        os.makedirs(upload_dir)
                    file.save(os.path.join(upload_dir, unique_name))
                    
                    doc = PaymentDocument(
                        payment_id=payment.id,
                        filename=unique_name,
                        original_name=secure_filename(file.filename),
                        file_type=ext,
                        uploaded_by=current_user.id
                    )
                    db.session.add(doc)

            # Notification if assigned employee changed
            if new_assigned_to and new_assigned_to != old_assigned_to:
                if new_assigned_to != (current_user.employee.id if current_user.employee else None):
                    notif = Notification(
                        title="Due Payment Assigned",
                        message=f"Payment {payment.payment_number} for {payment.customer_name} (₹{payment.amount:,.0f}) has been assigned to you.",
                        recipient_id=new_assigned_to,
                        sender_id=current_user.employee.id if current_user.employee else None,
                        organization_id=org_id,
                        link=url_for('payments.view_payment', payment_id=payment.id)
                    )
                    db.session.add(notif)

            db.session.commit()
            flash("Payment updated successfully.", "paymentsuccess")
            return redirect(url_for("payments.due_payments"))
        except Exception as e:
            db.session.rollback()
            flash(f"Error updating payment: {str(e)}", "paymenterror")

    return render_template("payments/edit_payment.html", payment=payment,
                           employees=employees, priorities=PaymentPriority)


# ─────────────────────────────────────────────────────────────────────────────
# ADD REMARK
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/remark/<int:payment_id>", methods=["POST"])
@login_required
def add_remark(payment_id):
    org_id = current_user.organization_id
    payment = Payment.query.filter_by(id=payment_id, organization_id=org_id, is_deleted=False).first_or_404()
    remark_text = request.form.get("remark", "").strip()
    if not remark_text:
        flash("Remark cannot be empty.", "paymenterror")
        return redirect(url_for("payments.view_payment", payment_id=payment_id))

    remark = PaymentRemark(
        payment_id=payment.id,
        remark=remark_text,
        created_by=current_user.id,
    )
    db.session.add(remark)
    db.session.commit()
    flash("Remark added.", "paymentsuccess")
    return redirect(url_for("payments.view_payment", payment_id=payment_id))


# ─────────────────────────────────────────────────────────────────────────────
# MARK AS RECEIVED
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/mark-received/<int:payment_id>", methods=["POST"])
@login_required
def mark_received(payment_id):
    org_id = current_user.organization_id
    payment = Payment.query.filter_by(id=payment_id, organization_id=org_id, is_deleted=False).first_or_404()

    amount_received = request.form.get("amount_received", "").strip()
    mode_val = request.form.get("payment_mode", "").strip()
    transaction_ref = request.form.get("transaction_reference", "").strip() or None
    received_date_str = request.form.get("received_date", "").strip()
    received_remarks = request.form.get("received_remarks", "").strip() or None
    received_by_emp = current_user.employee.id if current_user.employee else None

    try:
        payment.status = PaymentStatus.RECEIVED
        payment.payment_mode = PaymentMode(mode_val) if mode_val else None
        payment.transaction_reference = transaction_ref
        payment.received_date = datetime.strptime(received_date_str, "%Y-%m-%d").date() if received_date_str else date.today()
        payment.received_remarks = received_remarks
        payment.received_by = received_by_emp
        if amount_received:
            payment.amount = float(amount_received)

        # Auto-add a remark
        remark = PaymentRemark(
            payment_id=payment.id,
            remark=f"Payment marked as Received. Mode: {mode_val or 'N/A'}. Ref: {transaction_ref or 'N/A'}.",
            created_by=current_user.id,
        )
        db.session.add(remark)
        db.session.commit()
        flash(f"Payment {payment.payment_number} marked as Received.", "paymentsuccess")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "paymenterror")

    return redirect(url_for("payments.due_payments"))


# ─────────────────────────────────────────────────────────────────────────────
# VIEW PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/view/<int:payment_id>")
@login_required
def view_payment(payment_id):
    org_id = current_user.organization_id
    payment = Payment.query.filter_by(id=payment_id, organization_id=org_id, is_deleted=False).first_or_404()
    return render_template("payments/view_payment.html", payment=payment,
                           PaymentStatus=PaymentStatus)


# ─────────────────────────────────────────────────────────────────────────────
# RECEIVED PAYMENTS
# ─────────────────────────────────────────────────────────────────────────────

@payments_bp.route("/received")
@login_required
def received_payments():
    org_id = current_user.organization_id
    today = date.today()
    q = Payment.query.filter_by(organization_id=org_id, is_deleted=False,
                                 status=PaymentStatus.RECEIVED)

    # Filters
    search = request.args.get("search", "").strip()
    mode_filter = request.args.get("payment_mode", "").strip()
    employee_id = request.args.get("employee_id", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()

    if search:
        like = f"%{search}%"
        q = q.filter(
            db.or_(
                Payment.customer_name.ilike(like),
                Payment.company_name.ilike(like),
                Payment.payment_number.ilike(like),
                Payment.transaction_reference.ilike(like),
            )
        )
    if mode_filter:
        q = q.filter(Payment.payment_mode == PaymentMode(mode_filter))
    if employee_id:
        q = q.filter(Payment.received_by == int(employee_id))
    if date_from:
        q = q.filter(Payment.received_date >= datetime.strptime(date_from, "%Y-%m-%d").date())
    if date_to:
        q = q.filter(Payment.received_date <= datetime.strptime(date_to, "%Y-%m-%d").date())

    payments = q.order_by(Payment.received_date.desc()).all()

    # Summary stats
    today_total = sum(float(p.amount) for p in payments if p.received_date == today)
    month_total = sum(
        float(p.amount) for p in payments
        if p.received_date and p.received_date.month == today.month
        and p.received_date.year == today.year
    )
    grand_total = sum(float(p.amount) for p in payments)
    total_transactions = len(payments)

    employees = _get_employees(org_id)
    payment_modes = [(m.value, m.value) for m in PaymentMode]

    return render_template(
        "payments/received_payments.html",
        payments=payments,
        employees=employees,
        payment_modes=payment_modes,
        today_total=today_total,
        month_total=month_total,
        grand_total=grand_total,
        total_transactions=total_transactions,
        filters=dict(search=search, payment_mode=mode_filter,
                     employee_id=employee_id, date_from=date_from, date_to=date_to),
    )

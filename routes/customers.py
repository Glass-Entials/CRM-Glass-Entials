import os
import re
from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    Response,
    send_file,
    current_app,
)
from flask_login import login_required, current_user
from model import db, Customer, Employee, LeadSource, CustomerStatus, CustomerDocument
from utils.exports import export_to_csv, export_to_excel, export_to_pdf
import pandas as pd
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime
from utils.activity import log_activity
from utils.notifications import create_notification
from utils.security import tenant_record_id, validate_upload

customers_bp = Blueprint("customers", __name__)
DOCUMENT_EXTENSIONS = {
    "pdf",
    "png",
    "jpg",
    "jpeg",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "txt",
}


@customers_bp.route("/customers")
@login_required
def customers_list():
    org_id = current_user.organization_id
    status_filter = request.args.get("status")

    query = Customer.query.filter_by(organization_id=org_id, is_deleted=False)

    if status_filter:
        # Match enum value
        status_map = {e.value: e for e in CustomerStatus}
        if status_filter in status_map:
            query = query.filter(Customer.status == status_map[status_filter])

    all_customers = query.order_by(Customer.created_at.desc()).all()
    all_employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    unique_cities = sorted(list(set(c.city for c in all_customers if c.city)))

    return render_template(
        "customer/customer.html",
        customers=all_customers,
        employees=all_employees,
        cities=unique_cities,
        current_status=status_filter,
        CustomerStatus=CustomerStatus,
    )


@customers_bp.route("/export-customers/<string:format>")
@login_required
def export_customer(format):
    org_id = current_user.organization_id
    customers = (
        Customer.query.filter_by(organization_id=org_id, is_deleted=False)
        .order_by(Customer.created_at.desc())
        .all()
    )

    data = []
    for c in customers:
        data.append(
            {
                "id": str(c.id),
                "name": str(c.name or ""),
                "email": str(c.email or ""),
                "phone": str(c.phone_number or ""),
                "address": str(c.address or "—"),
                "city": str(c.city or "—"),
                "company": str(c.company or "—"),
                "source": str(c.source.value if c.source else "—"),
                "status": str(c.status.value if c.status else "New"),
                "created_date": (
                    c.created_at.strftime("%Y-%m-%d") if c.created_at else "—"
                ),
                "updated_date": (
                    c.updated_at.strftime("%Y-%m-%d") if c.updated_at else "—"
                ),
                "assigned_to": (
                    c.assignee.user.username
                    if c.assignee and c.assignee.user
                    else "Unassigned"
                ),
                "created_by": (
                    c.creator.user.username
                    if c.creator and c.creator.user
                    else "Unknown"
                ),
                "updated_by": (
                    c.updater.user.username
                    if c.updater and c.updater.user
                    else "Unknown"
                ),
            }
        )

    headers = [
        "ID",
        "Name",
        "Email",
        "Phone",
        "Address",
        "City",
        "Company",
        "Source",
        "Status",
        "Created_Date",
        "Updated_Date",
        "Assigned_To",
        "Created_By",
        "Updated_By",
    ]
    filename = f"customers_report_{format}.{'csv' if format == 'csv' else ('xlsx' if format == 'excel' else 'pdf')}"

    if format == "csv":
        return export_to_csv(data, headers, filename)
    elif format == "excel":
        return export_to_excel(data, headers, filename)
    elif format == "pdf":
        return export_to_pdf(
            data, headers, filename, title="Customer Management Report"
        )

    flash("Invalid format or export error.", "customererror")
    return redirect(url_for("customers.customers_list"))


@customers_bp.route("/add-customer", methods=["GET", "POST"])
@login_required
def add_customer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone_number = re.sub(r"\D", "", request.form.get("phone_number", ""))
        address = request.form.get("address", "").strip()
        city = request.form.get("city", "").strip()
        company = request.form.get("company", "").strip()
        source = request.form.get("source", "").strip()
        status = request.form.get("status", "New")
        notes = request.form.get("notes", "").strip()
        assigned_to_id = request.form.get("assigned_to")

        assigned_to_id = tenant_record_id(
            Employee, assigned_to_id, current_user.organization_id, is_deleted=False
        )

        if not all([name, email, phone_number]) or len(phone_number) != 10:
            flash(
                "All primary fields required and phone must be 10 digits.",
                "customererror",
            )
            return redirect(url_for("customers.add_customer"))

        existing = Customer.query.filter(
            ((Customer.email == email) | (Customer.phone_number == phone_number)),
            Customer.organization_id == current_user.organization_id,
            Customer.is_deleted == False,
        ).first()

        if existing:
            flash(
                "Customer already exists with this email or phone number in your organization.",
                "customererror",
            )
            return redirect(url_for("customers.add_customer"))

        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in CustomerStatus}

        new_customer = Customer(
            name=name,
            email=email,
            phone_number=phone_number,
            address=address,
            city=city,
            company=company,
            source=source_map.get(source, LeadSource.OTHER),
            status=status_map.get(status, CustomerStatus.REQUIREMENT_UNDERSTOOD),
            notes=notes,
            created_by=current_user.employee.id,
            assigned_to=assigned_to_id,
            organization_id=current_user.organization_id,
            # GST Fields
            gst_number=request.form.get("gst_number", "").strip(),
            trade_name=request.form.get("trade_name", "").strip(),
            state=request.form.get("state", "").strip(),
            pincode=request.form.get("pincode", "").strip(),
            business_type=request.form.get("business_type", "").strip(),
            gst_status=request.form.get("gst_status", "").strip(),
        )
        try:
            db.session.add(new_customer)
            db.session.flush()

            # Notification for assignment
            if assigned_to_id:
                create_notification(
                    recipient_id=assigned_to_id,
                    title="New Customer Assigned",
                    message=f"You have been assigned a new customer: {new_customer.name}",
                    link=url_for(
                        "customers.view_customer", customer_id=new_customer.id
                    ),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id,
                )

            log_activity(
                "customer_added",
                "customer",
                new_customer.name,
                current_user.organization_id,
                current_user.employee.id,
                new_customer.id,
            )
            db.session.commit()
            flash("Customer added successfully!", "customersuccess")
            return redirect(url_for("customers.customers_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "customererror")
            return redirect(url_for("customers.add_customer"))

    employees = Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()
    return render_template("customer/addcustomer.html", employees=employees)


@customers_bp.route("/edit-customer/<int:customer_id>", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    customer = Customer.query.filter_by(
        id=customer_id, organization_id=current_user.organization_id
    ).first_or_404()
    if request.method == "POST":
        customer.name = request.form.get("name", "").strip()
        customer.email = request.form.get("email", "").strip()
        customer.phone_number = re.sub(r"\D", "", request.form.get("phone_number", ""))
        customer.address = request.form.get("address", "").strip()
        customer.city = request.form.get("city", "").strip()
        customer.company = request.form.get("company", "").strip()

        assigned_to_id = request.form.get("assigned_to")
        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in CustomerStatus}

        try:
            # Check if assignment changed
            old_assignee = customer.assigned_to
            customer.assigned_to = tenant_record_id(
                Employee,
                assigned_to_id,
                current_user.organization_id,
                is_deleted=False,
            )

            customer.source = source_map.get(
                request.form.get("source"), LeadSource.OTHER
            )
            customer.status = status_map.get(
                request.form.get("status"), CustomerStatus.NEW
            )
            customer.notes = request.form.get("notes", "").strip()

            # GST Fields
            customer.gst_number = request.form.get("gst_number", "").strip()
            customer.trade_name = request.form.get("trade_name", "").strip()
            customer.state = request.form.get("state", "").strip()
            customer.pincode = request.form.get("pincode", "").strip()
            customer.business_type = request.form.get("business_type", "").strip()
            customer.gst_status = request.form.get("gst_status", "").strip()

            customer.updated_by = current_user.employee.id

            if customer.assigned_to and customer.assigned_to != old_assignee:
                create_notification(
                    recipient_id=customer.assigned_to,
                    title="Customer Assigned to You",
                    message=f"Customer '{customer.name}' has been assigned to you.",
                    link=url_for("customers.view_customer", customer_id=customer.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id,
                )

            log_activity(
                "customer_updated",
                "customer",
                customer.name,
                current_user.organization_id,
                current_user.employee.id,
                customer.id,
            )
            db.session.commit()
            flash("Customer updated successfully!", "customersuccess")
            return redirect(url_for("customers.customers_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "customererror")
            return redirect(url_for("customers.edit_customer", customer_id=customer_id))

    employees = Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()
    return render_template(
        "customer/editcustomer.html",
        customer=customer,
        customer_id=customer_id,
        employees=employees,
    )


@customers_bp.route("/delete-customer/<int:customer_id>", methods=["POST"])
@login_required
def delete_customer(customer_id):
    customer = Customer.query.filter_by(
        id=customer_id, organization_id=current_user.organization_id
    ).first_or_404()
    customer.is_deleted = True
    log_activity(
        "customer_deleted",
        "customer",
        customer.name,
        current_user.organization_id,
        current_user.employee.id,
        customer.id,
    )
    db.session.commit()
    flash("Customer deleted.", "customersuccess")
    return redirect(url_for("customers.customers_list"))


@customers_bp.route("/view-customer/<int:customer_id>")
@login_required
def view_customer(customer_id):
    customer = Customer.query.filter_by(
        id=customer_id, organization_id=current_user.organization_id
    ).first_or_404()
    return render_template("customer/customer_profile.html", customer=customer)


@customers_bp.route("/bulk-upload", methods=["GET", "POST"])
@login_required
def bulk_upload():
    if request.method == "POST":
        file = request.files.get("customer_file")
        if not file:
            flash("No file.", "customererror")
            return redirect(url_for("customers.bulk_upload"))
        try:
            try:
                df = pd.read_csv(file, encoding="utf-8").fillna("")
            except UnicodeDecodeError:
                file.seek(0)
                try:
                    df = pd.read_csv(file, encoding="cp1252").fillna("")
                except UnicodeDecodeError:
                    file.seek(0)
                    df = pd.read_csv(file, encoding="latin1").fillna("")
            
            col_map = {c.lower(): c for c in df.columns}
            if not {"name", "email", "phone"}.issubset(set(col_map.keys())):
                flash("Missing required columns: Name, Email, Phone.", "customererror")
                return redirect(url_for("customers.bulk_upload"))

            source_map = {e.value.lower(): e for e in LeadSource}
            status_map = {e.value.lower(): e for e in CustomerStatus}
            
            total_rows = len(df)
            imported = 0
            skipped_duplicates = 0
            skipped_validation = 0
            skipped_db = 0
            failed_rows = []

            for index, row in df.iterrows():
                row_num = index + 2  # header is row 1
                
                email_col = col_map.get("email", "")
                phone_col = col_map.get("phone", "")
                name_col = col_map.get("name", "")
                
                name = str(row.get(name_col, "")).strip() if name_col else ""
                email = str(row.get(email_col, "")).strip() if email_col else ""
                phone_raw = str(row.get(phone_col, "")).strip() if phone_col else ""
                
                # Normalize phone
                phone_raw = re.sub(r'\.0+$', '', phone_raw)
                phone_number = re.sub(r"\D", "", phone_raw)
                if len(phone_number) == 12 and phone_number.startswith("91"):
                    phone_number = phone_number[2:]

                # Validation
                if not name:
                    failed_rows.append({"row": row_num, "name": "Unknown", "reason": "Missing Name"})
                    current_app.logger.warning(f"Row {row_num} skipped\nReason:\nMissing Name\n")
                    skipped_validation += 1
                    continue
                if phone_number and len(phone_number) != 10:
                    failed_rows.append({"row": row_num, "name": name, "reason": "Invalid Phone"})
                    current_app.logger.warning(f"Row {row_num} skipped\nReason:\nInvalid Phone\nPhone:\n{phone_raw}\n")
                    skipped_validation += 1
                    continue

                existing = None
                if email and phone_number:
                    existing = Customer.query.filter(
                        ((Customer.email == email) | (Customer.phone_number == phone_number)),
                        Customer.organization_id == current_user.organization_id,
                        Customer.is_deleted == False,
                    ).first()
                elif email:
                    existing = Customer.query.filter(
                        Customer.email == email,
                        Customer.organization_id == current_user.organization_id,
                        Customer.is_deleted == False,
                    ).first()
                elif phone_number:
                    existing = Customer.query.filter(
                        Customer.phone_number == phone_number,
                        Customer.organization_id == current_user.organization_id,
                        Customer.is_deleted == False,
                    ).first()

                if existing:
                    reason = "Duplicate Email" if existing.email == email and email else "Duplicate Phone"
                    failed_rows.append({"row": row_num, "name": name, "reason": reason})
                    current_app.logger.warning(f"Row {row_num} skipped\nReason:\n{reason}\nEmail:\n{email}\nPhone:\n{phone_number}\n")
                    skipped_duplicates += 1
                    continue

                c_comp = col_map.get("company", "")
                c_addr = col_map.get("address", "")
                c_city = col_map.get("city", "")
                c_src = col_map.get("source", "")
                c_stat = col_map.get("status", "")

                s_val = str(row.get(c_src, "")).strip().lower() if c_src else ""
                st_val = str(row.get(c_stat, "")).strip().lower() if c_stat else ""

                new_c = Customer(
                    name=name,
                    email=email,
                    phone_number=phone_number,
                    company=str(row.get(c_comp, "")).strip() if c_comp else None,
                    address=str(row.get(c_addr, "")).strip() if c_addr else None,
                    city=str(row.get(c_city, "")).strip() if c_city else None,
                    source=source_map.get(s_val, LeadSource.OTHER),
                    status=status_map.get(
                        st_val, CustomerStatus.REQUIREMENT_UNDERSTOOD
                    ),
                    organization_id=current_user.organization_id,
                    created_by=current_user.employee.id,
                )
                
                try:
                    with db.session.begin_nested():
                        db.session.add(new_c)
                    imported += 1
                    current_app.logger.info(f"Row {row_num} imported successfully.")
                except Exception as db_e:
                    failed_rows.append({"row": row_num, "name": name, "reason": "Database Error"})
                    skipped_db += 1
                    current_app.logger.error(f"Row {row_num} skipped\nReason:\nDatabase Error\nDetails:\n{str(db_e)}\n")

            db.session.commit()
            
            flash("Bulk Upload Completed", "customersuccess")
            flash(f"Total Rows : {total_rows}", "customersuccess")
            flash(f"Imported : {imported}", "customersuccess")
            
            skipped_total = skipped_duplicates + skipped_validation + skipped_db
            if skipped_total > 0:
                flash(f"Skipped : {skipped_total}", "customererror")
            if skipped_duplicates > 0:
                flash(f"Duplicates : {skipped_duplicates}", "customererror")
            if skipped_validation > 0:
                flash(f"Validation Errors : {skipped_validation}", "customererror")
            if skipped_db > 0:
                flash(f"Database Errors : {skipped_db}", "customererror")
                
            if failed_rows:
                for f in failed_rows[:10]:
                    flash(f"Row {f['row']} ({f['name']}): {f['reason']}", "customererror")
                if len(failed_rows) > 10:
                    flash(f"...and {len(failed_rows) - 10} more. Check logs for full report.", "customererror")
            
            return redirect(url_for("customers.customers_list"))
        except Exception as e:
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "customererror")
    return render_template("customer/bulkuploadcustomer.html")


@customers_bp.route("/customer-document/upload/<int:customer_id>", methods=["POST"])
@login_required
def upload_document(customer_id):
    customer = Customer.query.filter_by(
        id=customer_id, organization_id=current_user.organization_id
    ).first_or_404()

    if "document" not in request.files:
        flash("No file part", "customererror")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    file = request.files["document"]
    if file.filename == "":
        flash("No selected file", "customererror")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    if file:
        original_filename, extension, file_size = validate_upload(
            file, DOCUMENT_EXTENSIONS
        )

        # Unique filename to avoid collisions
        unique_filename = f"{uuid.uuid4().hex}.{extension}"

        upload_folder = os.path.join(
            current_app.root_path, "static", "uploads", "customer_docs"
        )
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder, exist_ok=True)

        file_path = os.path.join(upload_folder, unique_filename)
        file.save(file_path)

        new_doc = CustomerDocument(
            customer_id=customer.id,
            filename=unique_filename,
            original_name=original_filename,
            file_type=extension,
            organization_id=current_user.organization_id,
        )

        db.session.add(new_doc)
        db.session.flush()
        log_activity(
            "document_uploaded",
            "document",
            f"{original_filename} (Customer: {customer.name})",
            current_user.organization_id,
            current_user.employee.id,
            new_doc.id,
        )
        db.session.commit()

        flash(
            f'Document "{original_filename}" uploaded successfully!', "customersuccess"
        )
        return redirect(url_for("customers.view_customer", customer_id=customer_id))


@customers_bp.route("/customer-document/download/<int:doc_id>")
@login_required
def download_document(doc_id):
    doc = CustomerDocument.query.filter_by(
        id=doc_id, organization_id=current_user.organization_id
    ).first_or_404()
    file_path = os.path.join(
        current_app.root_path, "static", "uploads", "customer_docs", doc.filename
    )

    if not os.path.exists(file_path):
        flash("File not found on server.", "customererror")
        return redirect(url_for("customers.view_customer", customer_id=doc.customer_id))

    return send_file(file_path, as_attachment=True, download_name=doc.original_name)


@customers_bp.route("/customer-document/delete/<int:doc_id>", methods=["POST"])
@login_required
def delete_document(doc_id):
    doc = CustomerDocument.query.filter_by(
        id=doc_id, organization_id=current_user.organization_id
    ).first_or_404()
    customer_id = doc.customer_id

    file_path = os.path.join(
        current_app.root_path, "static", "uploads", "customer_docs", doc.filename
    )

    try:
        if os.path.exists(file_path):
            os.remove(file_path)

        log_activity(
            "document_deleted",
            "document",
            doc.original_name,
            current_user.organization_id,
            current_user.employee.id,
            doc.id,
        )
        db.session.delete(doc)
        db.session.commit()
        flash("Document deleted successfully.", "customersuccess")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error: {str(e)}", exc_info=True)
        flash("An error occurred. Please try again.", "customererror")

    return redirect(url_for("customers.view_customer", customer_id=customer_id))

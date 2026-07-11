import os
import re
from datetime import datetime
from flask import (
    Blueprint,
    render_template,
    request,
    flash,
    redirect,
    url_for,
    current_app,
    abort,
)
from flask_login import login_required, current_user
from model import (
    db,
    Contact,
    ContactStatus,
    ContactActivity,
    ContactNote,
    ContactSystemLog,
    Employee,
    LeadSource,
    ActivityType,
    Lead,
    LeadStatus,
)
import pandas as pd
from utils.exports import export_to_csv, export_to_excel, export_to_pdf
from utils.activity import log_activity
from utils.notifications import create_notification
from utils.security import tenant_record_id

contacts_bp = Blueprint("contacts", __name__)

def log_contact_event(contact_id, event_type, message, icon="🔔", actor_id=None, organization_id=None):
    try:
        log = ContactSystemLog(
            contact_id=contact_id,
            event_type=event_type,
            message=message,
            icon=icon,
            actor_id=actor_id,
            organization_id=organization_id,
        )
        db.session.add(log)
    except Exception as e:
        current_app.logger.error(f"Error logging contact event: {str(e)}", exc_info=True)


@contacts_bp.route("/contacts")
@login_required
def contacts_list():
    org_id = current_user.organization_id
    query = Contact.query.filter_by(organization_id=org_id, is_deleted=False)
    
    assigned_to = request.args.get("assigned_to")
    if assigned_to:
        query = query.filter_by(assigned_to=int(assigned_to))
        
    all_contacts = query.order_by(Contact.created_at.desc()).all()
    all_employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    return render_template("contacts/contacts_list.html", contacts=all_contacts, employees=all_employees)


@contacts_bp.route("/contacts/<int:contact_id>")
@login_required
def view_contact(contact_id):
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=current_user.organization_id
    ).first_or_404()
    now_utc = datetime.utcnow()
    all_employees = Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()
    activities = (
        ContactActivity.query.filter_by(contact_id=contact_id, organization_id=current_user.organization_id)
        .order_by(ContactActivity.created_at.desc())
        .all()
    )
    is_manager = current_user.role.value in ["admin", "manager"]
    notes = (
        ContactNote.query.filter_by(contact_id=contact_id, organization_id=current_user.organization_id)
        .order_by(ContactNote.created_at.desc())
        .all()
    )
    system_logs = (
        ContactSystemLog.query.filter_by(contact_id=contact_id, organization_id=current_user.organization_id)
        .order_by(ContactSystemLog.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template(
        "contacts/view_contact.html",
        contact=contact,
        now_utc=now_utc,
        employees=all_employees,
        activities=activities,
        ActivityType=ActivityType,
        is_manager=is_manager,
        notes=notes,
        system_logs=system_logs,
    )


@contacts_bp.route("/contacts/add", methods=["GET", "POST"])
@login_required
def add_contact():
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip() or None
        phone_number = request.form.get("phone")

        assigned_to_id = request.form.get("assigned_to")
        assigned_to_id = tenant_record_id(
            Employee, assigned_to_id, current_user.organization_id, is_deleted=False
        )

        if not all([first_name, phone_number]) or len(phone_number) != 10:
            flash("Required fields missing or phone invalid.", "leadserror")
            return redirect(url_for("contacts.add_contact"))

        if email:
            existing = Contact.query.filter(
                ((Contact.email == email) | (Contact.phone_number == phone_number)),
                Contact.organization_id == current_user.organization_id,
                Contact.is_deleted == False,
            ).first()
        else:
            existing = Contact.query.filter(
                (Contact.phone_number == phone_number),
                Contact.organization_id == current_user.organization_id,
                Contact.is_deleted == False,
            ).first()

        if existing:
            flash(
                "Contact already exists with this email or phone number in your organization.",
                "leadserror",
            )
            return redirect(url_for("contacts.add_contact"))

        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in ContactStatus}

        new_contact = Contact(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone_number=phone_number,
            secondary_phone=request.form.get("secondary_phone"),
            whatsapp_number=request.form.get("whatsapp_number"),
            website=request.form.get("website"),
            company=request.form.get("company"),
            designation=request.form.get("designation"),
            address=request.form.get("address"),
            city=request.form.get("city"),
            state=request.form.get("state"),
            country=request.form.get("country"),
            pincode=request.form.get("pincode"),
            notes=request.form.get("notes", ""),
            tags=request.form.get("tags", ""),
            source=source_map.get(request.form.get("source"), LeadSource.OTHER),
            status=status_map.get(request.form.get("status"), ContactStatus.CONTACT),
            created_by=current_user.employee.id,
            assigned_to=assigned_to_id,
            organization_id=current_user.organization_id,
        )
        
        birthday_str = request.form.get("birthday")
        if birthday_str:
            try:
                new_contact.birthday = datetime.strptime(birthday_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        try:
            db.session.add(new_contact)
            db.session.flush()

            if assigned_to_id:
                assignee = Employee.query.get(assigned_to_id)
                create_notification(
                    recipient_id=assigned_to_id,
                    title="New Contact Assigned",
                    message=f"You have been assigned a new contact: {new_contact.name}",
                    link=url_for("contacts.view_contact", contact_id=new_contact.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id,
                )
                assignee_name = assignee.name if assignee else "someone"
                log_contact_event(new_contact.id, "contact_assigned", f"Contact assigned to {assignee_name}.", "👤", current_user.employee.id, current_user.organization_id)

            log_contact_event(new_contact.id, "contact_created", f"{current_user.employee.name} created the contact.", "🌱", current_user.employee.id, current_user.organization_id)
            log_activity(
                "contact_added",
                "contact",
                new_contact.name,
                current_user.organization_id,
                current_user.employee.id,
                new_contact.id,
            )
            db.session.commit()
            flash("Contact added!", "leadssuccess")
            return redirect(url_for("contacts.contacts_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "leadserror")
            return redirect(url_for("contacts.add_contact"))

    employees = Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()
    return render_template("contacts/add_contact.html", employees=employees)


@contacts_bp.route("/contacts/<int:contact_id>/edit", methods=["GET", "POST"])
@login_required
def edit_contact(contact_id):
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=current_user.organization_id
    ).first_or_404()
    
    if request.method == "POST":
        contact.first_name = request.form.get("first_name", "").strip()
        contact.last_name = request.form.get("last_name", "").strip()
        contact.email = request.form.get("email", "").strip() or None
        contact.phone_number = re.sub(r"\D", "", request.form.get("phone", ""))
        contact.secondary_phone = re.sub(r"\D", "", request.form.get("secondary_phone", ""))
        contact.whatsapp_number = re.sub(r"\D", "", request.form.get("whatsapp_number", ""))
        contact.website = request.form.get("website", "").strip()
        contact.designation = request.form.get("designation", "").strip()
        contact.state = request.form.get("state", "").strip()
        contact.country = request.form.get("country", "").strip()
        contact.pincode = request.form.get("pincode", "").strip()
        contact.tags = request.form.get("tags", "").strip()

        birthday_str = request.form.get("birthday")
        if birthday_str:
            try:
                contact.birthday = datetime.strptime(birthday_str, "%Y-%m-%d").date()
            except ValueError:
                pass

        assigned_to_id = request.form.get("assigned_to")
        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in ContactStatus}

        try:
            old_assignee = contact.assigned_to
            old_status = contact.status

            contact.assigned_to = tenant_record_id(
                Employee,
                assigned_to_id,
                current_user.organization_id,
                is_deleted=False,
            )

            contact.source = source_map.get(request.form.get("source"), LeadSource.OTHER)
            new_status = status_map.get(request.form.get("status"), ContactStatus.CONTACT)
            contact.status = new_status
            contact.company = request.form.get("company", "").strip()
            contact.address = request.form.get("address", "").strip()
            contact.city = request.form.get("city", "").strip()
            contact.notes = request.form.get("notes", "").strip()

            contact.updated_by = current_user.employee.id

            if contact.assigned_to and contact.assigned_to != old_assignee:
                assignee = Employee.query.get(contact.assigned_to)
                create_notification(
                    recipient_id=contact.assigned_to,
                    title="Contact Assigned to You",
                    message=f"Contact '{contact.name}' has been assigned to you.",
                    link=url_for("contacts.view_contact", contact_id=contact.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id,
                )
                aname = assignee.name if assignee else "someone"
                log_contact_event(contact.id, "contact_assigned", f"Contact reassigned to {aname} by {current_user.employee.name}.", "👤", current_user.employee.id, current_user.organization_id)

            if old_status and new_status and old_status != new_status:
                log_contact_event(contact.id, "status_changed", f"Status changed from {old_status.value} to {new_status.value} by {current_user.employee.name}.", "🔄", current_user.employee.id, current_user.organization_id)

            log_activity(
                "contact_updated",
                "contact",
                contact.name,
                current_user.organization_id,
                current_user.employee.id,
                contact.id,
            )
            db.session.commit()
            flash("Contact updated!", "leadssuccess")
            return redirect(url_for("contacts.contacts_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "leadserror")
            return redirect(url_for("contacts.edit_contact", contact_id=contact_id))

    employees = Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()
    return render_template(
        "contacts/edit_contact.html", contact=contact, employees=employees
    )


@contacts_bp.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
def delete_contact(contact_id):
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=current_user.organization_id
    ).first_or_404()
    contact.is_deleted = True
    log_activity(
        "contact_deleted",
        "contact",
        contact.name,
        current_user.organization_id,
        current_user.employee.id,
        contact.id,
    )
    db.session.commit()
    flash("Contact deleted.", "leadssuccess")
    return redirect(url_for("contacts.contacts_list"))


@contacts_bp.route("/contacts/<int:contact_id>/convert", methods=["POST"])
@login_required
def convert_to_lead(contact_id):
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=current_user.organization_id
    ).first_or_404()
    
    if contact.status == ContactStatus.LEAD and contact.converted_lead:
        flash("Contact is already converted to a Lead.", "leadserror")
        return redirect(url_for("contacts.view_contact", contact_id=contact.id))

    lead_title = request.form.get("lead_title", contact.name)
    interested_products = request.form.get("interested_products", "")
    estimated_budget = request.form.get("estimated_budget", "")
    priority = request.form.get("priority", "Medium")
    remarks = request.form.get("remarks", "")
    lead_owner_id = request.form.get("lead_owner")

    lead_owner_id = tenant_record_id(Employee, lead_owner_id, current_user.organization_id, is_deleted=False) or contact.assigned_to
    
    notes_str = f"Interested Products: {interested_products}\nEstimated Budget: {estimated_budget}\nPriority: {priority}\nRemarks: {remarks}"
    
    try:
        new_lead = Lead(
            name=lead_title or contact.name,
            email=contact.email,
            phone_number=contact.phone_number,
            company=contact.company,
            address=contact.address,
            city=contact.city,
            notes=f"{contact.notes}\n\n---\n{notes_str}",
            source=contact.source,
            status=LeadStatus.NEW,
            created_by=current_user.employee.id,
            assigned_to=lead_owner_id,
            organization_id=current_user.organization_id,
            contact_id=contact.id
        )
        
        db.session.add(new_lead)
        db.session.flush()

        contact.status = ContactStatus.LEAD
        contact.lead_id = new_lead.id
        
        log_contact_event(contact.id, "converted_to_lead", f"{current_user.employee.name} converted this contact to a lead.", "🚀", current_user.employee.id, current_user.organization_id)
        
        db.session.commit()
        flash("Contact successfully converted to Lead!", "leadssuccess")
        return redirect(url_for("leads.view_lead", lead_id=new_lead.id))
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error converting contact to lead: {str(e)}", exc_info=True)
        flash("Failed to convert contact to lead.", "leadserror")
        return redirect(url_for("contacts.view_contact", contact_id=contact.id))


@contacts_bp.route("/contacts/<int:contact_id>/add-note", methods=["POST"])
@login_required
def add_note(contact_id):
    contact = Contact.query.filter_by(id=contact_id, organization_id=current_user.organization_id).first_or_404()
    text = request.form.get("note", "").strip()
    if not text:
        flash("Note cannot be empty.", "leadserror")
        return redirect(url_for("contacts.view_contact", contact_id=contact_id))
    try:
        c = ContactNote(
            contact_id=contact.id,
            note=text,
            created_by=current_user.employee.id,
            organization_id=current_user.organization_id,
        )
        db.session.add(c)
        snippet = (text[:60] + "…") if len(text) > 60 else text
        log_contact_event(contact.id, "note_added", f"{current_user.employee.name} added a note: \"{snippet}\"", "💬", current_user.employee.id, current_user.organization_id)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error adding note: {str(e)}", exc_info=True)
        flash("Could not post note.", "leadserror")
    return redirect(url_for("contacts.view_contact", contact_id=contact_id) + "#contact-notes")


@contacts_bp.route("/contacts/<int:contact_id>/add-activity", methods=["POST"])
@login_required
def add_activity(contact_id):
    contact = Contact.query.filter_by(
        id=contact_id, organization_id=current_user.organization_id
    ).first_or_404()
    activity_type = request.form.get("activity_type")
    description = request.form.get("description", "").strip()

    if not activity_type or not description:
        flash("Activity type and description are required.", "leadserror")
        return redirect(url_for("contacts.view_contact", contact_id=contact_id))

    try:
        type_map = {e.value: e for e in ActivityType}
        new_act = ContactActivity(
            contact_id=contact.id,
            activity_type=type_map.get(activity_type, ActivityType.Note),
            description=description,
            created_by=current_user.employee.id,
            organization_id=current_user.organization_id,
        )
        db.session.add(new_act)
        log_contact_event(contact.id, "activity_logged", f"{current_user.employee.name} logged a {activity_type}: \"{description[:60]}\"", "📝", current_user.employee.id, current_user.organization_id)
        db.session.commit()
        flash("Activity logged!", "leadssuccess")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error: {str(e)}", exc_info=True)
        flash("An error occurred. Please try again.", "leadserror")

    return redirect(url_for("contacts.view_contact", contact_id=contact_id))


@contacts_bp.route("/contacts/export/<string:format>")
@login_required
def export_contacts(format):
    contacts = (
        Contact.query.filter_by(
            organization_id=current_user.organization_id, is_deleted=False
        )
        .order_by(Contact.created_at.desc())
        .all()
    )
    data = []
    for l in contacts:
        data.append(
            {
                "ID": str(l.id),
                "First Name": str(l.first_name or ""),
                "Last Name": str(l.last_name or ""),
                "Email": str(l.email or ""),
                "Phone": str(l.phone_number or ""),
                "Company": str(l.company or "—"),
                "Source": str(l.source.value if l.source else "—"),
                "Status": str(l.status.value if l.status else "Contact"),
                "Created Date": (
                    l.created_at.strftime("%Y-%m-%d") if l.created_at else "—"
                ),
                "Assigned To": (
                    l.assignee.user.username
                    if l.assignee and l.assignee.user
                    else "Unassigned"
                ),
            }
        )
    headers = [
        "ID",
        "First Name",
        "Last Name",
        "Email",
        "Phone",
        "Company",
        "Source",
        "Status",
        "Created Date",
        "Assigned To",
    ]
    filename = f"contacts_report_{format}.{'csv' if format == 'csv' else ('xlsx' if format == 'excel' else 'pdf')}"

    if format == "csv":
        return export_to_csv(data, headers, filename)
    elif format == "excel":
        return export_to_excel(data, headers, filename)
    elif format == "pdf":
        return export_to_pdf(data, headers, filename, title="Contacts Management Report")

    flash("Invalid format.", "leadserror")
    return redirect(url_for("contacts.contacts_list"))


@contacts_bp.route("/contacts/bulk-upload", methods=["GET", "POST"])
@login_required
def bulk_upload_contacts():
    if request.method == "POST":
        file = request.files.get("contact_file")
        if not file:
            flash("No file selected.", "leadserror")
            return redirect(url_for("contacts.bulk_upload_contacts"))
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

            col_map = {c.lower().strip(): c for c in df.columns}

            if "name" not in col_map:
                flash("Missing required column: Name.", "leadserror")
                return redirect(url_for("contacts.bulk_upload_contacts"))
            if "phone" not in col_map:
                flash("Missing required column: Phone.", "leadserror")
                return redirect(url_for("contacts.bulk_upload_contacts"))

            source_map = {e.value.lower(): e for e in LeadSource}
            status_map = {e.value.lower(): e for e in ContactStatus}

            total_rows = len(df)
            imported = 0
            skipped_duplicates = 0
            skipped_validation = 0
            skipped_db = 0
            failed_rows = []

            for index, row in df.iterrows():
                row_num = index + 2

                # Name resolution — split Name into first and last name
                name_col = col_map.get("name", "")
                full_name = str(row.get(name_col, "")).strip() if name_col else ""
                
                parts = full_name.split(" ", 1)
                first_name = parts[0] if parts else None
                last_name = parts[1] if len(parts) > 1 else None

                phone_col = col_map.get("phone", "")
                phone_raw = str(row.get(phone_col, "")).strip() if phone_col else ""
                phone_number = None
                if phone_raw:
                    phone_raw = re.sub(r"\.0+$", "", phone_raw)
                    phone_number = re.sub(r"\D", "", phone_raw)
                    if len(phone_number) == 12 and phone_number.startswith("91"):
                        phone_number = phone_number[2:]
                    if not phone_number:
                        phone_number = None

                # Validation
                if not first_name:
                    failed_rows.append({"row": row_num, "name": "Unknown", "reason": "Missing First Name"})
                    skipped_validation += 1
                    continue
                if not phone_number:
                    failed_rows.append({"row": row_num, "name": first_name, "reason": "Missing Phone"})
                    skipped_validation += 1
                    continue
                if len(phone_number) != 10:
                    failed_rows.append({"row": row_num, "name": first_name, "reason": f"Invalid Phone: {phone_raw}"})
                    skipped_validation += 1
                    continue

                # Duplicate check
                existing = Contact.query.filter(
                    Contact.phone_number == phone_number,
                    Contact.organization_id == current_user.organization_id,
                    Contact.is_deleted == False,
                ).first()
                if existing:
                    failed_rows.append({"row": row_num, "name": first_name, "reason": "Duplicate Phone"})
                    skipped_duplicates += 1
                    continue

                # Optional fields
                email_col = col_map.get("email", "")
                company_col = col_map.get("company", "")
                designation_col = col_map.get("designation", "")
                city_col = col_map.get("city", "")
                state_col = col_map.get("state", "")
                country_col = col_map.get("country", "")
                source_col = col_map.get("source", "")
                status_col = col_map.get("status", "")

                email = str(row.get(email_col, "")).strip() if email_col else ""
                email = email or None
                company = str(row.get(company_col, "")).strip() if company_col else ""
                company = company or None
                designation = str(row.get(designation_col, "")).strip() if designation_col else ""
                designation = designation or None
                city = str(row.get(city_col, "")).strip() if city_col else ""
                city = city or None
                state = str(row.get(state_col, "")).strip() if state_col else ""
                state = state or None
                country = str(row.get(country_col, "")).strip() if country_col else ""
                country = country or None

                s_val = str(row.get(source_col, "")).strip().lower() if source_col else ""
                st_val = str(row.get(status_col, "")).strip().lower() if status_col else ""

                new_contact = Contact(
                    first_name=first_name,
                    last_name=last_name,
                    email=email,
                    phone_number=phone_number,
                    company=company,
                    designation=designation,
                    city=city,
                    state=state,
                    country=country,
                    source=source_map.get(s_val, LeadSource.OTHER),
                    status=status_map.get(st_val, ContactStatus.CONTACT),
                    organization_id=current_user.organization_id,
                    created_by=current_user.employee.id,
                )
                try:
                    with db.session.begin_nested():
                        db.session.add(new_contact)
                    imported += 1
                except Exception as db_e:
                    failed_rows.append({"row": row_num, "name": first_name, "reason": "Database Error"})
                    skipped_db += 1
                    current_app.logger.error(f"Row {row_num} DB error: {str(db_e)}")

            db.session.commit()

            flash("Bulk Upload Completed", "leadssuccess")
            flash(f"Total Rows: {total_rows}", "leadssuccess")
            flash(f"Imported: {imported}", "leadssuccess")

            skipped_total = skipped_duplicates + skipped_validation + skipped_db
            if skipped_total > 0:
                flash(f"Skipped: {skipped_total}", "leadserror")
            if skipped_duplicates > 0:
                flash(f"Duplicates: {skipped_duplicates}", "leadserror")
            if skipped_validation > 0:
                flash(f"Validation Errors: {skipped_validation}", "leadserror")
            if skipped_db > 0:
                flash(f"Database Errors: {skipped_db}", "leadserror")

            for f in failed_rows[:10]:
                flash(f"Row {f['row']} ({f['name']}): {f['reason']}", "leadserror")
            if len(failed_rows) > 10:
                flash(f"...and {len(failed_rows) - 10} more errors. Check server logs.", "leadserror")

            return redirect(url_for("contacts.contacts_list"))

        except Exception as e:
            current_app.logger.error(f"Bulk upload error: {str(e)}", exc_info=True)
            flash("An error occurred while processing the file. Please check the format and try again.", "leadserror")

    return render_template("contacts/bulk_upload_contacts.html")

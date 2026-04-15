import os
import re
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from model import db, Lead, Employee, LeadSource, LeadStatus, LeadActivity, ActivityType, Customer, CustomerStatus
from utils.exports import export_to_csv, export_to_excel, export_to_pdf
from utils.activity import log_activity
from utils.notifications import create_notification
import pandas as pd

leads_bp = Blueprint('leads', __name__)

@leads_bp.route('/leads')
@login_required
def leads_list():
    org_id = current_user.organization_id
    all_leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Lead.created_at.desc()).all()
    all_employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template('leads/Lead.html', leads=all_leads, employees=all_employees)

@leads_bp.route('/view-lead/<int:lead_id>')
@login_required
def view_lead(lead_id):
    lead = Lead.query.filter_by(id=lead_id, organization_id=current_user.organization_id).first_or_404()
    return render_template('leads/lead_profile.html', lead=lead)

@leads_bp.route('/add-lead', methods=['GET', 'POST'])
@login_required
def add_lead():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone_number = request.form.get('phone')
        
        assigned_to_id = request.form.get('assigned_to')
        assigned_to_id = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None

        if not all([name, email, phone_number]) or len(phone_number) != 10:
            flash('Required fields missing or phone invalid.', 'leadserror')
            return redirect(url_for('leads.add_lead'))

        existing = Lead.query.filter(
            ((Lead.email == email) | (Lead.phone_number == phone_number)),
            Lead.organization_id == current_user.organization_id,
            Lead.is_deleted == False
        ).first()

        if existing:
            flash('Lead already exists with this email or phone number in your organization.', 'leadserror')
            return redirect(url_for('leads.add_lead'))

        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in LeadStatus}
        
        new_lead = Lead(
            name=name, email=email, phone_number=phone_number,
            company=request.form.get('company'), address=request.form.get('address'),
            city=request.form.get('city'), notes=request.form.get('notes', ''),
            source=source_map.get(request.form.get('source'), LeadSource.OTHER),
            status=status_map.get(request.form.get('status'), LeadStatus.NEW),
            created_by=current_user.employee.id, assigned_to=assigned_to_id,
            organization_id=current_user.organization_id,
            # GST Fields
            gst_number=request.form.get('gst_number', '').strip(),
            trade_name=request.form.get('trade_name', '').strip(),
            state=request.form.get('state', '').strip(),
            pincode=request.form.get('pincode', '').strip(),
            business_type=request.form.get('business_type', '').strip(),
            gst_status=request.form.get('gst_status', '').strip()
        )
        try:
            db.session.add(new_lead)
            db.session.flush()
            
            # Notification for assignment
            if assigned_to_id:
                create_notification(
                    recipient_id=assigned_to_id,
                    title="New Lead Assigned",
                    message=f"You have been assigned a new lead: {new_lead.name}",
                    link=url_for('leads.view_lead', lead_id=new_lead.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id
                )

            log_activity('lead_added', 'lead', new_lead.name, current_user.organization_id, current_user.employee.id, new_lead.id)
            db.session.commit()
            flash('Lead added!', 'leadssuccess')
            return redirect(url_for('leads.leads_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'leadserror')
            return redirect(url_for('leads.add_lead'))

    employees = Employee.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    return render_template('leads/addLead.html', employees=employees)

@leads_bp.route('/edit-lead/<int:lead_id>', methods=['GET', 'POST'])
@login_required
def edit_lead(lead_id):
    lead = Lead.query.filter_by(id=lead_id, organization_id=current_user.organization_id).first_or_404()
    if request.method == 'POST':
        lead.name = request.form.get('name', '').strip()
        lead.email = request.form.get('email', '').strip()
        lead.phone_number = re.sub(r'\D', '', request.form.get('phone', ''))
        assigned_to_id = request.form.get('assigned_to')
        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in LeadStatus}
        
        try:
            # Check if assignment changed
            old_assignee = lead.assigned_to
            lead.assigned_to = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None
            
            lead.source = source_map.get(request.form.get('source'), LeadSource.OTHER)
            lead.status = status_map.get(request.form.get('status'), LeadStatus.NEW)
            lead.company = request.form.get('company', '').strip()
            lead.address = request.form.get('address', '').strip()
            lead.city = request.form.get('city', '').strip()
            lead.notes = request.form.get('notes', '').strip()
            
            # GST Fields
            lead.gst_number = request.form.get('gst_number', '').strip()
            lead.trade_name = request.form.get('trade_name', '').strip()
            lead.state = request.form.get('state', '').strip()
            lead.pincode = request.form.get('pincode', '').strip()
            lead.business_type = request.form.get('business_type', '').strip()
            lead.gst_status = request.form.get('gst_status', '').strip()
            
            lead.updated_by = current_user.employee.id

            if lead.assigned_to and lead.assigned_to != old_assignee:
                create_notification(
                    recipient_id=lead.assigned_to,
                    title="Lead Assigned to You",
                    message=f"Lead '{lead.name}' has been assigned to you.",
                    link=url_for('leads.view_lead', lead_id=lead.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id
                )

            log_activity('lead_updated', 'lead', lead.name, current_user.organization_id, current_user.employee.id, lead.id)
            db.session.commit()
            flash('Lead updated!', 'leadssuccess')
            return redirect(url_for('leads.leads_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'leadserror')
            return redirect(url_for('leads.edit_lead', lead_id=lead_id))

    employees = Employee.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    return render_template('leads/editLead.html', lead=lead, lead_id=lead_id, employees=employees)

@leads_bp.route('/delete-lead/<int:lead_id>', methods=['POST'])
@login_required
def delete_lead(lead_id):
    lead = Lead.query.filter_by(id=lead_id, organization_id=current_user.organization_id).first_or_404()
    lead.is_deleted = True
    log_activity('lead_deleted', 'lead', lead.name, current_user.organization_id, current_user.employee.id, lead.id)
    db.session.commit()
    flash('Lead deleted.', 'leadssuccess')
    return redirect(url_for('leads.leads_list'))

@leads_bp.route('/export-leads/<string:format>')
@login_required
def export_leads(format):
    leads = Lead.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).order_by(Lead.created_at.desc()).all()
    data = []
    for l in leads:
        data.append({
            'id': str(l.id), 'name': str(l.name or ''), 'email': str(l.email or ''),
            'phone': str(l.phone_number or ''), 'company': str(l.company or '—'),
            'source': str(l.source.value if l.source else '—'), 'status': str(l.status.value if l.status else 'New'),
            'created_date': l.created_at.strftime('%Y-%m-%d') if l.created_at else '—',
            'assigned_to': l.assignee.user.username if l.assignee and l.assignee.user else 'Unassigned'
        })
    headers = ['ID', 'Name', 'Email', 'Phone', 'Company', 'Source', 'Status', 'Created_Date', 'Assigned_To']
    filename = f"leads_report_{format}.{'csv' if format == 'csv' else ('xlsx' if format == 'excel' else 'pdf')}"
    
    if format == 'csv': return export_to_csv(data, headers, filename)
    elif format == 'excel': return export_to_excel(data, headers, filename)
    elif format == 'pdf': return export_to_pdf(data, headers, filename, title="Leads Management Report")
    
    flash('Invalid format.', 'leadserror')
    return redirect(url_for('leads.leads_list'))

@leads_bp.route('/convert-lead/<int:lead_id>')
@login_required
def convert_lead(lead_id):
    lead = Lead.query.filter_by(id=lead_id, organization_id=current_user.organization_id).first_or_404()
    if lead.converted_customer:
        flash('Already converted.', 'leadserror')
        return redirect(url_for('leads.leads_list'))
    
    existing = Customer.query.filter((Customer.email == lead.email) | (Customer.phone_number == lead.phone_number)).filter_by(organization_id=current_user.organization_id, is_deleted=False).first()
    if existing:
        flash('Customer already exists with this email/phone.', 'leadserror')
        return redirect(url_for('leads.leads_list'))
    
    try:
        new_c = Customer(
            lead_id=lead.id, name=lead.name, email=lead.email, phone_number=lead.phone_number,
            address=lead.address, city=lead.city, company=lead.company, source=lead.source,
            status=CustomerStatus.NEW, notes=lead.notes, assigned_to=lead.assigned_to,
            created_by=current_user.employee.id, organization_id=current_user.organization_id,
            gst_number=lead.gst_number, trade_name=lead.trade_name,
            state=lead.state, pincode=lead.pincode,
            business_type=lead.business_type, gst_status=lead.gst_status
        )
        lead.status = LeadStatus.ACTIVE
        db.session.add(new_c)
        db.session.flush()
        
        # Notification for assignment
        if new_c.assigned_to:
            create_notification(
                recipient_id=new_c.assigned_to,
                title="New Customer Assigned",
                message=f"A lead has been converted to a customer and assigned to you: {new_c.name}",
                link=url_for('customers.view_customer', customer_id=new_c.id),
                sender_id=current_user.employee.id,
                organization_id=current_user.organization_id
            )

        log_activity('customer_added', 'customer', new_c.name, current_user.organization_id, current_user.employee.id, new_c.id, description=f"Converted Lead to Customer: {new_c.name}")
        db.session.commit()
        flash(f'Lead "{lead.name}" converted to customer!', 'leadssuccess')
        return redirect(url_for('customers.customers_list'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'leadserror')
        return redirect(url_for('leads.leads_list'))

@leads_bp.route('/add-activity/<int:lead_id>', methods=['POST'])
@login_required
def add_activity(lead_id):
    lead = Lead.query.filter_by(id=lead_id, organization_id=current_user.organization_id).first_or_404()
    activity_type = request.form.get('activity_type')
    description = request.form.get('description', '').strip()
    
    if not activity_type or not description:
        flash('Activity type and description are required.', 'leadserror')
        return redirect(url_for('leads.view_lead', lead_id=lead_id))
    
    try:
        type_map = {e.value: e for e in ActivityType}
        new_act = LeadActivity(
            lead_id=lead.id,
            activity_type=type_map.get(activity_type, ActivityType.OTHER),
            description=description,
            created_by=current_user.employee.id,
            organization_id=current_user.organization_id
        )
        db.session.add(new_act)
        db.session.commit()
        flash('Activity logged!', 'leadssuccess')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'leadserror')
        
    return redirect(url_for('leads.view_lead', lead_id=lead_id))

@leads_bp.route('/bulk-upload-leads', methods=['GET', 'POST'])
@login_required
def bulk_upload_leads():
    if request.method == 'POST':
        file = request.files.get('lead_file')
        if not file:
            flash('No file.', 'leadserror')
            return redirect(url_for('leads.bulk_upload_leads'))
        try:
            df = pd.read_csv(file).fillna('')
            col_map = {c.lower(): c for c in df.columns}
            if not {'name', 'email', 'phone'}.issubset(set(col_map.keys())):
                flash('Missing required columns.', 'leadserror')
                return redirect(url_for('leads.bulk_upload_leads'))
            
            source_map = {e.value.lower(): e for e in LeadSource}
            status_map = {e.value.lower(): e for e in LeadStatus}

            for _, row in df.iterrows():
                email_col = col_map.get('email', '')
                phone_col = col_map.get('phone', '')
                email = str(row.get(email_col, '')).strip() if email_col else ''
                phone_raw = str(row.get(phone_col, '')).strip() if phone_col else ''
                phone_number = re.sub(r'\D', '', phone_raw)
                
                if not email or not phone_number: continue
                
                existing = Lead.query.filter(
                    ((Lead.email == email) | (Lead.phone_number == phone_number)),
                    Lead.organization_id == current_user.organization_id,
                    Lead.is_deleted == False
                ).first()
                if not existing:
                    c_name = col_map.get('name', '')
                    c_comp = col_map.get('company', '')
                    c_addr = col_map.get('address', '')
                    c_city = col_map.get('city', '')
                    c_src = col_map.get('source', '')
                    c_stat = col_map.get('status', '')

                    s_val = str(row.get(c_src, '')).strip().lower() if c_src else ''
                    st_val = str(row.get(c_stat, '')).strip().lower() if c_stat else ''

                    new_l = Lead(
                        name=str(row.get(c_name, '')).strip() if c_name else 'Unknown',
                        email=email,
                        phone_number=phone_number,
                        company=str(row.get(c_comp, '')).strip() if c_comp else None,
                        address=str(row.get(c_addr, '')).strip() if c_addr else None,
                        city=str(row.get(c_city, '')).strip() if c_city else None,
                        source=source_map.get(s_val, LeadSource.OTHER),
                        status=status_map.get(st_val, LeadStatus.NEW),
                        organization_id=current_user.organization_id,
                        created_by=current_user.employee.id
                    )
                    db.session.add(new_l)
            db.session.commit()
            flash('Leads uploaded!', 'leadssuccess')
            return redirect(url_for('leads.leads_list'))
        except Exception as e:
            flash(f'Error: {str(e)}', 'leadserror')
    return render_template('leads/bulkuploadleads.html')

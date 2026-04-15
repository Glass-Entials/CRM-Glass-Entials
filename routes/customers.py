import os
import re
from flask import Blueprint, render_template, request, flash, redirect, url_for, Response, send_file, current_app
from flask_login import login_required, current_user
from model import db, Customer, Employee, LeadSource, CustomerStatus, CustomerDocument
from utils.exports import export_to_csv, export_to_excel, export_to_pdf
import pandas as pd
from werkzeug.utils import secure_filename
import uuid
from datetime import datetime
from utils.activity import log_activity
from utils.notifications import create_notification

customers_bp = Blueprint('customers', __name__)

@customers_bp.route('/customers')
@login_required
def customers_list():
    org_id = current_user.organization_id
    status_filter = request.args.get('status')
    
    query = Customer.query.filter_by(organization_id=org_id, is_deleted=False)
    
    if status_filter:
        # Match enum value
        status_map = {e.value: e for e in CustomerStatus}
        if status_filter in status_map:
            query = query.filter(Customer.status == status_map[status_filter])
            
    all_customers = query.order_by(Customer.created_at.desc()).all()
    all_employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    unique_cities = sorted(list(set(c.city for c in all_customers if c.city)))
    
    return render_template('customer/customer.html', 
                         customers=all_customers, 
                         employees=all_employees, 
                         cities=unique_cities, 
                         current_status=status_filter,
                         CustomerStatus=CustomerStatus)

@customers_bp.route('/export-customers/<string:format>')
@login_required
def export_customer(format):
    org_id = current_user.organization_id
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Customer.created_at.desc()).all()
    
    data = []
    for c in customers:
        data.append({
            'id': str(c.id),
            'name': str(c.name or ''),
            'email': str(c.email or ''),
            'phone': str(c.phone_number or ''),
            'address': str(c.address or '—'),
            'city': str(c.city or '—'),
            'company': str(c.company or '—'),
            'source': str(c.source.value if c.source else '—'),
            'status': str(c.status.value if c.status else 'New'),
            'created_date': c.created_at.strftime('%Y-%m-%d') if c.created_at else '—',
            'updated_date': c.updated_at.strftime('%Y-%m-%d') if c.updated_at else '—',
            'assigned_to': c.assignee.user.username if c.assignee and c.assignee.user else 'Unassigned',
            'created_by': c.creator.user.username if c.creator and c.creator.user else 'Unknown',
            'updated_by': c.updater.user.username if c.updater and c.updater.user else 'Unknown'
        })
    
    headers = ['ID', 'Name', 'Email', 'Phone', 'Address', 'City', 'Company', 'Source', 'Status', 'Created_Date', 'Updated_Date', 'Assigned_To', 'Created_By', 'Updated_By']
    filename = f"customers_report_{format}.{'csv' if format == 'csv' else ('xlsx' if format == 'excel' else 'pdf')}"
    
    if format == 'csv':
        return export_to_csv(data, headers, filename)
    elif format == 'excel':
        return export_to_excel(data, headers, filename)
    elif format == 'pdf':
        return export_to_pdf(data, headers, filename, title="Customer Management Report")
    
    flash('Invalid format or export error.', 'customererror')
    return redirect(url_for('customers.customers_list'))

@customers_bp.route('/add-customer', methods=['GET', 'POST'])
@login_required
def add_customer():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))
        address = request.form.get('address', '').strip()
        city = request.form.get('city', '').strip()
        company = request.form.get('company', '').strip()
        source = request.form.get('source', '').strip()
        status = request.form.get('status', 'New')
        notes = request.form.get('notes', '').strip()
        assigned_to_id = request.form.get('assigned_to')
        
        assigned_to_id = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None

        if not all([name, email, phone_number]) or len(phone_number) != 10:
            flash('All primary fields required and phone must be 10 digits.', 'customererror')
            return redirect(url_for('customers.add_customer'))

        existing = Customer.query.filter(
            ((Customer.email == email) | (Customer.phone_number == phone_number)),
            Customer.organization_id == current_user.organization_id,
            Customer.is_deleted == False
        ).first()

        if existing:
            flash('Customer already exists with this email or phone number in your organization.', 'customererror')
            return redirect(url_for('customers.add_customer'))

        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in CustomerStatus}
        
        new_customer = Customer(
            name=name, email=email, phone_number=phone_number,
            address=address, city=city, company=company,
            source=source_map.get(source, LeadSource.OTHER),
            status=status_map.get(status, CustomerStatus.REQUIREMENT_UNDERSTOOD),
            notes=notes, created_by=current_user.employee.id,
            assigned_to=assigned_to_id, organization_id=current_user.organization_id,
            # GST Fields
            gst_number=request.form.get('gst_number', '').strip(),
            trade_name=request.form.get('trade_name', '').strip(),
            state=request.form.get('state', '').strip(),
            pincode=request.form.get('pincode', '').strip(),
            business_type=request.form.get('business_type', '').strip(),
            gst_status=request.form.get('gst_status', '').strip()
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
                    link=url_for('customers.view_customer', customer_id=new_customer.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id
                )

            log_activity('customer_added', 'customer', new_customer.name, current_user.organization_id, current_user.employee.id, new_customer.id)
            db.session.commit()
            flash('Customer added successfully!', 'customersuccess')
            return redirect(url_for('customers.customers_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving: {str(e)}', 'customererror')
            return redirect(url_for('customers.add_customer'))

    employees = Employee.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    return render_template('customer/addcustomer.html', employees=employees)

@customers_bp.route('/edit-customer/<int:customer_id>', methods=['GET', 'POST'])
@login_required
def edit_customer(customer_id):
    customer = Customer.query.filter_by(id=customer_id, organization_id=current_user.organization_id).first_or_404()
    if request.method == 'POST':
        customer.name = request.form.get('name', '').strip()
        customer.email = request.form.get('email', '').strip()
        customer.phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))
        customer.address = request.form.get('address', '').strip()
        customer.city = request.form.get('city', '').strip()
        customer.company = request.form.get('company', '').strip()
        
        assigned_to_id = request.form.get('assigned_to')
        source_map = {e.value: e for e in LeadSource}
        status_map = {e.value: e for e in CustomerStatus}
        
        try:
            # Check if assignment changed
            old_assignee = customer.assigned_to
            customer.assigned_to = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None
            
            customer.source = source_map.get(request.form.get('source'), LeadSource.OTHER)
            customer.status = status_map.get(request.form.get('status'), CustomerStatus.NEW)
            customer.notes = request.form.get('notes', '').strip()
            
            # GST Fields
            customer.gst_number = request.form.get('gst_number', '').strip()
            customer.trade_name = request.form.get('trade_name', '').strip()
            customer.state = request.form.get('state', '').strip()
            customer.pincode = request.form.get('pincode', '').strip()
            customer.business_type = request.form.get('business_type', '').strip()
            customer.gst_status = request.form.get('gst_status', '').strip()
            
            customer.updated_by = current_user.employee.id

            if customer.assigned_to and customer.assigned_to != old_assignee:
                create_notification(
                    recipient_id=customer.assigned_to,
                    title="Customer Assigned to You",
                    message=f"Customer '{customer.name}' has been assigned to you.",
                    link=url_for('customers.view_customer', customer_id=customer.id),
                    sender_id=current_user.employee.id,
                    organization_id=current_user.organization_id
                )

            log_activity('customer_updated', 'customer', customer.name, current_user.organization_id, current_user.employee.id, customer.id)
            db.session.commit()
            flash('Customer updated successfully!', 'customersuccess')
            return redirect(url_for('customers.customers_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'customererror')
            return redirect(url_for('customers.edit_customer', customer_id=customer_id))

    employees = Employee.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    return render_template('customer/editcustomer.html', customer=customer, customer_id=customer_id, employees=employees)

@customers_bp.route('/delete-customer/<int:customer_id>', methods=['POST'])
@login_required
def delete_customer(customer_id):
    customer = Customer.query.filter_by(id=customer_id, organization_id=current_user.organization_id).first_or_404()
    customer.is_deleted = True
    log_activity('customer_deleted', 'customer', customer.name, current_user.organization_id, current_user.employee.id, customer.id)
    db.session.commit()
    flash('Customer deleted.', 'customersuccess')
    return redirect(url_for('customers.customers_list'))

@customers_bp.route('/view-customer/<int:customer_id>')
@login_required
def view_customer(customer_id):
    customer = Customer.query.filter_by(id=customer_id, organization_id=current_user.organization_id).first_or_404()
    return render_template('customer/customer_profile.html', customer=customer)

@customers_bp.route('/bulk-upload', methods=['GET', 'POST'])
@login_required
def bulk_upload():
    if request.method == 'POST':
        file = request.files.get('customer_file')
        if not file:
            flash('No file.', 'customererror')
            return redirect(url_for('customers.bulk_upload'))
        try:
            df = pd.read_csv(file).fillna('')
            col_map = {c.lower(): c for c in df.columns}
            if not {'name', 'email', 'phone'}.issubset(set(col_map.keys())):
                flash('Missing required columns.', 'customererror')
                return redirect(url_for('customers.bulk_upload'))
            
            source_map = {e.value.lower(): e for e in LeadSource}
            status_map = {e.value.lower(): e for e in CustomerStatus}
            
            for _, row in df.iterrows():
                email_col = col_map.get('email', '')
                phone_col = col_map.get('phone', '')
                email = str(row.get(email_col, '')).strip() if email_col else ''
                phone_raw = str(row.get(phone_col, '')).strip() if phone_col else ''
                phone_number = re.sub(r'\D', '', phone_raw)
                
                if not email or not phone_number: continue
                
                existing = Customer.query.filter(
                    ((Customer.email == email) | (Customer.phone_number == phone_number)),
                    Customer.organization_id == current_user.organization_id,
                    Customer.is_deleted == False
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

                    new_c = Customer(
                        name=str(row.get(c_name, '')).strip() if c_name else 'Unknown',
                        email=email,
                        phone_number=phone_number,
                        company=str(row.get(c_comp, '')).strip() if c_comp else None,
                        address=str(row.get(c_addr, '')).strip() if c_addr else None,
                        city=str(row.get(c_city, '')).strip() if c_city else None,
                        source=source_map.get(s_val, LeadSource.OTHER),
                        status=status_map.get(st_val, CustomerStatus.REQUIREMENT_UNDERSTOOD),
                        organization_id=current_user.organization_id,
                        created_by=current_user.employee.id
                    )
                    db.session.add(new_c)
            db.session.commit()
            flash('Bulk upload completed!', 'customersuccess')
            return redirect(url_for('customers.customers_list'))
        except Exception as e:
            flash(f'Upload error: {str(e)}', 'customererror')
    return render_template('customer/bulkuploadcustomer.html')

@customers_bp.route('/upload-document/<int:customer_id>', methods=['POST'])
@login_required
def upload_document(customer_id):
    customer = Customer.query.filter_by(id=customer_id, organization_id=current_user.organization_id).first_or_404()
    
    if 'document' not in request.files:
        flash('No file part', 'customererror')
        return redirect(url_for('customers.view_customer', customer_id=customer_id))
    
    file = request.files['document']
    if file.filename == '':
        flash('No selected file', 'customererror')
        return redirect(url_for('customers.view_customer', customer_id=customer_id))
    
    if file:
        original_filename = secure_filename(file.filename)
        extension = original_filename.rsplit('.', 1)[1].lower() if '.' in original_filename else ''
        
        # Unique filename to avoid collisions
        unique_filename = f"{uuid.uuid4().hex}.{extension}"
        
        upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', 'customer_docs')
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder, exist_ok=True)
            
        file_path = os.path.join(upload_folder, unique_filename)
        file.save(file_path)
        
        new_doc = CustomerDocument(
            customer_id=customer.id,
            filename=unique_filename,
            original_name=original_filename,
            file_type=extension,
            organization_id=current_user.organization_id
        )
        
        db.session.add(new_doc)
        db.session.flush()
        log_activity('document_uploaded', 'document', f"{original_filename} (Customer: {customer.name})", current_user.organization_id, current_user.employee.id, new_doc.id)
        db.session.commit()
        
        flash(f'Document "{original_filename}" uploaded successfully!', 'customersuccess')
        return redirect(url_for('customers.view_customer', customer_id=customer_id))

@customers_bp.route('/download-document/<int:doc_id>')
@login_required
def download_document(doc_id):
    doc = CustomerDocument.query.filter_by(id=doc_id, organization_id=current_user.organization_id).first_or_404()
    file_path = os.path.join(current_app.root_path, 'static', 'uploads', 'customer_docs', doc.filename)
    
    if not os.path.exists(file_path):
        flash('File not found on server.', 'customererror')
        return redirect(url_for('customers.view_customer', customer_id=doc.customer_id))
        
    return send_file(file_path, as_attachment=True, download_name=doc.original_name)

@customers_bp.route('/delete-document/<int:doc_id>', methods=['POST'])
@login_required
def delete_document(doc_id):
    doc = CustomerDocument.query.filter_by(id=doc_id, organization_id=current_user.organization_id).first_or_404()
    customer_id = doc.customer_id
    
    file_path = os.path.join(current_app.root_path, 'static', 'uploads', 'customer_docs', doc.filename)
    
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
        
        log_activity('document_deleted', 'document', doc.original_name, current_user.organization_id, current_user.employee.id, doc.id)
        db.session.delete(doc)
        db.session.commit()
        flash('Document deleted successfully.', 'customersuccess')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting document: {str(e)}', 'customererror')
        
    return redirect(url_for('customers.view_customer', customer_id=customer_id))


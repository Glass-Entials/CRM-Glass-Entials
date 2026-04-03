from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required, current_user
from model import db, Customer, Project, Invoice, InvoiceItem, InvoiceStatus, Employee, ActivityLog, Quotation, QuotationItem, QuotationStatus, Lead
import datetime
import random
import string

accounts = Blueprint('accounts', __name__)

def generate_invoice_number():
    prefix = "INV-"
    year = datetime.datetime.now().strftime("%y")
    random_str = ''.join(random.choices(string.digits, k=4))
    return f"{prefix}{year}{random_str}"

@accounts.route('/invoices')
@login_required
def invoice_list():
    all_invoices = Invoice.query.filter_by(
        organization_id=current_user.organization_id
    ).order_by(Invoice.created_at.desc()).all()
    
    return render_template('accounts/invoice_list.html', invoices=all_invoices)

@accounts.route('/add-invoice', methods=['GET', 'POST'])
@login_required
def add_invoice():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        project_id = request.form.get('project_id')
        issue_date_str = request.form.get('issue_date')
        due_date_str = request.form.get('due_date')
        notes = request.form.get('notes')
        
        # Items processing
        descriptions = request.form.getlist('description[]')
        quantities = request.form.getlist('quantity[]')
        rates = request.form.getlist('rate[]')
        
        if not customer_id or not descriptions:
            flash("Please fill in all required fields.", "error")
            return redirect(url_for('accounts.add_invoice'))
        
        try:
            issue_date = datetime.datetime.strptime(issue_date_str, '%Y-%m-%d')
            due_date = datetime.datetime.strptime(due_date_str, '%Y-%m-%d') if due_date_str else None
            
            invoice = Invoice(
                invoice_number=generate_invoice_number(),
                customer_id=customer_id,
                project_id=project_id if project_id else None,
                organization_id=current_user.organization_id,
                issue_date=issue_date,
                due_date=due_date,
                notes=notes,
                created_by=current_user.employee.id,
                status=InvoiceStatus.UNPAID
            )
            
            db.session.add(invoice)
            db.session.flush() # To get invoice ID
            
            total_amount = 0
            for desc, qty, rate in zip(descriptions, quantities, rates):
                if not desc: continue
                q = float(qty or 0)
                r = float(rate or 0)
                amt = q * r
                item = InvoiceItem(
                    invoice_id=invoice.id,
                    description=desc,
                    quantity=q,
                    rate=r,
                    amount=amt
                )
                db.session.add(item)
                total_amount += amt
            
            # Simple GST calculation (assuming 18%)
            gst_rate = 0.18
            invoice.amount = total_amount
            invoice.gst_amount = total_amount * gst_rate
            invoice.total_amount = total_amount * (1 + gst_rate)
            
            # Log activity
            log = ActivityLog(
                action='invoice_created',
                entity_type='invoice',
                entity_id=invoice.id,
                entity_name=invoice.invoice_number,
                description=f"Created invoice {invoice.invoice_number} for customer {invoice.customer.name}",
                actor_id=current_user.employee.id,
                organization_id=current_user.organization_id
            )
            db.session.add(log)
            
            db.session.commit()
            flash(f"Invoice {invoice.invoice_number} created successfully!", "success")
            return redirect(url_for('accounts.invoice_list'))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating invoice: {str(e)}", "error")
            return redirect(url_for('accounts.add_invoice'))

    customers = Customer.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    
    return render_template('accounts/add_invoice.html', customers=customers, projects=projects, today=datetime.date.today())

@accounts.route('/view-invoice/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    invoice = Invoice.query.filter_by(id=invoice_id, organization_id=current_user.organization_id).first_or_404()
    return render_template('accounts/invoice_view.html', invoice=invoice)

def generate_quotation_number():
    prefix = "QT-"
    year = datetime.datetime.now().strftime("%y")
    random_str = ''.join(random.choices(string.digits, k=4))
    return f"{prefix}{year}{random_str}"

@accounts.route('/quotations')
@login_required
def quotation_list():
    all_quotations = Quotation.query.filter_by(
        organization_id=current_user.organization_id
    ).order_by(Quotation.created_at.desc()).all()
    
    return render_template('accounts/quotation_list.html', quotations=all_quotations)

@accounts.route('/add-quotation', methods=['GET', 'POST'])
@login_required
def add_quotation():
    if request.method == 'POST':
        customer_id = request.form.get('customer_id')
        lead_id = request.form.get('lead_id')
        project_id = request.form.get('project_id')
        issue_date_str = request.form.get('issue_date')
        due_date_str = request.form.get('due_date')
        notes = request.form.get('notes')
        terms_conditions = request.form.get('terms_conditions')
        source = request.form.get('source')
        amendment_no = request.form.get('amendment_no')
        measurements = request.form.get('measurements')
        quote_level = request.form.get('quote_level')
        
        # Items processing
        descriptions = request.form.getlist('description[]')
        widths = request.form.getlist('width[]')
        heights = request.form.getlist('height[]')
        quantities = request.form.getlist('quantity[]')
        units = request.form.getlist('unit[]')
        gst_rates = request.form.getlist('gst_rate[]')
        rates = request.form.getlist('rate[]')
        
        if not (customer_id or lead_id) or not descriptions:
            flash("Please provide a client and at least one item.", "error")
            return redirect(url_for('accounts.add_quotation'))
        
        try:
            issue_date = datetime.datetime.strptime(issue_date_str, '%Y-%m-%d') if issue_date_str else datetime.datetime.now()
            due_date = datetime.datetime.strptime(due_date_str, '%Y-%m-%d') if due_date_str else None
            
            quotation = Quotation(
                quotation_number=generate_quotation_number(),
                customer_id=customer_id if customer_id else None,
                lead_id=lead_id if lead_id else None,
                project_id=project_id if project_id else None,
                organization_id=current_user.organization_id,
                issue_date=issue_date,
                due_date=due_date,
                source=source,
                amendment_no=amendment_no,
                measurements=measurements,
                quote_level=quote_level,
                notes=notes,
                terms_conditions=terms_conditions,
                created_by=current_user.employee.id,
                status=QuotationStatus.DRAFT
            )
            
            db.session.add(quotation)
            db.session.flush() # Get ID
            
            subtotal = 0
            total_gst = 0
            
            for i in range(len(descriptions)):
                if not descriptions[i]: continue
                desc = descriptions[i]
                w = float(widths[i] or 0)
                h = float(heights[i] or 0)
                qty = float(quantities[i] or 1)
                unit = units[i] if i < len(units) else 'Sq.Ft'
                gst = float(gst_rates[i] or 18.0)
                rate = float(rates[i] or 0)
                
                # Calculate subtotal based on Area (w*h) * qty * rate OR just qty * rate if dimensions not given
                if w > 0 and h > 0:
                    area = (w * h) # Assumes standard unit conversion or raw calculation
                    amount = area * qty * rate
                else:
                    amount = qty * rate
                    
                item_gst = amount * (gst / 100)
                item_total = amount + item_gst
                
                item = QuotationItem(
                    quotation_id=quotation.id,
                    description=desc,
                    width=w,
                    height=h,
                    quantity=qty,
                    gst_percentage=gst,
                    unit=unit,
                    rate=rate,
                    amount=amount,
                    total=item_total
                )
                db.session.add(item)
                subtotal += amount
                total_gst += item_gst
            
            quotation.subtotal = subtotal
            quotation.gst_amount = total_gst
            quotation.total_amount = subtotal + total_gst
            
            # Log activity
            log = ActivityLog(
                action='quotation_created',
                entity_type='quotation',
                entity_id=quotation.id,
                entity_name=quotation.quotation_number,
                description=f"Created quotation {quotation.quotation_number}",
                actor_id=current_user.employee.id,
                organization_id=current_user.organization_id
            )
            db.session.add(log)
            
            db.session.commit()
            flash(f"Quotation {quotation.quotation_number} created successfully!", "success")
            return redirect(url_for('accounts.quotation_list'))
            
        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            flash(f"Error creating quotation: {str(e)}", "error")
            return redirect(url_for('accounts.add_quotation'))

    # If GET request, render the form
    from model import Customer, Project, Lead
    customers = Customer.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    leads = Lead.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    
    return render_template('accounts/add_quotation.html', customers=customers, leads=leads, projects=projects, today=datetime.date.today())

@accounts.route('/view-quotation/<int:quotation_id>')
@login_required
def view_quotation(quotation_id):
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=current_user.organization_id).first_or_404()
    return render_template('accounts/quotation_view.html', quotation=quotation)

@accounts.route('/update-quotation-status/<int:quotation_id>', methods=['POST'])
@login_required
def update_quotation_status(quotation_id):
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=current_user.organization_id).first_or_404()
    new_status = request.form.get('status')
    
    try:
        if new_status:
            quotation.status = QuotationStatus(new_status)
            db.session.commit()
            return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)})
    return jsonify({'success': False, 'error': 'Invalid status'})

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



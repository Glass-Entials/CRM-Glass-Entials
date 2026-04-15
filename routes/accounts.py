from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from flask_login import login_required, current_user
from model import (db, Customer, Project, Invoice, InvoiceItem, InvoiceStatus,
                    Employee, ActivityLog, QuotationSettings, Lead)
from utils.number_words import number_to_words
import datetime, os, uuid, json
from collections import defaultdict

accounts = Blueprint('accounts', __name__)

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _get_or_create_settings(org_id):
    """Fetch quotation settings for org, creating defaults if missing."""
    settings = QuotationSettings.query.filter_by(organization_id=org_id).first()
    if not settings:
        settings = QuotationSettings(organization_id=org_id)
        db.session.add(settings)
        db.session.commit()
    return settings

def generate_invoice_number(org_id):
    """Generate next Invoice number. Simple implementation for now."""
    prefix = "INV"
    year = datetime.datetime.now().strftime("%y")
    # Count existing invoices for this org this year to get sequence
    count = Invoice.query.filter(
        Invoice.organization_id == org_id,
        Invoice.invoice_number.like(f"{prefix}/{year}/%")
    ).count()
    return f"{prefix}/{year}/{(count + 1):04d}"

def _calc_invoice_totals(items_data, total_discount, total_discount_type,
                        additional_charges, additional_charges_taxable, is_igst):
    subtotal       = 0.0
    total_quantity = 0.0
    tax_buckets    = defaultdict(float)

    items_out = []
    for it in items_data:
        qty = float(it.get('quantity') or 1)
        rate = float(it.get('rate') or 0)
        c_qty = it.get('chargeable_quantity')
        
        if c_qty is not None and str(c_qty).strip() != '':
            base = float(c_qty) * rate
        else:
            base = qty * rate

        disc = float(it.get('discount') or 0)
        disc_type = it.get('discount_type', 'flat')
        gst  = float(it.get('gst_percentage') or 18)

        if disc_type == 'percent':
            discount_amt = base * disc / 100
        else:
            discount_amt = disc

        amount = max(base - discount_amt, 0)
        gst_amount = amount * gst / 100
        total_item = amount + gst_amount

        items_out.append({**it, 'amount': amount, 'gst_amount': gst_amount, 'total': total_item})
        subtotal       += amount
        total_quantity += qty
        tax_buckets[gst] += amount

    if total_discount_type == 'percent':
        disc_amt = subtotal * float(total_discount or 0) / 100
    else:
        disc_amt = float(total_discount or 0)
    taxable_base = max(subtotal - disc_amt, 0)

    charges = float(additional_charges or 0)
    if additional_charges_taxable:
        taxable_total = taxable_base + charges
    else:
        taxable_total = taxable_base

    sgst = cgst = igst = 0.0
    for rate, taxable in tax_buckets.items():
        scale = taxable_base / subtotal if subtotal else 1
        scaled = taxable * scale
        if additional_charges_taxable:
            scaled += charges * (taxable / subtotal if subtotal else 0)

        if is_igst:
            igst += scaled * rate / 100
        else:
            sgst += scaled * (rate / 2) / 100
            cgst += scaled * (rate / 2) / 100

    gst_amount   = sgst + cgst + igst
    total_amount = taxable_base + gst_amount + (0 if additional_charges_taxable else charges)

    return {
        'subtotal': subtotal,
        'items': items_out,
        'total_quantity': total_quantity,
        'sgst': sgst, 'cgst': cgst, 'igst': igst,
        'gst_amount': gst_amount,
        'total_amount': total_amount,
    }

def _save_invoice_from_form(invoice, form, org_id, employee_id):
    invoice.invoice_title = form.get('invoice_title', 'Tax Invoice')
    
    issue_date_str = form.get('issue_date')
    due_date_str   = form.get('due_date')
    invoice.issue_date = (datetime.datetime.strptime(issue_date_str, '%Y-%m-%d')
                          if issue_date_str else datetime.datetime.now())
    invoice.due_date   = (datetime.datetime.strptime(due_date_str, '%Y-%m-%d')
                          if due_date_str else None)

    invoice.customer_id = form.get('customer_id') or None
    invoice.project_id  = form.get('project_id')  or None

    invoice.total_discount      = float(form.get('total_discount') or 0)
    invoice.total_discount_type = form.get('total_discount_type', 'flat')
    invoice.additional_charges  = float(form.get('additional_charges') or 0)
    invoice.additional_charges_taxable = (form.get('additional_charges_taxable') == '1')
    invoice.additional_charges_label   = form.get('additional_charges_label') or None
    invoice.is_igst             = (form.get('is_igst') == '1')

    invoice.notes            = form.get('notes') or None
    invoice.terms_conditions = form.get('terms_conditions') or None
    invoice.signature_label  = form.get('signature_label', 'Authorised Signatory')

    status_val = form.get('status', 'Unpaid')
    try:
        invoice.status = InvoiceStatus(status_val)
    except ValueError:
        invoice.status = InvoiceStatus.UNPAID

    # Clear items
    InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()

    item_names      = form.getlist('item_name[]')
    descriptions    = form.getlist('description[]')
    group_names     = form.getlist('group_name[]')
    formula_types   = form.getlist('formula_type[]')
    widths          = form.getlist('width[]')
    heights         = form.getlist('height[]')
    quantities      = form.getlist('quantity[]')
    chargeable_qtys = form.getlist('chargeable_quantity[]')
    units           = form.getlist('unit[]')
    rates           = form.getlist('rate[]')
    discounts       = form.getlist('discount[]')
    discount_types  = form.getlist('discount_type[]')
    gst_rates       = form.getlist('gst_rate[]')

    items_data = []
    for i in range(len(item_names)):
        name = item_names[i].strip() if i < len(item_names) else ''
        if not name: continue
        
        c_qty_val = None
        if i < len(chargeable_qtys) and chargeable_qtys[i]:
            c_qty_val = float(chargeable_qtys[i])
            
        items_data.append({
            'sort_order': i,
            'group_name': group_names[i].strip() if i < len(group_names) else '',
            'item_name': name,
            'description': descriptions[i].strip() if i < len(descriptions) else '',
            'width': float(widths[i]) if i < len(widths) and widths[i] else 0,
            'height': float(heights[i]) if i < len(heights) and heights[i] else 0,
            'formula_type': formula_types[i].strip() if i < len(formula_types) else 'standard',
            'quantity': float(quantities[i]) if i < len(quantities) and quantities[i] else 1,
            'chargeable_quantity': c_qty_val,
            'unit': units[i] if i < len(units) else 'Sq.Ft',
            'rate': float(rates[i]) if i < len(rates) and rates[i] else 0,
            'discount': float(discounts[i]) if i < len(discounts) and discounts[i] else 0,
            'discount_type': discount_types[i] if i < len(discount_types) else 'flat',
            'gst_percentage': float(gst_rates[i]) if i < len(gst_rates) and gst_rates[i] else 18,
        })

    totals = _calc_invoice_totals(
        items_data,
        invoice.total_discount,
        invoice.total_discount_type,
        invoice.additional_charges,
        invoice.additional_charges_taxable,
        invoice.is_igst
    )

    for it in totals['items']:
        gst = it['gst_percentage']
        half = gst / 2
        item = InvoiceItem(
            invoice_id = invoice.id,
            sort_order = it['sort_order'],
            group_name = it.get('group_name'),
            item_name = it['item_name'],
            description = it['description'],
            formula_type = it.get('formula_type'),
            width = it['width'],
            height = it['height'],
            quantity = it['quantity'],
            chargeable_quantity = it.get('chargeable_quantity'),
            unit = it['unit'],
            rate = it['rate'],
            discount = it['discount'],
            discount_type = it['discount_type'],
            gst_percentage = gst,
            sgst_rate = 0 if invoice.is_igst else half,
            cgst_rate = 0 if invoice.is_igst else half,
            igst_rate = gst if invoice.is_igst else 0,
            amount = it['amount'],
            gst_amount = it['gst_amount'],
            total = it['total'],
        )
        db.session.add(item)

    invoice.subtotal       = totals['subtotal']
    invoice.amount         = totals['subtotal'] - invoice.total_discount # Taxable base
    invoice.total_quantity = totals['total_quantity']
    invoice.sgst           = totals['sgst']
    invoice.cgst           = totals['cgst']
    invoice.igst           = totals['igst']
    invoice.gst_amount     = totals['gst_amount']
    invoice.total_amount   = totals['total_amount']
    invoice.total_in_words = number_to_words(totals['total_amount'])

# ─────────────────────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────────────────────

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
    org_id = current_user.organization_id
    emp = current_user.employee

    if request.method == 'POST':
        try:
            cust_id = request.form.get('customer_id')
            if not cust_id:
                raise Exception("Please select a Customer.")

            invoice = Invoice(
                invoice_number = generate_invoice_number(org_id),
                organization_id = org_id,
                created_by = emp.id,
                customer_id = cust_id
            )
            db.session.add(invoice)
            db.session.flush()

            _save_invoice_from_form(invoice, request.form, org_id, emp.id)

            log = ActivityLog(
                action='invoice_created',
                entity_type='invoice',
                entity_id=invoice.id,
                entity_name=invoice.invoice_number,
                description=f"Created invoice {invoice.invoice_number}",
                actor_id=emp.id,
                organization_id=org_id
            )
            db.session.add(log)
            db.session.commit()
            flash(f"✅ Invoice {invoice.invoice_number} created!", "success")
            return redirect(url_for('accounts.view_invoice', invoice_id=invoice.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error creating invoice: {str(e)}", "error")

    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    settings = _get_or_create_settings(org_id)
    next_number = generate_invoice_number(org_id)
    
    return render_template('accounts/add_invoice.html', 
                           customers=customers, 
                           projects=projects, 
                           settings=settings,
                           next_number=next_number,
                           today=datetime.date.today(),
                           mode='create',
                           invoice=None)

@accounts.route('/edit-invoice/<int:invoice_id>', methods=['GET', 'POST'])
@login_required
def edit_invoice(invoice_id):
    org_id = current_user.organization_id
    emp = current_user.employee
    invoice = Invoice.query.filter_by(id=invoice_id, organization_id=org_id).first_or_404()

    if request.method == 'POST':
        try:
            _save_invoice_from_form(invoice, request.form, org_id, emp.id)
            
            log = ActivityLog(
                action='invoice_updated',
                entity_type='invoice',
                entity_id=invoice.id,
                entity_name=invoice.invoice_number,
                description=f"Updated invoice {invoice.invoice_number}",
                actor_id=emp.id,
                organization_id=org_id
            )
            db.session.add(log)
            db.session.commit()
            flash(f"✅ Invoice {invoice.invoice_number} updated!", "success")
            return redirect(url_for('accounts.view_invoice', invoice_id=invoice.id))
            
        except Exception as e:
            db.session.rollback()
            import traceback; traceback.print_exc()
            flash(f"Error updating invoice: {str(e)}", "error")

    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    settings = _get_or_create_settings(org_id)
    
    return render_template('accounts/add_invoice.html', 
                           customers=customers, 
                           projects=projects, 
                           settings=settings,
                           invoice=invoice,
                           today=datetime.date.today(),
                           mode='edit')

@accounts.route('/view-invoice/<int:invoice_id>')
@login_required
def view_invoice(invoice_id):
    org_id = current_user.organization_id
    invoice = Invoice.query.filter_by(id=invoice_id, organization_id=org_id).first_or_404()
    settings = _get_or_create_settings(org_id)
    return render_template('accounts/invoice_view.html', invoice=invoice, settings=settings)

@accounts.route('/api/calculate', methods=['POST'])
@login_required
def api_calculate():
    """AJAX endpoint for real-time totals calculation."""
    try:
        data = request.get_json()
        totals = _calc_invoice_totals(
            data.get('items', []),
            data.get('total_discount', 0),
            data.get('total_discount_type', 'flat'),
            data.get('additional_charges', 0),
            data.get('additional_charges_taxable', False),
            data.get('is_igst', False),
        )
        totals['words'] = number_to_words(totals['total_amount'])
        return jsonify({'success': True, 'result': totals})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

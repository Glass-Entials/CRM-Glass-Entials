from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, current_app)
from flask_login import login_required, current_user
from model import (db, Customer, Project, Lead, Employee,
                   Quotation, QuotationItem, QuotationStatus, QuotationDocType,
                   QuotationSettings, QuotationCustomField, QuotationCustomFieldValue,
                   QuotationTermGroup, QuotationTerm, QuotationTermLink,
                   QuotationAttachment, QuotationSignature, QuotationTaxSummary,
                   ActivityLog)
from utils.number_words import number_to_words
import datetime, os, uuid, json, base64
from collections import defaultdict

quotations_bp = Blueprint('quotations', __name__, url_prefix='/quotations')

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


def generate_quotation_number(org_id):
    """Generate next GL quotation number. Thread-safe via DB lock."""
    settings = _get_or_create_settings(org_id)
    prefix = settings.number_prefix or 'GL'
    year   = datetime.datetime.now().strftime('%y')
    seq    = settings.number_counter
    number = f"{prefix}/{year}/{seq:04d}"
    settings.number_counter = seq + 1
    db.session.flush()
    return number


def _calc_totals(items_data, total_discount, total_discount_type,
                 additional_charges, additional_charges_taxable, is_igst):
    """
    Recalculate all financial totals from raw item dicts.
    Returns a dict with: subtotal, items_with_tax, sgst, cgst, igst,
    gst_amount, taxable_for_additional, total_amount, total_quantity,
    tax_summary_rows
    """
    subtotal       = 0.0
    total_quantity = 0.0
    tax_buckets    = defaultdict(float)  # gst_rate → taxable_amount

    items_out = []
    for it in items_data:
        qty = float(it.get('quantity') or 1)
        rate = float(it.get('rate') or 0)
        c_qty = it.get('chargeable_quantity')
        
        if c_qty is not None and str(c_qty).strip() != '':
            base = float(c_qty) * rate
        else:
            base = qty * rate # fallback

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

    # Total-level discount
    if total_discount_type == 'percent':
        disc_amt = subtotal * float(total_discount or 0) / 100
    else:
        disc_amt = float(total_discount or 0)
    taxable_base = max(subtotal - disc_amt, 0)

    # Additional charges
    charges = float(additional_charges or 0)
    if additional_charges_taxable:
        # Add charges to taxable base (apply at 18% default)
        taxable_total = taxable_base + charges
    else:
        taxable_total = taxable_base

    # GST split
    sgst = cgst = igst = 0.0
    tax_summary_rows = []
    for rate, taxable in tax_buckets.items():
        # Scale taxable to account for discount
        scale = taxable_base / subtotal if subtotal else 1
        scaled = taxable * scale
        if additional_charges_taxable:
            scaled += charges * (taxable / subtotal if subtotal else 0)

        if is_igst:
            i = scaled * rate / 100
            igst += i
            tax_summary_rows.append({'gst_rate': rate, 'taxable_amount': scaled,
                                     'sgst_amount': 0, 'cgst_amount': 0, 'igst_amount': i,
                                     'total_tax': i})
        else:
            s = scaled * (rate / 2) / 100
            c = scaled * (rate / 2) / 100
            sgst += s; cgst += c
            tax_summary_rows.append({'gst_rate': rate, 'taxable_amount': scaled,
                                     'sgst_amount': s, 'cgst_amount': c, 'igst_amount': 0,
                                     'total_tax': s + c})

    gst_amount   = sgst + cgst + igst
    total_amount = taxable_base + gst_amount + (0 if additional_charges_taxable else charges)

    return {
        'subtotal': subtotal,
        'items': items_out,
        'total_quantity': total_quantity,
        'sgst': sgst, 'cgst': cgst, 'igst': igst,
        'gst_amount': gst_amount,
        'total_amount': total_amount,
        'tax_summary_rows': tax_summary_rows,
    }


def _save_quotation_from_form(quotation, form, files, org_id, employee_id):
    """
    Populate a Quotation (new or existing) from POST form data.
    Also creates/replaces items, tax summary, signature, attachments.
    """
    # ── Header ──────────────────────────────────────────────
    quotation.quotation_title  = form.get('quotation_title', 'Quotation')
    doc_type_val               = form.get('doc_type', 'Quotation')
    try:
        quotation.doc_type = QuotationDocType(doc_type_val)
    except ValueError:
        quotation.doc_type = QuotationDocType.QUOTATION

    issue_date_str = form.get('issue_date')
    due_date_str   = form.get('due_date')
    quotation.issue_date = (datetime.datetime.strptime(issue_date_str, '%Y-%m-%d')
                            if issue_date_str else datetime.datetime.now())
    quotation.due_date   = (datetime.datetime.strptime(due_date_str, '%Y-%m-%d')
                            if due_date_str else None)
    quotation.valid_till_type = form.get('valid_till_type', 'date')
    vdays = form.get('valid_till_days')
    quotation.valid_till_days = int(vdays) if vdays else None

    # ── Links ───────────────────────────────────────────────
    quotation.customer_id = form.get('customer_id') or None
    quotation.lead_id     = form.get('lead_id')     or None
    quotation.project_id  = form.get('project_id')  or None

    # ── Standard custom fields ───────────────────────────────
    for field in ['source', 'timeline', 'amendment_no', 'measurements', 'quote_level',
                  'sales_source', 'delivery_terms', 'payment_terms', 'shop_drawings',
                  'project_lead_name', 'application', 'manager_in_charge', 'references',
                  'delivery_tat', 'mode_of_delivery', 'unloading', 'freight_unloading']:
        setattr(quotation, field, form.get(field) or None)

    # ── Financial flags ──────────────────────────────────────
    quotation.total_discount      = float(form.get('total_discount') or 0)
    quotation.total_discount_type = form.get('total_discount_type', 'flat')
    quotation.additional_charges  = float(form.get('additional_charges') or 0)
    quotation.additional_charges_taxable = (form.get('additional_charges_taxable') == '1')
    quotation.additional_charges_label   = form.get('additional_charges_label') or None
    quotation.is_igst             = (form.get('is_igst') == '1')

    # ── Payment fields ───────────────────────────────────────
    quotation.advance_payment  = float(form.get('advance_payment') or 0)
    quotation.mode_of_payment  = form.get('mode_of_payment') or None
    quotation.balance_payment  = float(form.get('balance_payment') or 0)

    # ── Notes / Terms ────────────────────────────────────────
    quotation.notes          = form.get('notes') or None
    quotation.additional_info = form.get('additional_info') or None
    quotation.terms_conditions = form.get('terms_conditions') or None

    # ── Signature label ──────────────────────────────────────
    quotation.signature_label = form.get('signature_label', 'Authorised Signatory')

    # ── Status ───────────────────────────────────────────────
    status_val = form.get('status', 'Draft')
    try:
        quotation.status = QuotationStatus(status_val)
    except ValueError:
        quotation.status = QuotationStatus.DRAFT

    # ── Line Items ───────────────────────────────────────────
    # Clear existing items
    QuotationItem.query.filter_by(quotation_id=quotation.id).delete()

    item_names       = form.getlist('item_name[]')
    descriptions     = form.getlist('description[]')
    group_names      = form.getlist('group_name[]')
    formula_types    = form.getlist('formula_type[]')
    widths           = form.getlist('width[]')
    heights          = form.getlist('height[]')
    quantities       = form.getlist('quantity[]')
    chargeable_qtys  = form.getlist('chargeable_quantity[]')
    units            = form.getlist('unit[]')
    rates            = form.getlist('rate[]')
    discounts        = form.getlist('discount[]')
    discount_types   = form.getlist('discount_type[]')
    gst_rates        = form.getlist('gst_rate[]')

    items_data = []
    for i in range(len(item_names)):
        name = item_names[i].strip() if i < len(item_names) else ''
        desc = descriptions[i].strip() if i < len(descriptions) else ''
        if not name and not desc:
            continue
        
        c_qty_val = None
        if i < len(chargeable_qtys) and chargeable_qtys[i]:
            c_qty_val = float(chargeable_qtys[i])
            
        items_data.append({
            'sort_order':        i,
            'group_name':        group_names[i].strip() if i < len(group_names) else '',
            'item_name':         name,
            'description':       desc,
            'width':             float(widths[i]) if i < len(widths) and widths[i] else 0,
            'height':            float(heights[i]) if i < len(heights) and heights[i] else 0,
            'formula_type':      formula_types[i].strip() if i < len(formula_types) else 'standard',
            'quantity':          float(quantities[i]) if i < len(quantities) and quantities[i] else 1,
            'chargeable_quantity': c_qty_val,
            'unit':              units[i] if i < len(units) else 'Sq.Ft',
            'rate':              float(rates[i]) if i < len(rates) and rates[i] else 0,
            'discount':          float(discounts[i]) if i < len(discounts) and discounts[i] else 0,
            'discount_type':     discount_types[i] if i < len(discount_types) else 'flat',
            'gst_percentage':    float(gst_rates[i]) if i < len(gst_rates) and gst_rates[i] else 18,
        })

    totals = _calc_totals(
        items_data,
        quotation.total_discount,
        quotation.total_discount_type,
        quotation.additional_charges,
        quotation.additional_charges_taxable,
        quotation.is_igst
    )

    # Persist items
    for it in totals['items']:
        gst = it['gst_percentage']
        half = gst / 2
        item = QuotationItem(
            quotation_id      = quotation.id,
            sort_order        = it['sort_order'],
            group_name        = it.get('group_name'),
            item_name         = it['item_name'],
            description       = it['description'],
            formula_type      = it.get('formula_type'),
            width             = it['width'],
            height            = it['height'],
            quantity          = it['quantity'],
            chargeable_quantity = it.get('chargeable_quantity'),
            unit              = it['unit'],
            rate              = it['rate'],
            discount          = it['discount'],
            discount_type     = it['discount_type'],
            gst_percentage    = gst,
            sgst_rate         = 0 if quotation.is_igst else half,
            cgst_rate         = 0 if quotation.is_igst else half,
            igst_rate         = gst if quotation.is_igst else 0,
            amount            = it['amount'],
            gst_amount        = it['gst_amount'],
            total             = it['total'],
        )
        db.session.add(item)

    # ── Persist financials ───────────────────────────────────
    quotation.subtotal        = totals['subtotal']
    quotation.total_quantity  = totals['total_quantity']
    quotation.sgst            = totals['sgst']
    quotation.cgst            = totals['cgst']
    quotation.igst            = totals['igst']
    quotation.gst_amount      = totals['gst_amount']
    quotation.total_amount    = totals['total_amount']
    quotation.total_in_words  = number_to_words(totals['total_amount'])

    # ── Tax Summary ──────────────────────────────────────────
    QuotationTaxSummary.query.filter_by(quotation_id=quotation.id).delete()
    for row in totals['tax_summary_rows']:
        ts = QuotationTaxSummary(
            quotation_id   = quotation.id,
            gst_rate       = row['gst_rate'],
            taxable_amount = row['taxable_amount'],
            sgst_amount    = row['sgst_amount'],
            cgst_amount    = row['cgst_amount'],
            igst_amount    = row['igst_amount'],
            total_tax      = row['total_tax'],
        )
        db.session.add(ts)

    # ── Signature ────────────────────────────────────────────
    sig_type   = form.get('sig_type', 'upload')
    pad_data   = form.get('pad_data') or None
    sig_label  = form.get('signature_label', 'Authorised Signatory')

    QuotationSignature.query.filter_by(quotation_id=quotation.id).delete()

    sig_image_path = None
    sig_file = files.get('sig_upload')
    if sig_file and sig_file.filename:
        ext = os.path.splitext(sig_file.filename)[1].lower()
        fname = f"sig_{uuid.uuid4().hex}{ext}"
        upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'signatures')
        os.makedirs(upload_dir, exist_ok=True)
        sig_file.save(os.path.join(upload_dir, fname))
        sig_image_path = fname

    if sig_image_path or pad_data:
        sig = QuotationSignature(
            quotation_id = quotation.id,
            sig_type     = sig_type,
            image_path   = sig_image_path,
            pad_data     = pad_data,
            label        = sig_label,
        )
        db.session.add(sig)

    # ── Attachments ──────────────────────────────────────────
    attach_files = files.getlist('attachments[]')
    upload_dir   = os.path.join(current_app.root_path, 'static', 'uploads', 'quotation_attachments')
    os.makedirs(upload_dir, exist_ok=True)
    for af in attach_files:
        if af and af.filename:
            ext   = os.path.splitext(af.filename)[1].lower()
            fname = f"att_{uuid.uuid4().hex}{ext}"
            af.save(os.path.join(upload_dir, fname))
            att = QuotationAttachment(
                quotation_id  = quotation.id,
                filename      = fname,
                original_name = af.filename,
                file_type     = ext.lstrip('.'),
                file_size     = None,
                organization_id = org_id,
            )
            db.session.add(att)

    # ── Term Links ───────────────────────────────────────────
    # Receive list of term_ids to attach
    attached_term_ids = form.getlist('term_ids[]')
    QuotationTermLink.query.filter_by(quotation_id=quotation.id).delete()
    for idx, tid in enumerate(attached_term_ids):
        try:
            term = QuotationTerm.query.get(int(tid))
            if term:
                link = QuotationTermLink(
                    quotation_id = quotation.id,
                    term_id      = term.id,
                    group_id     = term.group_id,
                    sort_order   = idx,
                )
                db.session.add(link)
        except (ValueError, TypeError):
            pass

    # ── Custom Dynamic Field Values ──────────────────────────
    custom_field_ids = form.getlist('custom_field_id[]')
    custom_field_vals= form.getlist('custom_field_value[]')
    QuotationCustomFieldValue.query.filter_by(quotation_id=quotation.id).delete()
    for fid, fval in zip(custom_field_ids, custom_field_vals):
        try:
            cfv = QuotationCustomFieldValue(
                quotation_id = quotation.id,
                field_id     = int(fid),
                value        = fval,
            )
            db.session.add(cfv)
        except (ValueError, TypeError):
            pass


# ─────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/')
@login_required
def quotation_list():
    org_id = current_user.organization_id
    status_filter  = request.args.get('status', 'all')
    doc_type_filter = request.args.get('doc_type', 'all')
    search         = request.args.get('q', '').strip()

    q = Quotation.query.filter_by(organization_id=org_id, is_deleted=False)

    if status_filter != 'all':
        try:
            q = q.filter(Quotation.status == QuotationStatus(status_filter))
        except ValueError:
            pass

    if doc_type_filter != 'all':
        try:
            q = q.filter(Quotation.doc_type == QuotationDocType(doc_type_filter))
        except ValueError:
            pass

    if search:
        q = q.filter(
            db.or_(
                Quotation.quotation_number.ilike(f'%{search}%'),
                Quotation.quotation_title.ilike(f'%{search}%'),
            )
        )

    quotations = q.order_by(Quotation.created_at.desc()).all()

    # Stats
    all_q = Quotation.query.filter_by(organization_id=org_id, is_deleted=False)
    stats = {
        'total':    all_q.count(),
        'draft':    all_q.filter(Quotation.status == QuotationStatus.DRAFT).count(),
        'sent':     all_q.filter(Quotation.status == QuotationStatus.SENT).count(),
        'accepted': all_q.filter(Quotation.status == QuotationStatus.ACCEPTED).count(),
        'rejected': all_q.filter(Quotation.status == QuotationStatus.REJECTED).count(),
    }

    settings = _get_or_create_settings(org_id)
    return render_template('accounts/quotation_list.html',
                           quotations=quotations,
                           stats=stats,
                           status_filter=status_filter,
                           doc_type_filter=doc_type_filter,
                           search=search,
                           settings=settings)


# ─────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/new', methods=['GET', 'POST'])
@login_required
def add_quotation():
    org_id = current_user.organization_id
    emp    = current_user.employee

    if request.method == 'POST':
        try:
            quotation = Quotation(
                quotation_number = generate_quotation_number(org_id),
                organization_id  = org_id,
                created_by       = emp.id,
            )
            db.session.add(quotation)
            db.session.flush()  # Get ID

            _save_quotation_from_form(quotation, request.form, request.files, org_id, emp.id)

            log = ActivityLog(
                action        = 'quotation_created',
                entity_type   = 'quotation',
                entity_id     = quotation.id,
                entity_name   = quotation.quotation_number,
                description   = f"Created {quotation.doc_type_display} {quotation.quotation_number}",
                actor_id      = emp.id,
                organization_id = org_id,
            )
            db.session.add(log)
            db.session.commit()
            flash(f"✅ {quotation.doc_type_display} {quotation.quotation_number} created!", 'success')
            return redirect(url_for('quotations.view_quotation', quotation_id=quotation.id))

        except Exception as e:
            db.session.rollback()
            import traceback; traceback.print_exc()
            flash(f"Error: {str(e)}", 'error')

    # GET — form data
    settings     = _get_or_create_settings(org_id)
    customers    = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    leads        = Lead.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects     = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    employees    = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    custom_fields= QuotationCustomField.query.filter_by(organization_id=org_id, is_active=True)\
                        .order_by(QuotationCustomField.sort_order).all()
    term_groups  = QuotationTermGroup.query.filter_by(organization_id=org_id, is_active=True)\
                        .order_by(QuotationTermGroup.sort_order).all()

    # Pre-select default term groups
    default_term_ids = []
    for tg in term_groups:
        if tg.is_default:
            for t in tg.terms:
                default_term_ids.append(t.id)

    next_number = f"{settings.number_prefix}/{datetime.datetime.now().strftime('%y')}/{settings.number_counter:04d}"

    return render_template('accounts/quotation_form.html',
                           quotation    = None,
                           settings     = settings,
                           customers    = customers,
                           leads        = leads,
                           projects     = projects,
                           employees    = employees,
                           custom_fields= custom_fields,
                           term_groups  = term_groups,
                           default_term_ids = default_term_ids,
                           next_number  = next_number,
                           today        = datetime.date.today(),
                           mode         = 'create')


# ─────────────────────────────────────────────────────────────
# EDIT
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_quotation(quotation_id):
    org_id    = current_user.organization_id
    emp       = current_user.employee
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id, is_deleted=False).first_or_404()

    if request.method == 'POST':
        try:
            _save_quotation_from_form(quotation, request.form, request.files, org_id, emp.id)

            log = ActivityLog(
                action        = 'quotation_updated',
                entity_type   = 'quotation',
                entity_id     = quotation.id,
                entity_name   = quotation.quotation_number,
                description   = f"Updated {quotation.doc_type_display} {quotation.quotation_number}",
                actor_id      = emp.id,
                organization_id = org_id,
            )
            db.session.add(log)
            db.session.commit()
            flash(f"✅ {quotation.quotation_number} updated!", 'success')
            return redirect(url_for('quotations.view_quotation', quotation_id=quotation.id))

        except Exception as e:
            db.session.rollback()
            import traceback; traceback.print_exc()
            flash(f"Error: {str(e)}", 'error')

    settings     = _get_or_create_settings(org_id)
    customers    = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    leads        = Lead.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects     = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    employees    = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    custom_fields= QuotationCustomField.query.filter_by(organization_id=org_id, is_active=True)\
                        .order_by(QuotationCustomField.sort_order).all()
    term_groups  = QuotationTermGroup.query.filter_by(organization_id=org_id, is_active=True)\
                        .order_by(QuotationTermGroup.sort_order).all()

    attached_term_ids = [tl.term_id for tl in quotation.term_links if tl.term_id]

    return render_template('accounts/quotation_form.html',
                           quotation    = quotation,
                           settings     = settings,
                           customers    = customers,
                           leads        = leads,
                           projects     = projects,
                           employees    = employees,
                           custom_fields= custom_fields,
                           term_groups  = term_groups,
                           attached_term_ids = attached_term_ids,
                           today        = datetime.date.today(),
                           mode         = 'edit')


# ─────────────────────────────────────────────────────────────
# VIEW / PRINT PREVIEW
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>')
@login_required
def view_quotation(quotation_id):
    org_id    = current_user.organization_id
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id, is_deleted=False).first_or_404()
    settings  = _get_or_create_settings(org_id)

    # Build term groups for display
    group_map = {}
    for tl in sorted(quotation.term_links, key=lambda x: x.sort_order):
        g = tl.group
        if g and g.id not in group_map:
            group_map[g.id] = {'group': g, 'terms': []}
        term_display = {
            'title': tl.custom_title or (tl.term.term_title if tl.term else ''),
            'body':  tl.custom_body  or (tl.term.term_body  if tl.term else ''),
        }
        if g:
            group_map[g.id]['terms'].append(term_display)

    term_sections = list(group_map.values())

    signature = quotation.signatures[0] if quotation.signatures else None

    return render_template('accounts/quotation_view.html',
                           quotation     = quotation,
                           settings      = settings,
                           term_sections = term_sections,
                           signature     = signature)


# ─────────────────────────────────────────────────────────────
# UPDATE STATUS (AJAX)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/status', methods=['POST'])
@login_required
def update_status(quotation_id):
    org_id    = current_user.organization_id
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id).first_or_404()
    payload   = request.get_json() or request.form
    new_status= payload.get('status')
    try:
        quotation.status = QuotationStatus(new_status)
        db.session.commit()
        return jsonify({'success': True, 'status': quotation.status.value})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


# ─────────────────────────────────────────────────────────────
# SAVE DRAFT (AJAX)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/save-draft', methods=['POST'])
@login_required
def save_draft(quotation_id):
    org_id    = current_user.organization_id
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id).first_or_404()
    try:
        _save_quotation_from_form(quotation, request.form, request.files, org_id,
                                  current_user.employee.id)
        quotation.status = QuotationStatus.DRAFT
        db.session.commit()
        return jsonify({'success': True, 'quotation_number': quotation.quotation_number})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400


# ─────────────────────────────────────────────────────────────
# DUPLICATE
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/duplicate', methods=['POST'])
@login_required
def duplicate_quotation(quotation_id):
    org_id    = current_user.organization_id
    emp       = current_user.employee
    src = Quotation.query.filter_by(id=quotation_id, organization_id=org_id, is_deleted=False).first_or_404()

    try:
        new_q = Quotation(
            quotation_number = generate_quotation_number(org_id),
            organization_id  = org_id,
            created_by       = emp.id,
            quotation_title  = src.quotation_title,
            doc_type         = src.doc_type,
            customer_id      = src.customer_id,
            lead_id          = src.lead_id,
            project_id       = src.project_id,
            source           = src.source,
            timeline         = src.timeline,
            amendment_no     = src.amendment_no,
            measurements     = src.measurements,
            quote_level      = src.quote_level,
            sales_source     = src.sales_source,
            delivery_terms   = src.delivery_terms,
            payment_terms    = src.payment_terms,
            shop_drawings    = src.shop_drawings,
            project_lead_name= src.project_lead_name,
            application      = src.application,
            manager_in_charge= src.manager_in_charge,
            references       = src.references,
            delivery_tat     = src.delivery_tat,
            mode_of_delivery = src.mode_of_delivery,
            unloading        = src.unloading,
            freight_unloading= src.freight_unloading,
            total_discount   = src.total_discount,
            total_discount_type = src.total_discount_type,
            additional_charges = src.additional_charges,
            additional_charges_taxable = src.additional_charges_taxable,
            additional_charges_label   = src.additional_charges_label,
            is_igst          = src.is_igst,
            advance_payment  = src.advance_payment,
            mode_of_payment  = src.mode_of_payment,
            balance_payment  = src.balance_payment,
            notes            = src.notes,
            additional_info  = src.additional_info,
            terms_conditions = src.terms_conditions,
            signature_label  = src.signature_label,
            status           = QuotationStatus.DRAFT,
            issue_date       = datetime.datetime.now(),
        )
        db.session.add(new_q)
        db.session.flush()

        # Copy items
        for it in src.items:
            ni = QuotationItem(
                quotation_id=new_q.id, sort_order=it.sort_order,
                item_name=it.item_name, description=it.description,
                width=it.width, height=it.height, quantity=it.quantity,
                unit=it.unit, rate=it.rate, discount=it.discount,
                discount_type=it.discount_type, gst_percentage=it.gst_percentage,
                sgst_rate=it.sgst_rate, cgst_rate=it.cgst_rate, igst_rate=it.igst_rate,
                amount=it.amount, gst_amount=it.gst_amount, total=it.total,
            )
            db.session.add(ni)

        # Copy term links
        for tl in src.term_links:
            ntl = QuotationTermLink(
                quotation_id=new_q.id, term_id=tl.term_id,
                group_id=tl.group_id, sort_order=tl.sort_order,
            )
            db.session.add(ntl)

        # Re-aggregate totals
        new_q.subtotal       = src.subtotal
        new_q.total_quantity = src.total_quantity
        new_q.sgst           = src.sgst
        new_q.cgst           = src.cgst
        new_q.igst           = src.igst
        new_q.gst_amount     = src.gst_amount
        new_q.total_amount   = src.total_amount
        new_q.total_in_words = src.total_in_words

        db.session.commit()
        flash(f"✅ Duplicated as {new_q.quotation_number}", 'success')
        return redirect(url_for('quotations.edit_quotation', quotation_id=new_q.id))

    except Exception as e:
        db.session.rollback()
        import traceback; traceback.print_exc()
        flash(f"Error duplicating: {str(e)}", 'error')
        return redirect(url_for('quotations.view_quotation', quotation_id=quotation_id))


# ─────────────────────────────────────────────────────────────
# CONVERT TO PROFORMA INVOICE
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/convert-proforma', methods=['POST'])
@login_required
def convert_to_proforma(quotation_id):
    org_id    = current_user.organization_id
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id, is_deleted=False).first_or_404()
    try:
        quotation.doc_type = QuotationDocType.PROFORMA_INVOICE
        quotation.quotation_title = "Proforma Invoice"
        db.session.commit()
        flash(f"✅ Converted to Proforma Invoice", 'success')
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", 'error')
    return redirect(url_for('quotations.view_quotation', quotation_id=quotation_id))


# ─────────────────────────────────────────────────────────────
# DELETE (soft)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/delete', methods=['POST'])
@login_required
def delete_quotation(quotation_id):
    org_id    = current_user.organization_id
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id).first_or_404()
    quotation.is_deleted = True
    db.session.commit()
    flash(f"Quotation {quotation.quotation_number} deleted.", 'info')
    return redirect(url_for('quotations.quotation_list'))


# ─────────────────────────────────────────────────────────────
# PDF GENERATION
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/<int:quotation_id>/pdf')
@login_required
def generate_pdf(quotation_id):
    org_id    = current_user.organization_id
    quotation = Quotation.query.filter_by(id=quotation_id, organization_id=org_id, is_deleted=False).first_or_404()
    settings  = _get_or_create_settings(org_id)

    # Build term groups
    group_map = {}
    for tl in sorted(quotation.term_links, key=lambda x: x.sort_order):
        g = tl.group
        if g and g.id not in group_map:
            group_map[g.id] = {'group': g, 'terms': []}
        term_display = {
            'title': tl.custom_title or (tl.term.term_title if tl.term else ''),
            'body':  tl.custom_body  or (tl.term.term_body  if tl.term else ''),
        }
        if g:
            group_map[g.id]['terms'].append(term_display)

    term_sections = list(group_map.values())
    signature     = quotation.signatures[0] if quotation.signatures else None

    try:
        from utils.quotation_pdf import render_quotation_pdf
        pdf_bytes = render_quotation_pdf(quotation, settings, term_sections, signature)
        import io
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype        = 'application/pdf',
            as_attachment   = False,
            download_name   = f"{quotation.quotation_number.replace('/', '-')}.pdf"
        )
    except Exception as e:
        import traceback; traceback.print_exc()
        flash(f"PDF generation error: {str(e)}", 'error')
        return redirect(url_for('quotations.view_quotation', quotation_id=quotation_id))


# ─────────────────────────────────────────────────────────────
# API — NEXT NUMBER (AJAX)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/api/next-number')
@login_required
def api_next_number():
    org_id   = current_user.organization_id
    settings = _get_or_create_settings(org_id)
    prefix   = settings.number_prefix or 'GL'
    year     = datetime.datetime.now().strftime('%y')
    seq      = settings.number_counter
    return jsonify({'number': f"{prefix}/{year}/{seq:04d}"})


# ─────────────────────────────────────────────────────────────
# API — CUSTOMER DETAILS (AJAX autofill)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/api/customer/<int:customer_id>')
@login_required
def api_customer(customer_id):
    c = Customer.query.filter_by(id=customer_id, organization_id=current_user.organization_id).first_or_404()
    return jsonify({
        'id':         c.id,
        'name':       c.name,
        'email':      c.email,
        'phone':      c.phone_number,
        'address':    c.address or '',
        'city':       c.city or '',
        'state':      c.state or '',
        'gstin':      c.gst_number or '',
        'company':    c.company or '',
        'trade_name': c.trade_name or '',
    })


# ─────────────────────────────────────────────────────────────
# API — CALCULATE TOTALS (AJAX live calc)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/api/calculate', methods=['POST'])
@login_required
def api_calculate():
    data = request.get_json()
    try:
        totals = _calc_totals(
            data.get('items', []),
            data.get('total_discount', 0),
            data.get('total_discount_type', 'flat'),
            data.get('additional_charges', 0),
            data.get('additional_charges_taxable', False),
            data.get('is_igst', False),
        )
        totals['total_in_words'] = number_to_words(totals['total_amount'])
        totals['words'] = totals['total_in_words'] # Add words explicitly for frontend
        return jsonify({'success': True, 'result': totals})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


# ─────────────────────────────────────────────────────────────
# ATTACHMENT DOWNLOAD
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/attachment/<int:att_id>')
@login_required
def download_attachment(att_id):
    att = QuotationAttachment.query.filter_by(
        id=att_id, organization_id=current_user.organization_id).first_or_404()
    path = os.path.join(current_app.root_path, 'static', 'uploads',
                        'quotation_attachments', att.filename)
    return send_file(path, as_attachment=True, download_name=att.original_name)


# ─────────────────────────────────────────────────────────────
# ATTACHMENT DELETE (AJAX)
# ─────────────────────────────────────────────────────────────

@quotations_bp.route('/attachment/<int:att_id>/delete', methods=['POST'])
@login_required
def delete_attachment(att_id):
    att = QuotationAttachment.query.filter_by(
        id=att_id, organization_id=current_user.organization_id).first_or_404()
    try:
        path = os.path.join(current_app.root_path, 'static', 'uploads',
                            'quotation_attachments', att.filename)
        if os.path.exists(path):
            os.remove(path)
        db.session.delete(att)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400

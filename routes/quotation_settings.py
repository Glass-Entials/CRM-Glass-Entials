from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, current_app)
from flask_login import login_required, current_user
from model import (db, QuotationSettings, QuotationCustomField,
                   QuotationTermGroup, QuotationTerm, Organization)
import os, uuid, json

quotation_settings_bp = Blueprint('quotation_settings', __name__,
                                  url_prefix='/quotation-settings')


def _get_or_create_settings(org_id):
    s = QuotationSettings.query.filter_by(organization_id=org_id).first()
    if not s:
        s = QuotationSettings(organization_id=org_id)
        db.session.add(s)
        db.session.commit()
    return s


def _seed_default_custom_fields(org_id):
    """Create the standard optional fields if none exist."""
    defaults = [
        ('Unloading',          'unloading',          'text',     0),
        ('Freight & Unloading','freight_unloading',   'text',     1),
        ('Amendment No',       'amendment_no',        'text',     2),
        ('Measurements',       'measurements',        'text',     3),
        ('Quote Level',        'quote_level',         'text',     4),
        ('Sales Source',       'sales_source',        'text',     5),
        ('Delivery Terms',     'delivery_terms',      'textarea', 6),
        ('Payment Terms',      'payment_terms',       'textarea', 7),
        ('Shop Drawings',      'shop_drawings',       'text',     8),
        ('Project Lead',       'project_lead_name',   'text',     9),
        ('Application',        'application',         'text',     10),
        ('Manager in Charge',  'manager_in_charge',   'text',     11),
        ('References',         'references',          'text',     12),
        ('Delivery TAT',       'delivery_tat',        'text',     13),
        ('Mode of Delivery',   'mode_of_delivery',    'text',     14),
        ('Timeline',           'timeline',            'text',     15),
        ('Source',             'source',              'text',     16),
    ]
    for label, key, ftype, order in defaults:
        exists = QuotationCustomField.query.filter_by(
            organization_id=org_id, field_key=key).first()
        if not exists:
            f = QuotationCustomField(
                organization_id    = org_id,
                label              = label,
                field_key          = key,
                field_type         = ftype,
                sort_order         = order,
                is_active          = True,
                is_default_visible = False,
                is_system          = True,
            )
            db.session.add(f)
    db.session.commit()


def _seed_default_terms(org_id):
    """Create Annexure-1 default terms group if none exist."""
    existing = QuotationTermGroup.query.filter_by(organization_id=org_id).first()
    if existing:
        return

    grp = QuotationTermGroup(
        organization_id = org_id,
        name            = 'Annexure-1',
        description     = 'Standard Terms & Conditions',
        sort_order      = 0,
        is_active       = True,
        is_default      = True,
    )
    db.session.add(grp)
    db.session.flush()

    sample_terms = [
        ('Validity',
         'This quotation is valid for 30 days from the date of issue.'),
        ('Payment Terms',
         '50% advance payment at the time of order confirmation. Balance before delivery.'),
        ('Delivery',
         'Delivery timeline will be communicated after order confirmation. Transit risks are at buyer\'s account.'),
        ('Installation',
         'Installation charges are not included unless specified separately.'),
        ('Warranty',
         'All products carry a manufacturer warranty of 1 year from the date of installation against manufacturing defects.'),
        ('Cancellation',
         'Orders once confirmed cannot be cancelled. Any cancellation will attract 25% of the order value as cancellation charges.'),
        ('Disputes',
         'Any disputes shall be subject to the jurisdiction of local courts only.'),
    ]
    for i, (title, body) in enumerate(sample_terms):
        t = QuotationTerm(
            group_id        = grp.id,
            organization_id = org_id,
            term_title      = title,
            term_body       = body,
            sort_order      = i,
            is_active       = True,
            version         = 1,
        )
        db.session.add(t)
    db.session.commit()


# ─────────────────────────────────────────────────────────────
# MAIN SETTINGS PAGE
# ─────────────────────────────────────────────────────────────

@quotation_settings_bp.route('/', methods=['GET', 'POST'])
@login_required
def settings_home():
    org_id   = current_user.organization_id
    settings = _get_or_create_settings(org_id)

    if request.method == 'POST':
        section = request.form.get('section')

        if section == 'company':
            settings.company_name    = request.form.get('company_name') or None
            settings.company_address = request.form.get('company_address') or None
            settings.company_gstin   = request.form.get('company_gstin') or None
            settings.company_pan     = request.form.get('company_pan') or None
            settings.company_email   = request.form.get('company_email') or None
            settings.company_phone   = request.form.get('company_phone') or None
            settings.company_state   = request.form.get('company_state') or None

            logo_file = request.files.get('company_logo')
            if logo_file and logo_file.filename:
                ext   = os.path.splitext(logo_file.filename)[1].lower()
                fname = f"logo_{uuid.uuid4().hex}{ext}"
                upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'logos')
                os.makedirs(upload_dir, exist_ok=True)
                logo_file.save(os.path.join(upload_dir, fname))
                settings.company_logo = fname
            flash('✅ Company profile saved.', 'success')

        elif section == 'numbering':
            settings.number_prefix  = request.form.get('number_prefix', 'GL').strip().upper()
            reset = request.form.get('reset_counter')
            if reset == '1':
                settings.number_counter = 1
            flash('✅ Numbering settings saved.', 'success')

        elif section == 'defaults':
            settings.validity_days         = int(request.form.get('validity_days') or 30)
            settings.default_gst_rate      = float(request.form.get('default_gst_rate') or 18)
            settings.default_sgst_rate     = settings.default_gst_rate / 2
            settings.default_cgst_rate     = settings.default_gst_rate / 2
            settings.default_igst_rate     = settings.default_gst_rate
            settings.default_payment_terms = request.form.get('default_payment_terms') or None
            settings.default_delivery_terms= request.form.get('default_delivery_terms') or None
            settings.default_notes         = request.form.get('default_notes') or None
            flash('✅ Default settings saved.', 'success')

        elif section == 'bank':
            settings.bank_name        = request.form.get('bank_name') or None
            settings.bank_account_no  = request.form.get('bank_account_no') or None
            settings.bank_ifsc        = request.form.get('bank_ifsc') or None
            settings.bank_branch      = request.form.get('bank_branch') or None
            settings.beneficiary_name = request.form.get('beneficiary_name') or None
            flash('✅ Bank details saved.', 'success')

        elif section == 'pdf':
            settings.pdf_footer_text         = request.form.get('pdf_footer_text') or None
            settings.show_bank_details_on_pdf = (request.form.get('show_bank_details_on_pdf') == '1')
            settings.show_signature_on_pdf    = (request.form.get('show_signature_on_pdf') == '1')
            settings.default_signature_label  = request.form.get('default_signature_label', 'Authorised Signatory')

            sig_file = request.files.get('default_signature')
            if sig_file and sig_file.filename:
                ext   = os.path.splitext(sig_file.filename)[1].lower()
                fname = f"defaultsig_{uuid.uuid4().hex}{ext}"
                upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'signatures')
                os.makedirs(upload_dir, exist_ok=True)
                sig_file.save(os.path.join(upload_dir, fname))
                settings.default_signature_path = fname
            flash('✅ PDF settings saved.', 'success')

        db.session.commit()
        return redirect(url_for('quotation_settings.settings_home'))

    custom_fields = QuotationCustomField.query.filter_by(organization_id=org_id)\
                        .order_by(QuotationCustomField.sort_order).all()
    term_groups   = QuotationTermGroup.query.filter_by(organization_id=org_id)\
                        .order_by(QuotationTermGroup.sort_order).all()

    return render_template('accounts/quotation_settings.html',
                           settings     = settings,
                           custom_fields= custom_fields,
                           term_groups  = term_groups)


# ─────────────────────────────────────────────────────────────
# SEED DEFAULTS (one-time action)
# ─────────────────────────────────────────────────────────────

@quotation_settings_bp.route('/seed-defaults', methods=['POST'])
@login_required
def seed_defaults():
    org_id = current_user.organization_id
    _seed_default_custom_fields(org_id)
    _seed_default_terms(org_id)
    flash('✅ Default fields & terms seeded.', 'success')
    return redirect(url_for('quotation_settings.settings_home'))


# ─────────────────────────────────────────────────────────────
# CUSTOM FIELDS CRUD
# ─────────────────────────────────────────────────────────────

@quotation_settings_bp.route('/custom-fields', methods=['POST'])
@login_required
def add_custom_field():
    org_id = current_user.organization_id
    label  = request.form.get('label', '').strip()
    if not label:
        return jsonify({'success': False, 'error': 'Label required'}), 400

    key = label.lower().replace(' ', '_').replace('&', 'and')[:50]
    # Ensure unique key
    existing = QuotationCustomField.query.filter_by(
        organization_id=org_id, field_key=key).first()
    if existing:
        key = key + '_' + uuid.uuid4().hex[:4]

    max_order = db.session.query(db.func.max(QuotationCustomField.sort_order))\
                    .filter_by(organization_id=org_id).scalar() or 0
    f = QuotationCustomField(
        organization_id    = org_id,
        label              = label,
        field_key          = key,
        field_type         = request.form.get('field_type', 'text'),
        sort_order         = max_order + 1,
        is_active          = True,
        is_default_visible = (request.form.get('is_default_visible') == '1'),
        is_system          = False,
    )
    db.session.add(f)
    db.session.commit()
    return jsonify({'success': True, 'id': f.id, 'label': f.label, 'field_key': f.field_key})


@quotation_settings_bp.route('/custom-fields/<int:field_id>', methods=['POST'])
@login_required
def update_custom_field(field_id):
    org_id = current_user.organization_id
    f = QuotationCustomField.query.filter_by(
        id=field_id, organization_id=org_id).first_or_404()

    action = request.form.get('action') or request.get_json(silent=True, force=True).get('action')
    payload = request.get_json(silent=True, force=True) or request.form

    if action == 'toggle':
        f.is_active = not f.is_active
    elif action == 'toggle_visible':
        f.is_default_visible = not f.is_default_visible
    elif action == 'delete' and not f.is_system:
        db.session.delete(f)
        db.session.commit()
        return jsonify({'success': True, 'deleted': True})
    elif action == 'reorder':
        f.sort_order = int(payload.get('sort_order', f.sort_order))
    else:
        f.label      = payload.get('label', f.label)
        f.field_type = payload.get('field_type', f.field_type)
        opts = payload.get('options')
        if opts:
            f.options = opts if isinstance(opts, str) else json.dumps(opts)

    db.session.commit()
    return jsonify({'success': True})


# ─────────────────────────────────────────────────────────────
# TERMS MANAGEMENT
# ─────────────────────────────────────────────────────────────

@quotation_settings_bp.route('/terms')
@login_required
def terms_list():
    org_id      = current_user.organization_id
    term_groups = QuotationTermGroup.query.filter_by(organization_id=org_id)\
                      .order_by(QuotationTermGroup.sort_order).all()
    return render_template('accounts/quotation_terms.html', term_groups=term_groups)


@quotation_settings_bp.route('/terms/groups', methods=['POST'])
@login_required
def add_term_group():
    org_id = current_user.organization_id
    name   = request.form.get('name', '').strip()
    if not name:
        flash('Group name required.', 'error')
        return redirect(url_for('quotation_settings.terms_list'))

    max_order = db.session.query(db.func.max(QuotationTermGroup.sort_order))\
                    .filter_by(organization_id=org_id).scalar() or 0
    grp = QuotationTermGroup(
        organization_id = org_id,
        name            = name,
        description     = request.form.get('description') or None,
        sort_order      = max_order + 1,
        is_active       = True,
        is_default      = (request.form.get('is_default') == '1'),
    )
    db.session.add(grp)
    db.session.commit()
    flash(f'✅ Term group "{name}" created.', 'success')
    return redirect(url_for('quotation_settings.terms_list'))


@quotation_settings_bp.route('/terms/groups/<int:group_id>/delete', methods=['POST'])
@login_required
def delete_term_group(group_id):
    org_id = current_user.organization_id
    grp = QuotationTermGroup.query.filter_by(id=group_id, organization_id=org_id).first_or_404()
    db.session.delete(grp)
    db.session.commit()
    flash('Term group deleted.', 'info')
    return redirect(url_for('quotation_settings.terms_list'))


@quotation_settings_bp.route('/terms/add', methods=['POST'])
@login_required
def add_term():
    org_id   = current_user.organization_id
    group_id = request.form.get('group_id')
    title    = request.form.get('term_title', '').strip()
    body     = request.form.get('term_body', '').strip()

    if not group_id or not title or not body:
        flash('All fields required.', 'error')
        return redirect(url_for('quotation_settings.terms_list'))

    grp = QuotationTermGroup.query.filter_by(id=group_id, organization_id=org_id).first_or_404()
    max_order = db.session.query(db.func.max(QuotationTerm.sort_order))\
                    .filter_by(group_id=grp.id).scalar() or 0
    t = QuotationTerm(
        group_id        = grp.id,
        organization_id = org_id,
        term_title      = title,
        term_body       = body,
        sort_order      = max_order + 1,
        is_active       = True,
        version         = 1,
    )
    db.session.add(t)
    db.session.commit()
    flash(f'✅ Term "{title}" added.', 'success')
    return redirect(url_for('quotation_settings.terms_list'))


@quotation_settings_bp.route('/terms/<int:term_id>/edit', methods=['POST'])
@login_required
def edit_term(term_id):
    org_id = current_user.organization_id
    t = QuotationTerm.query.filter_by(id=term_id, organization_id=org_id).first_or_404()
    t.term_title = request.form.get('term_title', t.term_title)
    t.term_body  = request.form.get('term_body', t.term_body)
    t.version   += 1
    db.session.commit()
    return jsonify({'success': True})


@quotation_settings_bp.route('/terms/<int:term_id>/delete', methods=['POST'])
@login_required
def delete_term(term_id):
    org_id = current_user.organization_id
    t = QuotationTerm.query.filter_by(id=term_id, organization_id=org_id).first_or_404()
    db.session.delete(t)
    db.session.commit()
    flash('Term deleted.', 'info')
    return redirect(url_for('quotation_settings.terms_list'))


@quotation_settings_bp.route('/terms/reorder', methods=['POST'])
@login_required
def reorder_terms():
    """Accepts JSON: [{id: N, sort_order: M}, ...]"""
    org_id = current_user.organization_id
    data   = request.get_json()
    try:
        for item in data:
            t = QuotationTerm.query.filter_by(
                id=item['id'], organization_id=org_id).first()
            if t:
                t.sort_order = item['sort_order']
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 400

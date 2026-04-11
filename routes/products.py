from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from flask_login import login_required, current_user
from model import db, Product, ProductCategory, ProductStatus, Employee, UserRole
from datetime import datetime
import os
import uuid
from werkzeug.utils import secure_filename

products_bp = Blueprint('products', __name__, url_prefix='/products')

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_image(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS


def save_product_image(file):
    """Save uploaded product image and return stored filename."""
    ext = file.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'products')
    os.makedirs(upload_dir, exist_ok=True)
    file.save(os.path.join(upload_dir, unique_name))
    return unique_name


# ─────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────
@products_bp.route('/')
@login_required
def products_list():
    org_id = current_user.organization_id

    # Filters
    category_filter = request.args.get('category', 'All')
    status_filter = request.args.get('status', 'Active')
    search_query = request.args.get('q', '').strip()

    query = Product.query.filter_by(organization_id=org_id, is_deleted=False)

    if status_filter != 'All':
        status_map = {e.value: e for e in ProductStatus}
        if status_filter in status_map:
            query = query.filter(Product.status == status_map[status_filter])

    if category_filter != 'All':
        cat_map = {e.value: e for e in ProductCategory}
        if category_filter in cat_map:
            query = query.filter(Product.category == cat_map[category_filter])

    if search_query:
        query = query.filter(
            Product.name.ilike(f'%{search_query}%') |
            Product.sku.ilike(f'%{search_query}%') |
            Product.description.ilike(f'%{search_query}%')
        )

    products = query.order_by(Product.created_at.desc()).all()

    # Stats
    total_products = Product.query.filter_by(organization_id=org_id, is_deleted=False).count()
    active_products = Product.query.filter_by(
        organization_id=org_id, is_deleted=False, status=ProductStatus.ACTIVE).count()
    low_stock_count = sum(1 for p in Product.query.filter_by(
        organization_id=org_id, is_deleted=False).all() if p.is_low_stock)

    categories = [e.value for e in ProductCategory]
    statuses = [e.value for e in ProductStatus]

    return render_template(
        'products/products_list.html',
        products=products,
        categories=categories,
        statuses=statuses,
        category_filter=category_filter,
        status_filter=status_filter,
        search_query=search_query,
        total_products=total_products,
        active_products=active_products,
        low_stock_count=low_stock_count,
    )


# ─────────────────────────────────────────────────────────
# ADD
# ─────────────────────────────────────────────────────────
@products_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_product():
    org_id = current_user.organization_id

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'producterror')
            return redirect(url_for('products.add_product'))

        category_val = request.form.get('category', ProductCategory.OTHER.value)
        status_val = request.form.get('status', ProductStatus.ACTIVE.value)
        cat_map = {e.value: e for e in ProductCategory}
        status_map = {e.value: e for e in ProductStatus}

        # Image upload
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_image(file.filename):
                image_filename = save_product_image(file)

        try:
            product = Product(
                name=name,
                sku=request.form.get('sku', '').strip() or None,
                description=request.form.get('description', '').strip() or None,
                image=image_filename,
                category=cat_map.get(category_val, ProductCategory.OTHER),
                status=status_map.get(status_val, ProductStatus.ACTIVE),
                unit=request.form.get('unit', 'Sq.Ft').strip(),
                cost_price=float(request.form.get('cost_price') or 0),
                selling_price=float(request.form.get('selling_price') or 0),
                min_price=float(request.form.get('min_price')) if request.form.get('min_price') else None,
                gst_rate=float(request.form.get('gst_rate') or 18),
                hsn_code=request.form.get('hsn_code', '').strip() or None,
                stock_quantity=float(request.form.get('stock_quantity') or 0),
                min_stock_alert=float(request.form.get('min_stock_alert')) if request.form.get('min_stock_alert') else None,
                notes=request.form.get('notes', '').strip() or None,
                tags=request.form.get('tags', '').strip() or None,
                organization_id=org_id,
                created_by=current_user.employee.id if current_user.employee else None,
            )
            db.session.add(product)
            db.session.commit()
            flash(f'Product "{name}" added successfully!', 'productsuccess')
            return redirect(url_for('products.products_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving product: {str(e)}', 'producterror')
            return redirect(url_for('products.add_product'))

    categories = [e.value for e in ProductCategory]
    statuses = [e.value for e in ProductStatus]
    units = ['Sq.Ft', 'Pcs', 'Rft', 'Kg', 'Litre', 'Metre', 'Set', 'Roll', 'Box', 'Other']
    return render_template('products/add_product.html',
                           categories=categories, statuses=statuses, units=units)


# ─────────────────────────────────────────────────────────
# VIEW
# ─────────────────────────────────────────────────────────
@products_bp.route('/view/<int:product_id>')
@login_required
def view_product(product_id):
    org_id = current_user.organization_id
    product = Product.query.filter_by(id=product_id, organization_id=org_id,
                                       is_deleted=False).first_or_404()
    return render_template('products/view_product.html', product=product)


# ─────────────────────────────────────────────────────────
# EDIT
# ─────────────────────────────────────────────────────────
@products_bp.route('/edit/<int:product_id>', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    org_id = current_user.organization_id
    product = Product.query.filter_by(id=product_id, organization_id=org_id,
                                       is_deleted=False).first_or_404()

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Product name is required.', 'producterror')
            return redirect(url_for('products.edit_product', product_id=product_id))

        cat_map = {e.value: e for e in ProductCategory}
        status_map = {e.value: e for e in ProductStatus}

        product.name = name
        product.sku = request.form.get('sku', '').strip() or None
        product.description = request.form.get('description', '').strip() or None
        product.category = cat_map.get(request.form.get('category'), ProductCategory.OTHER)
        product.status = status_map.get(request.form.get('status'), ProductStatus.ACTIVE)
        product.unit = request.form.get('unit', 'Sq.Ft').strip()
        product.cost_price = float(request.form.get('cost_price') or 0)
        product.selling_price = float(request.form.get('selling_price') or 0)
        product.min_price = float(request.form.get('min_price')) if request.form.get('min_price') else None
        product.gst_rate = float(request.form.get('gst_rate') or 18)
        product.hsn_code = request.form.get('hsn_code', '').strip() or None
        product.stock_quantity = float(request.form.get('stock_quantity') or 0)
        product.min_stock_alert = float(request.form.get('min_stock_alert')) if request.form.get('min_stock_alert') else None
        product.notes = request.form.get('notes', '').strip() or None
        product.tags = request.form.get('tags', '').strip() or None

        # Replace image if uploaded
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename != '' and allowed_image(file.filename):
                product.image = save_product_image(file)

        try:
            db.session.commit()
            flash(f'Product "{name}" updated successfully!', 'productsuccess')
            return redirect(url_for('products.view_product', product_id=product_id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating product: {str(e)}', 'producterror')

    categories = [e.value for e in ProductCategory]
    statuses = [e.value for e in ProductStatus]
    units = ['Sq.Ft', 'Pcs', 'Rft', 'Kg', 'Litre', 'Metre', 'Set', 'Roll', 'Box', 'Other']
    return render_template('products/edit_product.html',
                           product=product, categories=categories,
                           statuses=statuses, units=units)


# ─────────────────────────────────────────────────────────
# DELETE (soft)
# ─────────────────────────────────────────────────────────
@products_bp.route('/delete/<int:product_id>', methods=['POST'])
@login_required
def delete_product(product_id):
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        flash('Unauthorized.', 'producterror')
        return redirect(url_for('products.products_list'))

    org_id = current_user.organization_id
    product = Product.query.filter_by(id=product_id, organization_id=org_id,
                                       is_deleted=False).first_or_404()
    try:
        product.is_deleted = True
        db.session.commit()
        flash(f'Product "{product.name}" deleted.', 'productsuccess')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {str(e)}', 'producterror')

    return redirect(url_for('products.products_list'))


# ─────────────────────────────────────────────────────────
# ADJUST STOCK
# ─────────────────────────────────────────────────────────
@products_bp.route('/stock/<int:product_id>', methods=['POST'])
@login_required
def adjust_stock(product_id):
    org_id = current_user.organization_id
    product = Product.query.filter_by(id=product_id, organization_id=org_id,
                                       is_deleted=False).first_or_404()
    try:
        adjustment = float(request.form.get('adjustment', 0))
        product.stock_quantity = max(0, product.stock_quantity + adjustment)
        db.session.commit()
        action = 'added' if adjustment >= 0 else 'removed'
        flash(f'Stock {action} for "{product.name}". New qty: {product.stock_quantity}', 'productsuccess')
    except Exception as e:
        db.session.rollback()
        flash(f'Stock update failed: {str(e)}', 'producterror')

    return redirect(url_for('products.view_product', product_id=product_id))


# ─────────────────────────────────────────────────────────
# API – search products (used by quotation engine)
# ─────────────────────────────────────────────────────────
@products_bp.route('/api/search')
@login_required
def api_search():
    org_id = current_user.organization_id
    q = request.args.get('q', '').strip()
    products = Product.query.filter(
        Product.organization_id == org_id,
        Product.is_deleted == False,
        Product.status == ProductStatus.ACTIVE,
        Product.name.ilike(f'%{q}%')
    ).limit(20).all()

    return jsonify([{
        'id': p.id,
        'name': p.name,
        'sku': p.sku,
        'unit': p.unit,
        'selling_price': p.selling_price,
        'gst_rate': p.gst_rate,
        'category': p.category_display,
        'description': p.description or '',
    } for p in products])
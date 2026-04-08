from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from model import db, Expense, ExpenseCategory, ExpenseStatus, Employee, Project, UserRole
from datetime import datetime
import os
from werkzeug.utils import secure_filename
import uuid

expenses_bp = Blueprint('expenses', __name__, url_prefix='/expenses')

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'pdf'}
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@expenses_bp.route('/')
@login_required
def expenses_list():
    org_id = current_user.organization_id
    
    # Permission Logic
    if current_user.role in [UserRole.ADMIN, UserRole.MANAGER]:
        # Owner/Admin sees everyone's expenses in the organization
        expenses = Expense.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Expense.date.desc()).all()
    else:
        # Employees see only their own expenses
        if not current_user.employee:
            flash("Employee record not found for your user.", "profileerror")
            return redirect(url_for('home_page'))
        expenses = Expense.query.filter_by(employee_id=current_user.employee.id, is_deleted=False).order_by(Expense.date.desc()).all()
        
    return render_template('accounts/expenses_list.html', expenses=expenses)

@expenses_bp.route('/add', methods=['GET', 'POST'])
@login_required
def add_expense():
    org_id = current_user.organization_id
    if request.method == 'POST':
        title = request.form.get('title')
        amount = request.form.get('amount')
        category_val = request.form.get('category')
        project_id = request.form.get('project_id')
        description = request.form.get('description')
        
        # Date parsing
        date_str = request.form.get('date')
        date_obj = datetime.strptime(date_str, '%Y-%m-%d') if date_str else datetime.utcnow()

        if not title or not amount:
            flash("Title and amount are required.", "expenseerror")
            return redirect(url_for('expenses.add_expense'))

        # File Handling
        receipt_filename = None
        if 'receipt' in request.files:
            file = request.files['receipt']
            if file and file.filename != '' and allowed_file(file.filename):
                ext = file.filename.rsplit('.', 1)[1].lower()
                unique_name = f"{uuid.uuid4().hex}.{ext}"
                upload_dir = os.path.join(current_app.root_path, 'static', 'uploads', 'receipts')
                if not os.path.exists(upload_dir):
                    os.makedirs(upload_dir)
                file.save(os.path.join(upload_dir, unique_name))
                receipt_filename = unique_name

        category_map = {e.value: e for e in ExpenseCategory}
        
        new_expense = Expense(
            title=title,
            amount=float(amount),
            date=date_obj,
            category=category_map.get(category_val, ExpenseCategory.OTHER),
            description=description,
            receipt_path=receipt_filename,
            organization_id=org_id,
            employee_id=current_user.employee.id if current_user.employee else None
        )
        
        if project_id and project_id != 'none':
            new_expense.project_id = int(project_id)

        try:
            db.session.add(new_expense)
            db.session.commit()
            flash("Expense submitted successfully!", "expensesuccess")
            return redirect(url_for('expenses.expenses_list'))
        except Exception as e:
            db.session.rollback()
            flash(f"Error saving expense: {str(e)}", "expenseerror")
            return redirect(url_for('expenses.add_expense'))

    categories = [e.value for e in ExpenseCategory]
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    today_date = datetime.utcnow().strftime('%Y-%m-%d')
    return render_template('accounts/add_expense.html', categories=categories, projects=projects, today_date=today_date)

@expenses_bp.route('/status/<int:expense_id>', methods=['POST'])
@login_required
def update_status(expense_id):
    # Only Admin/Manager can update status
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        flash("Unauthorized.", "profileerror")
        return redirect(url_for('expenses.expenses_list'))

    expense = Expense.query.get_or_404(expense_id)
    if expense.organization_id != current_user.organization_id:
        flash("Unauthorized.", "profileerror")
        return redirect(url_for('expenses.expenses_list'))

    new_status = request.form.get('status')
    status_map = {e.value: e for e in ExpenseStatus}
    
    if new_status in status_map:
        expense.status = status_map[new_status]
        try:
            db.session.commit()
            flash(f"Expense {expense.title} updated to {new_status}.", "expensesuccess")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "expenseerror")
            
    return redirect(url_for('expenses.expenses_list'))

@expenses_bp.route('/view/<int:expense_id>')
@login_required
def view_expense(expense_id):
    org_id = current_user.organization_id
    expense = Expense.query.filter_by(id=expense_id, is_deleted=False).first_or_404()
    
    # Permission Check: Admin/Manager can see any organizational expense, 
    # Employee can only see their own.
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        if not current_user.employee or expense.employee_id != current_user.employee.id:
            flash("Unauthorized access.", "profileerror")
            return redirect(url_for('expenses.expenses_list'))
    
    # Safety Check: Must belong to the same organization
    if expense.organization_id != org_id:
        flash("Unauthorized access.", "profileerror")
        return redirect(url_for('expenses.expenses_list'))
        
    return render_template('accounts/view_expense.html', expense=expense, ExpenseStatus=ExpenseStatus, UserRole=UserRole)

@expenses_bp.route('/edit/<int:expense_id>', methods=['GET', 'POST'])
@login_required
def edit_expense(expense_id):
    expense = Expense.query.filter_by(id=expense_id, is_deleted=False).first_or_404()
    # Check permissions
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        if not current_user.employee or expense.employee_id != current_user.employee.id:
            flash("Unauthorized.", "profileerror")
            return redirect(url_for('expenses.expenses_list'))
            
    if request.method == 'POST':
        expense.title = request.form.get('title')
        expense.amount = float(request.form.get('amount'))
        expense.description = request.form.get('description')
        # Update project if selected
        proj_id = request.form.get('project_id')
        expense.project_id = int(proj_id) if proj_id and proj_id != 'none' else None
        
        try:
            db.session.commit()
            flash("Expense updated successfully!", "expensesuccess")
        except Exception as e:
            db.session.rollback()
            flash(f"Error: {str(e)}", "expenseerror")
        return redirect(url_for('expenses.expenses_list'))
        
    org_id = current_user.organization_id
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    categories = [e.value for e in ExpenseCategory]
    return render_template('accounts/edit_expense.html', expense=expense, projects=projects, categories=categories)

@expenses_bp.route('/delete/<int:expense_id>', methods=['POST'])
@login_required
def delete_expense(expense_id):
    expense = Expense.query.filter_by(id=expense_id, is_deleted=False).first_or_404()
    # Check permissions
    if current_user.role not in [UserRole.ADMIN, UserRole.MANAGER]:
        if not current_user.employee or expense.employee_id != current_user.employee.id:
            flash("Unauthorized.", "profileerror")
            return redirect(url_for('expenses.expenses_list'))
            
    try:
        expense.is_deleted = True
        db.session.commit()
        flash("Expense deleted successfully.", "expensesuccess")
    except Exception as e:
        db.session.rollback()
        flash(f"Error: {str(e)}", "expenseerror")
        
    return redirect(url_for('expenses.expenses_list'))

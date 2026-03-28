import re
from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from model import db, Employee, User, UserRole

employees_bp = Blueprint('employees', __name__)

@employees_bp.route('/employee')
@login_required
def employee_list():
    all_employees = Employee.query.filter_by(organization_id=current_user.organization_id, is_deleted=False).all()
    return render_template('employee/employee.html', employees=all_employees)

@employees_bp.route('/add-employee', methods=['GET', 'POST'])
@login_required
def add_employee():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))
        position = request.form.get('position', '').strip()

        if not all([name, email, phone_number, position]):
            flash('All required fields filled.', 'employeeerror')
            return redirect(url_for('employees.add_employee'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'employeeerror')
            return redirect(url_for('employees.add_employee'))

        new_user = User(
            username=name, email=email, phone_number=phone_number,
            password=generate_password_hash('Glass@123'),
            role=UserRole.STAFF, organization_id=current_user.organization_id
        )
        db.session.add(new_user)
        db.session.flush()

        new_emp = Employee(
            user_id=new_user.id, name=name, email=email,
            phone_number=phone_number, position=position,
            organization_id=current_user.organization_id
        )
        db.session.add(new_emp)
        db.session.commit()
        flash('Employee added! Pass: Glass@123', 'employeesuccess')
        return redirect(url_for('employees.employee_list'))

    return render_template('employee/add_employee.html')

@employees_bp.route('/edit-employee/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def edit_employee(employee_id):
    employee = Employee.query.filter_by(id=employee_id, organization_id=current_user.organization_id).first_or_404()
    if request.method == 'POST':
        employee.name = request.form.get('name', '').strip()
        employee.email = request.form.get('email', '').strip()
        employee.phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))
        employee.position = request.form.get('position', '').strip()
        
        if employee.user:
            employee.user.email = employee.email
            employee.user.phone_number = employee.phone_number
            
        try:
            db.session.commit()
            flash('Updated!', 'employeesuccess')
            return redirect(url_for('employees.employee_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {str(e)}', 'employeeerror')
            return redirect(url_for('employees.edit_employee', employee_id=employee_id))

    return render_template('employee/editemployee.html', employee=employee)

@employees_bp.route('/delete-employee/<int:employee_id>', methods=['POST'])
@login_required
def delete_employee(employee_id):
    employee = Employee.query.filter_by(id=employee_id, organization_id=current_user.organization_id).first_or_404()
    employee.is_deleted = True
    db.session.commit()
    flash('Employee deleted.', 'employeesuccess')
    return redirect(url_for('employees.employee_list'))

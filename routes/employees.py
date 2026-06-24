import re
import secrets
import string
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.security import generate_password_hash
from model import db, Employee, User, UserRole
from utils.security import require_roles

employees_bp = Blueprint("employees", __name__)


@employees_bp.route("/employee")
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def employee_list():
    all_employees = Employee.query.filter_by(
        organization_id=current_user.organization_id, is_deleted=False
    ).all()
    return render_template("employee/employee.html", employees=all_employees)


@employees_bp.route("/add-employee", methods=["GET", "POST"])
@login_required
@require_roles(UserRole.ADMIN)
def add_employee():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        phone_number = re.sub(r"\D", "", request.form.get("phone_number", ""))
        position = request.form.get("position", "").strip()

        if not all([name, email, phone_number, position]):
            flash("All required fields filled.", "employeeerror")
            return redirect(url_for("employees.add_employee"))

        # DUPLICATE CHECKS
        existing_user = User.query.filter(
            (User.email == email) | (User.phone_number == phone_number),
            User.organization_id == current_user.organization_id
        ).first()
        if existing_user:
            if existing_user.email == email:
                flash("Email already registered.", "employeeerror")
            else:
                flash("Phone number already registered.", "employeeerror")
            return redirect(url_for("employees.add_employee"))

        try:
            alphabet = string.ascii_letters + string.digits + "!@#$"
            temp_password = ''.join(secrets.choice(alphabet) for _ in range(12))

            new_user = User(
                username=name,
                email=email,
                phone_number=phone_number,
                password=generate_password_hash(temp_password),
                role=UserRole.EMPLOYEE,
                organization_id=current_user.organization_id,
                must_change_password=True,
            )
            db.session.add(new_user)
            db.session.flush()

            new_emp = Employee(
                user_id=new_user.id,
                name=name,
                email=email,
                phone_number=phone_number,
                position=position,
                organization_id=current_user.organization_id,
                temp_password=temp_password,
            )
            db.session.add(new_emp)
            db.session.commit()
            flash(f"Employee added! Temporary password: {temp_password} — Must change on first login.", "employeesuccess")
            return redirect(url_for("employees.employee_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "employeeerror")
            return redirect(url_for("employees.add_employee"))

    return render_template("employee/add_employee.html")

@employees_bp.route("/view-employee/<int:employee_id>")
@login_required
@require_roles(UserRole.ADMIN, UserRole.MANAGER)
def view_employee(employee_id):
    employee = Employee.query.filter_by(
        id=employee_id, organization_id=current_user.organization_id
    ).first_or_404()
    return render_template("employee/view_employee.html", employee=employee)

@employees_bp.route("/edit-employee/<int:employee_id>", methods=["GET", "POST"])
@login_required
@require_roles(UserRole.ADMIN)
def edit_employee(employee_id):
    employee = Employee.query.filter_by(
        id=employee_id, organization_id=current_user.organization_id
    ).first_or_404()
    if request.method == "POST":
        employee.name = request.form.get("name", "").strip()
        employee.email = request.form.get("email", "").strip()
        employee.phone_number = re.sub(r"\D", "", request.form.get("phone_number", ""))
        employee.position = request.form.get("position", "").strip()

        if employee.user:
            employee.user.email = employee.email
            employee.user.phone_number = employee.phone_number

        try:
            db.session.commit()
            flash("Updated!", "employeesuccess")
            return redirect(url_for("employees.employee_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "employeeerror")
            return redirect(url_for("employees.edit_employee", employee_id=employee_id))

    return render_template("employee/editemployee.html", employee=employee)


@employees_bp.route("/delete-employee/<int:employee_id>", methods=["POST"])
@login_required
@require_roles(UserRole.ADMIN)
def delete_employee(employee_id):
    employee = Employee.query.filter_by(
        id=employee_id, organization_id=current_user.organization_id
    ).first_or_404()
    employee.is_deleted = True
    db.session.commit()
    flash("Employee deleted.", "employeesuccess")
    return redirect(url_for("employees.employee_list"))

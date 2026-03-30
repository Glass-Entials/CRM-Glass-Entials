from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from model import db, Project, Employee, Customer, ProjectStatus, ProjectWorkType, ProjectCategory
from utils.activity import log_activity

projects_bp = Blueprint('projects', __name__)

@projects_bp.route('/projects')
@login_required
def projects_list():
    org_id = current_user.organization_id
    all_projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Project.created_at.desc()).all()
    all_employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    all_customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template('projects/project.html', projects=all_projects, employees=all_employees, customers=all_customers)

@projects_bp.route('/add-project', methods=['GET', 'POST'])
@login_required
def add_project():
    org_id = current_user.organization_id
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        status_val = request.form.get('status', 'Planning')
        assigned_to_id = request.form.get('assigned_to')
        customer_id = request.form.get('customer_id')

        assigned_to_id = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None
        customer_id = int(customer_id) if customer_id and customer_id != 'none' else None

        if not name:
            flash('Project name is required.', 'projecterror')
            return redirect(url_for('projects.add_project'))

        status_map = {e.value: e for e in ProjectStatus}
        work_type_map = {e.value: e for e in ProjectWorkType}
        category_map = {e.value: e for e in ProjectCategory}
        
        new_project = Project(
            name=name,
            description=description,
            status=status_map.get(status_val, ProjectStatus.PLANNING),
            work_type=work_type_map.get(request.form.get('work_type', 'Glass'), ProjectWorkType.GLASS),
            category=category_map.get(request.form.get('category', 'Commercial'), ProjectCategory.COMMERCIAL),
            customer_id=customer_id,
            assigned_to=assigned_to_id,
            organization_id=org_id,
            created_by=current_user.employee.id
        )
        
        try:
            db.session.add(new_project)
            db.session.flush()
            log_activity('project_added', 'project', new_project.name, org_id, current_user.employee.id, new_project.id)
            db.session.commit()
            flash('Project created successfully!', 'projectsuccess')
            return redirect(url_for('projects.projects_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving project: {str(e)}', 'projecterror')
            return redirect(url_for('projects.add_project'))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    
    # Pre-select customer if customer_id is provided in query params
    query_customer_id = request.args.get('customer_id', type=int)
    
    return render_template('projects/addproject.html', employees=employees, customers=customers, pre_selected_customer_id=query_customer_id)

@projects_bp.route('/edit-project/<int:project_id>', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(id=project_id, organization_id=org_id, is_deleted=False).first_or_404()
    
    if request.method == 'POST':
        project.name = request.form.get('name', '').strip()
        project.description = request.form.get('description', '').strip()
        
        assigned_to_id = request.form.get('assigned_to')
        project.assigned_to = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None
        
        customer_id = request.form.get('customer_id')
        project.customer_id = int(customer_id) if customer_id and customer_id != 'none' else None

        status_map = {e.value: e for e in ProjectStatus}
        work_type_map = {e.value: e for e in ProjectWorkType}
        category_map = {e.value: e for e in ProjectCategory}
        project.status = status_map.get(request.form.get('status'), ProjectStatus.PLANNING)
        project.work_type = work_type_map.get(request.form.get('work_type'), ProjectWorkType.GLASS)
        project.category = category_map.get(request.form.get('category'), ProjectCategory.COMMERCIAL)
        project.updated_by = current_user.employee.id

        try:
            log_activity('project_updated', 'project', project.name, org_id, current_user.employee.id, project.id)
            db.session.commit()
            flash('Project updated successfully!', 'projectsuccess')
            return redirect(url_for('projects.projects_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating project: {str(e)}', 'projecterror')
            return redirect(url_for('projects.edit_project', project_id=project_id))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template('projects/editproject.html', project=project, employees=employees, customers=customers)

@projects_bp.route('/delete-project/<int:project_id>', methods=['POST'])
@login_required
def delete_project(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(id=project_id, organization_id=org_id, is_deleted=False).first_or_404()
    project.is_deleted = True
    try:
        log_activity('project_deleted', 'project', project.name, org_id, current_user.employee.id, project.id)
        db.session.commit()
        flash('Project deleted.', 'projectsuccess')
    except Exception as e:
        db.session.rollback()
        flash('Failed to delete project.', 'projecterror')
    return redirect(url_for('projects.projects_list'))

@projects_bp.route('/view-project/<int:project_id>')
@login_required
def view_project(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(id=project_id, organization_id=org_id, is_deleted=False).first_or_404()
    return render_template('projects/project_profile.html', project=project)

from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from model import db, Task, Employee, Project, Lead, TaskStatus, DailyTask
from utils.activity import log_activity
from datetime import datetime

tasks_bp = Blueprint('tasks', __name__)

@tasks_bp.route('/tasks')
@login_required
def tasks_list():
    org_id = current_user.organization_id
    all_tasks = Task.query.filter_by(organization_id=org_id).order_by(Task.created_at.desc()).all()
    all_employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    all_projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template('tasks/tasks.html', tasks=all_tasks, employees=all_employees, projects=all_projects)

@tasks_bp.route('/add-task', methods=['GET', 'POST'])
@login_required
def add_task():
    org_id = current_user.organization_id
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        description = request.form.get('description', '').strip()
        status_val = request.form.get('status', 'Pending')
        
        due_date_str = request.form.get('due_date')
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d') if due_date_str else None

        assigned_to_id = request.form.get('assigned_to')
        project_id = request.form.get('project_id')
        lead_id = request.form.get('lead_id')

        assigned_to_id = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None
        project_id = int(project_id) if project_id and project_id != 'none' else None
        lead_id = int(lead_id) if lead_id and lead_id != 'none' else None

        if not title:
            flash('Task title is required.', 'taskerror')
            return redirect(url_for('tasks.add_task'))

        status_map = {e.value: e for e in TaskStatus}
        
        new_task = Task(
            title=title,
            description=description,
            status=status_map.get(status_val, TaskStatus.PENDING),
            due_date=due_date,
            assigned_to=assigned_to_id,
            project_id=project_id,
            lead_id=lead_id,
            organization_id=org_id,
            created_by=current_user.id
        )
        
        try:
            db.session.add(new_task)
            db.session.flush()
            
            # Log activity. We use 'task' as entity_type, we can log it on project if it's related to a project too
            actor_id = current_user.employee.id if current_user.employee else None
            log_activity('task_added', 'task', new_task.title, org_id, actor_id, new_task.id)
            
            db.session.commit()
            flash('Task created successfully!', 'tasksuccess')
            return redirect(url_for('tasks.tasks_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving task: {str(e)}', 'taskerror')
            return redirect(url_for('tasks.add_task'))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).all()
    
    # Pre-selects if coming from another module
    query_project_id = request.args.get('project_id', type=int)
    query_lead_id = request.args.get('lead_id', type=int)
    
    return render_template('tasks/addtask.html', employees=employees, projects=projects, leads=leads, 
                           pre_selected_project_id=query_project_id, pre_selected_lead_id=query_lead_id)

@tasks_bp.route('/edit-task/<int:task_id>', methods=['GET', 'POST'])
@login_required
def edit_task(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    
    if request.method == 'POST':
        task.title = request.form.get('title', '').strip()
        task.description = request.form.get('description', '').strip()
        
        due_date_str = request.form.get('due_date')
        task.due_date = datetime.strptime(due_date_str, '%Y-%m-%d') if due_date_str else None
        
        assigned_to_id = request.form.get('assigned_to')
        task.assigned_to = int(assigned_to_id) if assigned_to_id and assigned_to_id != 'unassigned' else None
        
        project_id = request.form.get('project_id')
        task.project_id = int(project_id) if project_id and project_id != 'none' else None
        
        lead_id = request.form.get('lead_id')
        task.lead_id = int(lead_id) if lead_id and lead_id != 'none' else None

        status_map = {e.value: e for e in TaskStatus}
        task.status = status_map.get(request.form.get('status'), TaskStatus.PENDING)

        try:
            actor_id = current_user.employee.id if current_user.employee else None
            log_activity('task_updated', 'task', task.title, org_id, actor_id, task.id)
            db.session.commit()
            flash('Task updated successfully!', 'tasksuccess')
            return redirect(url_for('tasks.tasks_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating task: {str(e)}', 'taskerror')
            return redirect(url_for('tasks.edit_task', task_id=task_id))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).all()
    
    return render_template('tasks/edittask.html', task=task, employees=employees, projects=projects, leads=leads)

@tasks_bp.route('/delete-task/<int:task_id>', methods=['POST'])
@login_required
def delete_task(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    try:
        actor_id = current_user.employee.id if current_user.employee else None
        log_activity('task_deleted', 'task', task.title, org_id, actor_id, task.id)
        db.session.delete(task)
        db.session.commit()
        flash('Task deleted.', 'tasksuccess')
    except Exception as e:
        db.session.rollback()
        flash('Failed to delete task.', 'taskerror')
    return redirect(url_for('tasks.tasks_list'))

@tasks_bp.route('/update-task-status/<int:task_id>', methods=['POST'])
@login_required
def update_task_status(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    
    status_val = request.form.get('status')
    if status_val:
        status_map = {e.value: e for e in TaskStatus}
        if status_val in status_map:
            task.status = status_map[status_val]
            try:
                db.session.commit()
                return {'success': True, 'message': 'Status updated'}
            except Exception as e:
                db.session.rollback()
                return {'success': False, 'message': str(e)}, 500
    return {'success': False, 'message': 'Invalid status'}, 400

@tasks_bp.route('/view-task/<int:task_id>')
@login_required
def view_task(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    return render_template('tasks/task_profile.html', task=task)

@tasks_bp.route('/daily-tasks')
@login_required
def daily_tasks_list():
    org_id = current_user.organization_id
    date_filter = request.args.get('date')
    emp_filter = request.args.get('employee_id')
    
    query = DailyTask.query.filter_by(organization_id=org_id)
    
    # Role-based restriction
    if current_user.role.value not in ['admin', 'manager']:
        employee = Employee.query.filter_by(user_id=current_user.id).first()
        if employee:
            query = query.filter_by(employee_id=employee.id)
        else:
            return render_template('tasks/daily_tasks.html', daily_tasks=[], employees=[])
    elif emp_filter:
        query = query.filter_by(employee_id=emp_filter)
        
    # Date Filtering
    if date_filter:
        try:
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
            query = query.filter_by(date=filter_date)
        except:
            pass
            
    all_daily_tasks = query.order_by(DailyTask.date.desc()).all()
    employees = Employee.query.filter_by(organization_id=org_id).all() if current_user.role.value in ['admin', 'manager'] else []
    
    return render_template('tasks/daily_tasks.html', 
                          daily_tasks=all_daily_tasks, 
                          employees=employees,
                          selected_date=date_filter,
                          selected_emp=emp_filter)

@tasks_bp.route('/add-daily-task', methods=['GET', 'POST'])
@login_required
def add_daily_task():
    org_id = current_user.organization_id
    employee = Employee.query.filter_by(user_id=current_user.id).first()
    
    if not employee:
        flash('Employee profile not found.', 'taskerror')
        return redirect(url_for('tasks.daily_tasks_list'))

    if request.method == 'POST':
        date_str = request.form.get('date')
        description = request.form.get('description', '').strip()
        hours = request.form.get('hours_spent')
        project_id = request.form.get('project_id')
        work_category = request.form.get('work_category', 'General')
        
        if not description:
            flash('Task description is required.', 'taskerror')
            return redirect(url_for('tasks.add_daily_task'))
            
        try:
            task_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else datetime.utcnow().date()
            new_daily_task = DailyTask(
                employee_id=employee.id,
                date=task_date,
                task_description=description,
                hours_spent=float(hours) if hours else None,
                work_category=work_category,
                project_id=int(project_id) if project_id and project_id != 'none' else None,
                organization_id=org_id
            )
            db.session.add(new_daily_task)
            db.session.commit()
            flash('Daily task added successfully!', 'tasksuccess')
            return redirect(url_for('tasks.daily_tasks_list'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error adding daily task: {str(e)}', 'taskerror')
            return redirect(url_for('tasks.add_daily_task'))

    from datetime import date
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template('tasks/add_daily_task.html', today_date=date.today().strftime('%Y-%m-%d'), projects=projects)

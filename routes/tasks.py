from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app, jsonify
from flask_login import login_required, current_user
from model import db, Task, Employee, Project, Lead, TaskStatus, DailyTask, UserRole, TaskActivity, TaskActivityType, CallResult
from utils.activity import log_activity
from utils.notifications import create_notification
from utils.security import tenant_record_id
from datetime import datetime

tasks_bp = Blueprint("tasks", __name__)


@tasks_bp.route("/tasks")
@login_required
def tasks_list():
    from datetime import date
    org_id = current_user.organization_id
    
    query = Task.query.filter_by(organization_id=org_id)
    
    assigned_to = request.args.get("assigned_to")
    if assigned_to:
        query = query.filter_by(assigned_to=int(assigned_to))
        
    completed_today = request.args.get("completed_today")
    if completed_today:
        query = query.filter(Task.status == TaskStatus.COMPLETED, db.func.date(Task.updated_at) == date.today())
        
    overdue = request.args.get("overdue")
    if overdue:
        query = query.filter(Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]), db.func.date(Task.due_date) < date.today())
    
    all_tasks = query.order_by(Task.created_at.desc()).all()
    all_employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    all_projects = Project.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    return render_template(
        "tasks/tasks.html",
        tasks=all_tasks,
        employees=all_employees,
        projects=all_projects,
    )


@tasks_bp.route("/add-task", methods=["GET", "POST"])
@login_required
def add_task():
    org_id = current_user.organization_id
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        status_val = request.form.get("status", "Pending")

        due_date_str = request.form.get("due_date")
        due_date = datetime.strptime(due_date_str, "%Y-%m-%d") if due_date_str else None

        assigned_to_id = request.form.get("assigned_to")
        project_id = request.form.get("project_id")
        lead_id = request.form.get("lead_id")

        assigned_to_id = tenant_record_id(
            Employee, assigned_to_id, org_id, is_deleted=False
        )
        project_id = tenant_record_id(Project, project_id, org_id, is_deleted=False)
        lead_id = tenant_record_id(Lead, lead_id, org_id, is_deleted=False)

        if not title:
            flash("Task title is required.", "taskerror")
            return redirect(url_for("tasks.add_task"))

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
            created_by=current_user.id,
        )

        try:
            db.session.add(new_task)
            db.session.flush()

            # Notification for assignment
            if assigned_to_id:
                create_notification(
                    recipient_id=assigned_to_id,
                    title="New Task Assigned",
                    message=f"You have been assigned a new task: {new_task.title}",
                    link=url_for("tasks.view_task", task_id=new_task.id),
                    sender_id=(
                        current_user.employee.id if current_user.employee else None
                    ),
                    organization_id=org_id,
                )

            # Log activity. We use 'task' as entity_type, we can log it on project if it's related to a project too
            actor_id = current_user.employee.id if current_user.employee else None
            log_activity(
                "task_added", "task", new_task.title, org_id, actor_id, new_task.id
            )

            # Handle file uploads if present (supports multiple)
            if "file" in request.files:
                from utils.documents import handle_file_upload
                for file in request.files.getlist("file"):
                    if file and file.filename != "":
                        handle_file_upload(
                            file=file,
                            entity_type="task",
                            entity_id=new_task.id,
                            organization_id=org_id,
                            uploader_id=(
                                current_user.employee.id if current_user.employee else None
                            ),
                            description=f"Attached during task creation: {new_task.title}",
                        )

            db.session.commit()
            flash("Task created successfully!", "tasksuccess")
            return redirect(url_for("tasks.tasks_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "taskerror")
            return redirect(url_for("tasks.add_task"))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).all()

    # Pre-selects if coming from another module
    query_project_id = tenant_record_id(
        Project, request.args.get("project_id"), org_id, is_deleted=False
    )
    query_lead_id = tenant_record_id(
        Lead, request.args.get("lead_id"), org_id, is_deleted=False
    )

    return render_template(
        "tasks/addtask.html",
        employees=employees,
        projects=projects,
        leads=leads,
        pre_selected_project_id=query_project_id,
        pre_selected_lead_id=query_lead_id,
    )


@tasks_bp.route("/edit-task/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit_task(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()

    if request.method == "POST":
        task.title = request.form.get("title", "").strip()
        task.description = request.form.get("description", "").strip()

        due_date_str = request.form.get("due_date")
        task.due_date = (
            datetime.strptime(due_date_str, "%Y-%m-%d") if due_date_str else None
        )

        # Parse assignment from form
        raw_assigned = request.form.get("assigned_to")
        new_assigned_id = tenant_record_id(
            Employee, raw_assigned, org_id, is_deleted=False
        )

        try:
            # Check if assignment changed
            old_assignee = task.assigned_to
            task.assigned_to = new_assigned_id

            project_id = request.form.get("project_id")
            task.project_id = tenant_record_id(
                Project, project_id, org_id, is_deleted=False
            )

            lead_id = request.form.get("lead_id")
            task.lead_id = tenant_record_id(Lead, lead_id, org_id, is_deleted=False)

            status_map = {e.value: e for e in TaskStatus}
            new_status_val = request.form.get("status")
            if new_status_val:
                can_change_status = False
                if current_user.employee and current_user.employee.id in (task.created_by, task.assigned_to):
                    can_change_status = True
                
                if can_change_status:
                    task.status = status_map.get(new_status_val, task.status)

            if task.assigned_to and task.assigned_to != old_assignee:
                create_notification(
                    recipient_id=task.assigned_to,
                    title="Task Assigned to You",
                    message=f"Task '{task.title}' has been assigned to you.",
                    link=url_for("tasks.view_task", task_id=task.id),
                    sender_id=(
                        current_user.employee.id if current_user.employee else None
                    ),
                    organization_id=org_id,
                )

            actor_id = current_user.employee.id if current_user.employee else None
            log_activity("task_updated", "task", task.title, org_id, actor_id, task.id)

            # Handle file uploads if present (supports multiple)
            if "file" in request.files:
                from utils.documents import handle_file_upload
                for file in request.files.getlist("file"):
                    if file and file.filename != "":
                        handle_file_upload(
                            file=file,
                            entity_type="task",
                            entity_id=task.id,
                            organization_id=org_id,
                            uploader_id=(
                                current_user.employee.id if current_user.employee else None
                            ),
                            description=f"Attached during task edit: {task.title}",
                        )

            db.session.commit()
            flash("Task updated successfully!", "tasksuccess")
            return redirect(url_for("tasks.tasks_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "taskerror")
            return redirect(url_for("tasks.edit_task", task_id=task_id))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).all()

    return render_template(
        "tasks/edittask.html",
        task=task,
        employees=employees,
        projects=projects,
        leads=leads,
    )


@tasks_bp.route("/delete-task/<int:task_id>", methods=["POST"])
@login_required
def delete_task(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    try:
        actor_id = current_user.employee.id if current_user.employee else None
        log_activity("task_deleted", "task", task.title, org_id, actor_id, task.id)
        db.session.delete(task)
        db.session.commit()
        flash("Task deleted.", "tasksuccess")
    except Exception as e:
        db.session.rollback()
        flash("Failed to delete task.", "taskerror")
    return redirect(url_for("tasks.tasks_list"))


@tasks_bp.route("/update-task-status/<int:task_id>", methods=["POST"])
@login_required
def update_task_status(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()

    # Authorization: only admin/manager, task creator, or assigned employee can update
    is_admin_or_manager = current_user.role in [UserRole.ADMIN, UserRole.MANAGER]
    emp_id = current_user.employee.id if current_user.employee else None
    is_assignee = emp_id is not None and task.assigned_to == emp_id
    is_creator = task.created_by == current_user.id

    if not (is_admin_or_manager or is_assignee or is_creator):
        return {"success": False, "message": "Unauthorized"}, 403

    status_val = request.form.get("status")
    if status_val:
        status_map = {e.value: e for e in TaskStatus}
        if status_val in status_map:
            task.status = status_map[status_val]
            try:
                db.session.commit()
                return {"success": True, "message": "Status updated"}
            except Exception as e:
                db.session.rollback()
                return {"success": False, "message": str(e)}, 500
    return {"success": False, "message": "Invalid status"}, 400


@tasks_bp.route("/view-task/<int:task_id>")
@login_required
def view_task(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    
    # Fetch activities newest-first, excluding soft-deleted, paginated
    page = request.args.get("act_page", 1, type=int)
    filter_type = request.args.get("filter", "all")
    search_q = request.args.get("q", "").strip()
    
    act_query = TaskActivity.query.filter_by(
        task_id=task_id,
        organization_id=org_id,
        is_deleted=False,
    )
    if filter_type != "all":
        type_map = {e.value.lower().replace(" ", "_"): e for e in TaskActivityType}
        matched = type_map.get(filter_type)
        if matched:
            act_query = act_query.filter(TaskActivity.activity_type == matched)
    if search_q:
        act_query = act_query.filter(TaskActivity.message.ilike(f"%{search_q}%"))
    
    activity_pagination = act_query.order_by(
        TaskActivity.created_at.desc()
    ).paginate(page=page, per_page=25, error_out=False)
    
    employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    
    is_manager = current_user.role in [UserRole.ADMIN, UserRole.MANAGER]
    
    return render_template(
        "tasks/task_profile.html",
        task=task,
        activity_pagination=activity_pagination,
        activities=activity_pagination.items,
        employees=employees,
        TaskActivityType=TaskActivityType,
        CallResult=CallResult,
        is_manager=is_manager,
        filter_type=filter_type,
        search_q=search_q,
    )


@tasks_bp.route("/daily-tasks")
@login_required
def daily_tasks_list():
    org_id = current_user.organization_id
    date_filter = request.args.get("date")
    emp_filter = request.args.get("employee_id")

    query = DailyTask.query.filter_by(organization_id=org_id)

    # Role-based restriction
    if current_user.role.value not in ["admin", "manager"]:
        employee = Employee.query.filter_by(user_id=current_user.id).first()
        if employee:
            query = query.filter_by(employee_id=employee.id)
        else:
            return render_template(
                "tasks/daily_tasks.html", daily_tasks=[], employees=[]
            )
    elif emp_filter:
        emp_id = tenant_record_id(Employee, emp_filter, org_id, is_deleted=False)
        query = query.filter_by(employee_id=emp_id)

    # Date Filtering
    if date_filter:
        try:
            filter_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            query = query.filter_by(date=filter_date)
        except:
            pass

    all_daily_tasks = query.order_by(DailyTask.date.desc()).all()
    employees = (
        Employee.query.filter_by(organization_id=org_id).all()
        if current_user.role.value in ["admin", "manager"]
        else []
    )

    return render_template(
        "tasks/daily_tasks.html",
        daily_tasks=all_daily_tasks,
        employees=employees,
        selected_date=date_filter,
        selected_emp=emp_filter,
    )


@tasks_bp.route("/add-daily-task", methods=["GET", "POST"])
@login_required
def add_daily_task():
    org_id = current_user.organization_id
    employee = Employee.query.filter_by(user_id=current_user.id).first()

    if not employee:
        flash("Employee profile not found.", "taskerror")
        return redirect(url_for("tasks.daily_tasks_list"))

    if request.method == "POST":
        date_str = request.form.get("date")
        description = request.form.get("description", "").strip()
        hours = request.form.get("hours_spent")
        project_id = request.form.get("project_id")
        work_category = request.form.get("work_category", "General")

        if not description:
            flash("Task description is required.", "taskerror")
            return redirect(url_for("tasks.add_daily_task"))

        try:
            task_date = (
                datetime.strptime(date_str, "%Y-%m-%d").date()
                if date_str
                else datetime.utcnow().date()
            )
            new_daily_task = DailyTask(
                employee_id=employee.id,
                date=task_date,
                task_description=description,
                hours_spent=float(hours) if hours else None,
                work_category=work_category,
                project_id=tenant_record_id(
                    Project, project_id, org_id, is_deleted=False
                ),
                organization_id=org_id,
            )
            db.session.add(new_daily_task)
            db.session.flush()

            if "file" in request.files:
                file = request.files["file"]
                if file and file.filename != "":
                    from utils.documents import handle_file_upload

                    handle_file_upload(
                        file=file,
                        entity_type="daily_task",
                        entity_id=new_daily_task.id,
                        organization_id=org_id,
                        uploader_id=(
                            current_user.employee.id if current_user.employee else None
                        ),
                        description=f"Attached to daily log: {task_date}",
                    )

            db.session.commit()
            flash("Daily task added successfully!", "tasksuccess")
            return redirect(url_for("tasks.daily_tasks_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "taskerror")
            return redirect(url_for("tasks.add_daily_task"))

    from datetime import date

    projects = Project.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template(
        "tasks/add_daily_task.html",
        today_date=date.today().strftime("%Y-%m-%d"),
        projects=projects,
    )


# ═══════════════════════════════════════════════════════════
#  TASK ACTIVITY TIMELINE ROUTES
# ═══════════════════════════════════════════════════════════

import re as _re


def _parse_mentions(message, org_id):
    """Return list of Employee objects mentioned with @Name in message."""
    names = _re.findall(r"@([\w\s]+?)(?=\s|$|@|,|\.|!|\?)", message)
    mentioned = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        emp = Employee.query.filter_by(
            name=name, organization_id=org_id, is_deleted=False
        ).first()
        if emp:
            mentioned.append(emp)
    return mentioned


@tasks_bp.route("/task/<int:task_id>/add-activity", methods=["POST"])
@login_required
def add_task_activity(task_id):
    org_id = current_user.organization_id
    task = Task.query.filter_by(id=task_id, organization_id=org_id).first_or_404()
    emp = current_user.employee
    if not emp:
        flash("Employee profile not found.", "taskerror")
        return redirect(url_for("tasks.view_task", task_id=task_id))

    activity_type_val = request.form.get("activity_type", "Comment")
    call_result_val = request.form.get("call_result", "").strip()
    message = request.form.get("message", "").strip()
    next_fu_str = request.form.get("next_follow_up_datetime", "").strip()

    if not message:
        flash("Activity message cannot be empty.", "taskerror")
        return redirect(url_for("tasks.view_task", task_id=task_id))

    type_map = {e.value: e for e in TaskActivityType}
    cr_map = {e.value: e for e in CallResult}

    activity_type = type_map.get(activity_type_val, TaskActivityType.COMMENT)
    call_result = cr_map.get(call_result_val) if call_result_val else None
    next_follow_up = None
    if next_fu_str:
        try:
            next_follow_up = datetime.strptime(next_fu_str, "%Y-%m-%dT%H:%M")
        except ValueError:
            pass

    try:
        activity = TaskActivity(
            task_id=task_id,
            organization_id=org_id,
            employee_id=emp.id,
            activity_type=activity_type,
            call_result=call_result,
            message=message,
            next_follow_up_datetime=next_follow_up,
        )
        db.session.add(activity)
        db.session.flush()  # get activity.id before commit

        # Handle file attachment
        if "attachment" in request.files:
            file = request.files["attachment"]
            if file and file.filename:
                from utils.documents import handle_file_upload
                handle_file_upload(
                    file=file,
                    entity_type="task_activity",
                    entity_id=activity.id,
                    organization_id=org_id,
                    uploader_id=emp.id,
                    description=f"Activity attachment – {activity_type.value}",
                )

        # Auto-create follow-up reminder notification
        if next_follow_up and task.assigned_to:
            create_notification(
                recipient_id=task.assigned_to,
                title="Follow-up Reminder",
                message=(
                    f"Reminder: Follow up on task '{task.title}' scheduled for "
                    f"{next_follow_up.strftime('%d %b %Y at %H:%M')}."
                ),
                link=url_for("tasks.view_task", task_id=task.id),
                sender_id=emp.id,
                organization_id=org_id,
            )

        # Mention notifications – @EmployeeName
        mentioned = _parse_mentions(message, org_id)
        for mentioned_emp in mentioned:
            if mentioned_emp.id != emp.id:
                create_notification(
                    recipient_id=mentioned_emp.id,
                    title="You were mentioned in a Task",
                    message=f"{emp.name} mentioned you in task '{task.title}'.",
                    link=url_for("tasks.view_task", task_id=task.id),
                    sender_id=emp.id,
                    organization_id=org_id,
                )

        # Notify task assignee about new activity (if not the one who added it)
        if task.assigned_to and task.assigned_to != emp.id:
            create_notification(
                recipient_id=task.assigned_to,
                title=f"New Activity on Task: {task.title}",
                message=(
                    f"{emp.name} logged a {activity_type.value} on task '{task.title}': "
                    f"{message[:100]}{'...' if len(message) > 100 else ''}"
                ),
                link=url_for("tasks.view_task", task_id=task.id),
                sender_id=emp.id,
                organization_id=org_id,
            )

        # Also notify the task creator (via their employee profile) if different from assignee and actor
        if task.creator and task.creator.employee:
            creator_emp_id = task.creator.employee.id
            already_notified = {task.assigned_to, emp.id}
            if creator_emp_id not in already_notified:
                create_notification(
                    recipient_id=creator_emp_id,
                    title=f"New Activity on Task: {task.title}",
                    message=(
                        f"{emp.name} logged a {activity_type.value} on task '{task.title}': "
                        f"{message[:100]}{'...' if len(message) > 100 else ''}"
                    ),
                    link=url_for("tasks.view_task", task_id=task.id),
                    sender_id=emp.id,
                    organization_id=org_id,
                )

        db.session.commit()
        flash("Activity logged successfully.", "tasksuccess")
    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"\n[ACTIVITY ERROR] {e}")
        traceback.print_exc()
        current_app.logger.error(f"Error adding task activity: {e}", exc_info=True)
        flash("Failed to log activity. Please try again.", "taskerror")

    return redirect(url_for("tasks.view_task", task_id=task_id))


@tasks_bp.route("/task/activity/<int:activity_id>/edit", methods=["POST"])
@login_required
def edit_task_activity(activity_id):
    org_id = current_user.organization_id
    activity = TaskActivity.query.filter_by(
        id=activity_id, organization_id=org_id, is_deleted=False
    ).first_or_404()

    emp = current_user.employee
    is_manager = current_user.role in [UserRole.ADMIN, UserRole.MANAGER]

    # Only author or manager can edit
    if not is_manager and (not emp or activity.employee_id != emp.id):
        flash("You are not allowed to edit this activity.", "taskerror")
        return redirect(url_for("tasks.view_task", task_id=activity.task_id))

    message = request.form.get("message", "").strip()
    if not message:
        flash("Activity message cannot be empty.", "taskerror")
        return redirect(url_for("tasks.view_task", task_id=activity.task_id))

    try:
        activity.message = message
        next_fu_str = request.form.get("next_follow_up_datetime", "").strip()
        activity.next_follow_up_datetime = (
            datetime.strptime(next_fu_str, "%Y-%m-%dT%H:%M") if next_fu_str else None
        )
        db.session.commit()
        flash("Activity updated.", "tasksuccess")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error editing activity: {e}", exc_info=True)
        flash("Failed to update activity.", "taskerror")

    return redirect(url_for("tasks.view_task", task_id=activity.task_id))


@tasks_bp.route("/task/activity/<int:activity_id>/delete", methods=["POST"])
@login_required
def delete_task_activity(activity_id):
    org_id = current_user.organization_id
    activity = TaskActivity.query.filter_by(
        id=activity_id, organization_id=org_id, is_deleted=False
    ).first_or_404()

    emp = current_user.employee
    is_manager = current_user.role in [UserRole.ADMIN, UserRole.MANAGER]

    if not is_manager and (not emp or activity.employee_id != emp.id):
        flash("Not authorized to delete this activity.", "taskerror")
        return redirect(url_for("tasks.view_task", task_id=activity.task_id))

    task_id = activity.task_id
    try:
        activity.is_deleted = True  # Soft delete only
        db.session.commit()
        flash("Activity removed.", "tasksuccess")
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error deleting activity: {e}", exc_info=True)
        flash("Failed to remove activity.", "taskerror")

    return redirect(url_for("tasks.view_task", task_id=task_id))

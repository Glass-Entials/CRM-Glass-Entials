from flask import Blueprint, render_template, request, flash, redirect, url_for
from flask_login import login_required, current_user
from model import (
    db,
    Project,
    Employee,
    Customer,
    ProjectStatus,
    ProjectWorkType,
    ProjectCategory,
)
from utils.activity import log_activity, build_changes
from utils.notifications import create_notification
from utils.security import tenant_record_id

projects_bp = Blueprint("projects", __name__)


@projects_bp.route("/projects")
@login_required
def projects_list():
    org_id = current_user.organization_id
    query = Project.query.filter_by(organization_id=org_id, is_deleted=False)
    
    assigned_to = request.args.get("assigned_to")
    if assigned_to:
        query = query.filter(Project.assigned_to == int(assigned_to))
        
    all_projects = query.order_by(Project.created_at.desc()).all()
    all_employees = Employee.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    all_customers = Customer.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).all()
    return render_template(
        "projects/project.html",
        projects=all_projects,
        employees=all_employees,
        customers=all_customers,
    )


@projects_bp.route("/add-project", methods=["GET", "POST"])
@login_required
def add_project():
    org_id = current_user.organization_id
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        status_val = request.form.get("status", "Planning")
        assigned_to_id = request.form.get("assigned_to")
        customer_id = request.form.get("customer_id")

        assigned_to_id = tenant_record_id(
            Employee, assigned_to_id, org_id, is_deleted=False
        )
        customer_id = tenant_record_id(
            Customer, customer_id, org_id, is_deleted=False
        )

        if not name:
            flash("Project name is required.", "projecterror")
            return redirect(url_for("projects.add_project"))

        status_map = {e.value: e for e in ProjectStatus}
        work_type_map = {e.value: e for e in ProjectWorkType}
        category_map = {e.value: e for e in ProjectCategory}

        new_project = Project(
            name=name,
            description=description,
            status=status_map.get(status_val, ProjectStatus.PLANNING),
            work_type=work_type_map.get(
                request.form.get("work_type", "Glass"), ProjectWorkType.GLASS
            ),
            category=category_map.get(
                request.form.get("category", "Commercial"), ProjectCategory.COMMERCIAL
            ),
            customer_id=customer_id,
            assigned_to=assigned_to_id,
            organization_id=org_id,
            created_by=current_user.employee.id,
        )
        try:
            db.session.add(new_project)
            db.session.flush()

            # Notification for assignment
            if assigned_to_id:
                create_notification(
                    recipient_id=assigned_to_id,
                    title="New Project Assigned",
                    message=f"You have been assigned a new project: {new_project.name}",
                    link=url_for("projects.view_project", project_id=new_project.id),
                    sender_id=current_user.employee.id,
                    organization_id=org_id,
                )

            log_activity(
                "create",
                "project",
                new_project.name,
                org_id,
                current_user.employee.id,
                new_project.id,
            )

            # Handle file upload if present
            if "file" in request.files:
                files = request.files.getlist("file")
                for file in files:
                    if file and file.filename != "":
                        from utils.documents import handle_file_upload

                        handle_file_upload(
                            file=file,
                            entity_type="project",
                            entity_id=new_project.id,
                            organization_id=org_id,
                            uploader_id=(
                                current_user.employee.id if current_user.employee else None
                            ),
                            description=f"Initial project document: {new_project.name}",
                        )

            db.session.commit()
            flash("Project created successfully!", "projectsuccess")
            return redirect(url_for("projects.projects_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "projecterror")
            return redirect(url_for("projects.add_project"))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()

    # Pre-select customer if customer_id is provided in query params
    query_customer_id = tenant_record_id(
        Customer, request.args.get("customer_id"), org_id, is_deleted=False
    )

    return render_template(
        "projects/addproject.html",
        employees=employees,
        customers=customers,
        pre_selected_customer_id=query_customer_id,
    )


@projects_bp.route("/edit-project/<int:project_id>", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(
        id=project_id, organization_id=org_id, is_deleted=False
    ).first_or_404()

    if request.method == "POST":
        # ── Snapshot old values BEFORE changes ─────────────────────────
        old_status      = project.status
        old_assignee_id = project.assigned_to
        old_assignee_emp = Employee.query.get(old_assignee_id) if old_assignee_id else None
        old_assignee_name = old_assignee_emp.name if old_assignee_emp else "Unassigned"
        old_name        = project.name

        project.name = request.form.get("name", "").strip()
        project.description = request.form.get("description", "").strip()
        assigned_to_id = request.form.get("assigned_to")
        customer_id = request.form.get("customer_id")
        project.customer_id = tenant_record_id(
            Customer, customer_id, org_id, is_deleted=False
        )

        status_map = {e.value: e for e in ProjectStatus}
        work_type_map = {e.value: e for e in ProjectWorkType}
        category_map = {e.value: e for e in ProjectCategory}

        try:
            new_assignee_id = tenant_record_id(
                Employee, assigned_to_id, org_id, is_deleted=False
            )
            project.assigned_to = new_assignee_id

            new_status = status_map.get(
                request.form.get("status"), ProjectStatus.PLANNING
            )
            project.status = new_status
            project.work_type = work_type_map.get(
                request.form.get("work_type"), ProjectWorkType.GLASS
            )
            project.category = category_map.get(
                request.form.get("category"), ProjectCategory.COMMERCIAL
            )
            project.updated_by = current_user.employee.id

            new_assignee_emp = Employee.query.get(new_assignee_id) if new_assignee_id else None
            new_assignee_name = new_assignee_emp.name if new_assignee_emp else "Unassigned"

            if new_assignee_id and new_assignee_id != old_assignee_id:
                create_notification(
                    recipient_id=new_assignee_id,
                    title="Project Assigned to You",
                    message=f"Project '{project.name}' has been assigned to you.",
                    link=url_for("projects.view_project", project_id=project.id),
                    sender_id=current_user.employee.id,
                    organization_id=org_id,
                )

            changes = build_changes([
                ("Status",      old_status.value if old_status else "",   new_status.value if new_status else ""),
                ("Assigned To", old_assignee_name, new_assignee_name),
                ("Name",        old_name, project.name),
            ])

            log_activity(
                "update",
                "project",
                project.name,
                org_id,
                current_user.employee.id,
                project.id,
                changes=changes,
            )

            # Handle file upload if present
            if "file" in request.files:
                files = request.files.getlist("file")
                for file in files:
                    if file and file.filename != "":
                        from utils.documents import handle_file_upload

                        handle_file_upload(
                            file=file,
                            entity_type="project",
                            entity_id=project.id,
                            organization_id=org_id,
                            uploader_id=(
                                current_user.employee.id if current_user.employee else None
                            ),
                            description=f"Updated project document: {project.name}",
                        )

            db.session.commit()
            flash("Project updated successfully!", "projectsuccess")
            return redirect(url_for("projects.projects_list"))
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error: {str(e)}", exc_info=True)
            flash("An error occurred. Please try again.", "projecterror")
            return redirect(url_for("projects.edit_project", project_id=project_id))

    employees = Employee.query.filter_by(organization_id=org_id, is_deleted=False).all()
    customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).all()
    return render_template(
        "projects/editproject.html",
        project=project,
        employees=employees,
        customers=customers,
    )


@projects_bp.route("/delete-project/<int:project_id>", methods=["POST"])
@login_required
def delete_project(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(
        id=project_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    project.is_deleted = True
    project.deleted_at = datetime.utcnow()
    project.deleted_by = current_user.employee.id
    try:
        log_activity(
            "delete",
            "project",
            project.name,
            org_id,
            current_user.employee.id,
            project.id,
        )
        db.session.commit()
        flash("Project deleted.", "projectsuccess")
    except Exception as e:
        db.session.rollback()
        flash("Failed to delete project.", "projecterror")
    return redirect(url_for("projects.projects_list"))


@projects_bp.route("/view-project/<int:project_id>")
@login_required
def view_project(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(
        id=project_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    return render_template("projects/project_profile.html", project=project)


@projects_bp.route("/project/<int:project_id>/update-description", methods=["POST"])
@login_required
def update_project_description(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(
        id=project_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    project.description = request.form.get("description", "").strip()
    try:
        db.session.commit()
        flash("Description updated.", "projectsuccess")
    except Exception:
        db.session.rollback()
        flash("Failed to update description.", "projecterror")
    return redirect(url_for("projects.view_project", project_id=project_id))


@projects_bp.route("/project/<int:project_id>/clear-description", methods=["POST"])
@login_required
def clear_project_description(project_id):
    org_id = current_user.organization_id
    project = Project.query.filter_by(
        id=project_id, organization_id=org_id, is_deleted=False
    ).first_or_404()
    project.description = ""
    try:
        db.session.commit()
        flash("Description cleared.", "projectsuccess")
    except Exception:
        db.session.rollback()
        flash("Failed to clear description.", "projecterror")
    return redirect(url_for("projects.view_project", project_id=project_id))


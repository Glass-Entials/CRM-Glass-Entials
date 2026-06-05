import pytest
from datetime import datetime
from model import db, Project, Task, DailyTask, Employee, Customer, ProjectStatus, TaskStatus

def test_projects_list(logged_in_client):
    resp = logged_in_client.get("/projects")
    assert resp.status_code == 200

def test_add_project(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    # Create customer first
    cust = Customer(name="Project Customer", email="proj.cust@test.com", phone_number="1234567890", organization_id=org.id, created_by=emp.id)
    db.session.add(cust)
    db.session.commit()

    resp = logged_in_client.post("/add-project", data={
        "name": "New Project",
        "description": "Test Project Desc",
        "status": ProjectStatus.PLANNING.value,
        "assigned_to": str(emp.id),
        "customer_id": str(cust.id),
        "work_type": "Glass",
        "category": "Commercial"
    }, follow_redirects=True)
    assert resp.status_code == 200
    
    proj = Project.query.filter_by(name="New Project").first()
    assert proj is not None
    assert proj.organization_id == org.id

def test_edit_project(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    proj = Project(name="Old Proj", organization_id=org.id, created_by=emp.id)
    db.session.add(proj)
    db.session.commit()

    resp = logged_in_client.post(f"/edit-project/{proj.id}", data={
        "name": "Updated Proj Name",
        "description": "Updated Proj Desc",
        "status": ProjectStatus.IN_PROGRESS.value
    }, follow_redirects=True)
    assert resp.status_code == 200
    
    db.session.refresh(proj)
    assert proj.name == "Updated Proj Name"
    assert proj.status == ProjectStatus.IN_PROGRESS

def test_delete_project(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    proj = Project(name="To Delete Proj", organization_id=org.id, created_by=emp.id)
    db.session.add(proj)
    db.session.commit()

    resp = logged_in_client.post(f"/delete-project/{proj.id}", follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(proj)
    assert proj.is_deleted is True

def test_tasks_list(logged_in_client):
    resp = logged_in_client.get("/tasks")
    assert resp.status_code == 200

def test_add_task(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    resp = logged_in_client.post("/add-task", data={
        "title": "New Task",
        "description": "Task Desc",
        "status": TaskStatus.PENDING.value,
        "due_date": "2026-12-31"
    }, follow_redirects=True)
    assert resp.status_code == 200
    
    task = Task.query.filter_by(title="New Task").first()
    assert task is not None
    assert task.organization_id == org.id

def test_edit_task(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    task = Task(title="Old Task", organization_id=org.id, created_by=user.id)
    db.session.add(task)
    db.session.commit()

    resp = logged_in_client.post(f"/edit-task/{task.id}", data={
        "title": "Updated Task Name",
        "description": "Updated Task Desc",
        "status": TaskStatus.IN_PROGRESS.value
    }, follow_redirects=True)
    assert resp.status_code == 200
    
    db.session.refresh(task)
    assert task.title == "Updated Task Name"
    assert task.status == TaskStatus.IN_PROGRESS

def test_delete_task(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    task = Task(title="To Delete Task", organization_id=org.id, created_by=user.id)
    db.session.add(task)
    db.session.commit()

    resp = logged_in_client.post(f"/delete-task/{task.id}", follow_redirects=True)
    assert resp.status_code == 200
    assert Task.query.get(task.id) is None

def test_update_task_status(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    task = Task(title="Status Task", organization_id=org.id, created_by=user.id, assigned_to=emp.id)
    db.session.add(task)
    db.session.commit()

    resp = logged_in_client.post(f"/update-task-status/{task.id}", data={
        "status": TaskStatus.COMPLETED.value
    })
    assert resp.status_code == 200
    assert resp.json["success"] is True
    
    db.session.refresh(task)
    assert task.status == TaskStatus.COMPLETED

def test_daily_tasks(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    resp_list = logged_in_client.get("/daily-tasks")
    assert resp_list.status_code == 200

    resp_add = logged_in_client.post("/add-daily-task", data={
        "date": "2026-06-05",
        "description": "Worked on project",
        "hours_spent": "4.5",
        "work_category": "General"
    }, follow_redirects=True)
    assert resp_add.status_code == 200
    
    daily = DailyTask.query.filter_by(employee_id=emp.id).first()
    assert daily is not None
    assert daily.hours_spent == 4.5

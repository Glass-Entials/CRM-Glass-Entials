"""Check if activity and notification were created."""
from app import app, db
from model import TaskActivity, Notification

with app.app_context():
    # Latest activities
    acts = TaskActivity.query.order_by(TaskActivity.created_at.desc()).limit(5).all()
    print("Latest TaskActivities:")
    for a in acts:
        print(f"  id={a.id}, task_id={a.task_id}, emp_id={a.employee_id}, msg='{a.message[:50]}'")

    # Latest notifications
    notifs = Notification.query.order_by(Notification.created_at.desc()).limit(5).all()
    print(f"\nLatest Notifications (total={Notification.query.count()}):")
    for n in notifs:
        print(f"  id={n.id}, to_emp={n.recipient_id}, title='{n.title}', is_read={n.is_read}")

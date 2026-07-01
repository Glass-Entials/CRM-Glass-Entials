"""Drop partially created task activity schema if exists."""
from app import app, db
from sqlalchemy import text

with app.app_context():
    with db.engine.begin() as conn:
        try:
            conn.execute(text("ALTER TABLE crm_document DROP FOREIGN KEY fk_crm_document_task_activity_id"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE crm_document DROP COLUMN task_activity_id"))
        except Exception:
            pass
        
        try:
            conn.execute(text("DROP TABLE task_activity"))
            print("Dropped task_activity table.")
        except Exception as e:
            print("Could not drop task_activity:", e)
print("Done.")

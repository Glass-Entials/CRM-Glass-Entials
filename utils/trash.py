from datetime import datetime
from model import db, Customer, Lead, Contact, Project, Task, Employee
from utils.activity import log_activity

TRASH_MODELS = {
    "customers": Customer,
    "leads": Lead,
    "contacts": Contact,
    "projects": Project,
    "tasks": Task,
}

def get_trashed_records(module_name, org_id, page=1, per_page=30, search=None):
    model = TRASH_MODELS.get(module_name)
    if not model:
        return None
        
    query = model.query.filter_by(organization_id=org_id, is_deleted=True)
    
    if search:
        if hasattr(model, 'name'):
            query = query.filter(model.name.ilike(f"%{search}%"))
        elif hasattr(model, 'title'):
            query = query.filter(model.title.ilike(f"%{search}%"))
        elif module_name == "contacts":
            query = query.filter(
                (model.first_name.ilike(f"%{search}%")) |
                (model.last_name.ilike(f"%{search}%"))
            )
            
    return query.order_by(model.deleted_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

def restore_record(module_name, record_id, org_id, actor_id):
    model = TRASH_MODELS.get(module_name)
    if not model:
        return False, "Invalid module"
        
    record = model.query.filter_by(id=record_id, organization_id=org_id, is_deleted=True).first()
    if not record:
        return False, "Record not found or already restored"
        
    record.is_deleted = False
    record.deleted_at = None
    record.deleted_by = None
    
    name_display = getattr(record, 'name', getattr(record, 'title', f"{module_name.capitalize()} #{record.id}"))
    if module_name == "contacts" and not hasattr(record, 'name'):
        name_display = f"{record.first_name} {record.last_name or ''}".strip()
        
    log_activity(
        action=f"{module_name[:-1]}_restored",
        entity_type=module_name[:-1],
        entity_name=name_display,
        org_id=org_id,
        actor_id=actor_id,
        entity_id=record.id,
        description=f"Restored {module_name[:-1]}: {name_display}"
    )
    
    db.session.commit()
    return True, "Record restored successfully"

def permanently_delete_record(module_name, record_id, org_id, actor_id):
    model = TRASH_MODELS.get(module_name)
    if not model:
        return False, "Invalid module"
        
    record = model.query.filter_by(id=record_id, organization_id=org_id, is_deleted=True).first()
    if not record:
        return False, "Record not found or already permanently deleted"
        
    name_display = getattr(record, 'name', getattr(record, 'title', f"{module_name.capitalize()} #{record.id}"))
    if module_name == "contacts" and not hasattr(record, 'name'):
        name_display = f"{record.first_name} {record.last_name or ''}".strip()
        
    log_activity(
        action=f"{module_name[:-1]}_permanently_deleted",
        entity_type=module_name[:-1],
        entity_name=name_display,
        org_id=org_id,
        actor_id=actor_id,
        entity_id=record.id,
        description=f"Permanently deleted {module_name[:-1]}: {name_display}"
    )
    
    db.session.delete(record)
    db.session.commit()
    return True, "Record permanently deleted"

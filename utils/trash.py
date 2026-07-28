from datetime import datetime
from model import db, Customer, Lead, Contact, Project, Task, Employee
from utils.activity import log_activity

class MockPagination:
    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, (total + per_page - 1) // per_page)
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1

TRASH_MODELS = {
    "customers": Customer,
    "leads": Lead,
    "contacts": Contact,
    "projects": Project,
    "tasks": Task,
}

def get_trashed_records(module_name, org_id, page=1, per_page=30, search=None):
    if module_name == "all":
        all_records = []
        for mod_name, model in TRASH_MODELS.items():
            query = model.query.filter_by(organization_id=org_id, is_deleted=True)
            if search:
                if hasattr(model, 'name'):
                    query = query.filter(model.name.ilike(f"%{search}%"))
                elif hasattr(model, 'title'):
                    query = query.filter(model.title.ilike(f"%{search}%"))
                elif mod_name == "contacts":
                    query = query.filter(
                        (model.first_name.ilike(f"%{search}%")) |
                        (model.last_name.ilike(f"%{search}%"))
                    )
            
            records = query.all()
            for r in records:
                r.module_name = mod_name
            all_records.extend(records)
            
        # Sort manually by deleted_at desc
        all_records.sort(key=lambda x: x.deleted_at or datetime.min, reverse=True)
        
        # Paginate
        total = len(all_records)
        start = (page - 1) * per_page
        end = start + per_page
        items = all_records[start:end]
        return MockPagination(items, page, per_page, total)
        
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
            
    # Add module_name to single module queries too for consistency in frontend
    pagination = query.order_by(model.deleted_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    for item in pagination.items:
        item.module_name = module_name
    return pagination

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

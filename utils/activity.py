"""
Utility: log_activity()
Call this after any significant CRM action to record it in the activity feed.
"""

from model import db, ActivityLog

ACTION_META = {
    # Customers
    "customer_added": ("👤", "Added new customer", "customer"),
    "customer_updated": ("✏️", "Updated customer details", "customer"),
    "customer_deleted": ("🗑", "Deleted customer", "customer"),
    "document_uploaded": ("📎", "Uploaded document", "document"),
    "document_deleted": ("🗑", "Deleted document", "document"),
    # Leads
    "lead_added": ("🎯", "Added new lead", "lead"),
    "lead_updated": ("✏️", "Updated lead details", "lead"),
    "lead_deleted": ("🗑", "Deleted lead", "lead"),
    # Projects
    "project_added": ("🏗", "Created new project", "project"),
    "project_updated": ("✏️", "Updated project", "project"),
    "project_deleted": ("🗑", "Deleted project", "project"),
}

from utils.activity_service import ActivityService

def log_activity(
    action: str,
    entity_type: str,
    entity_name: str,
    org_id: int,
    actor_id: int = None,
    entity_id: int = None,
    description: str = None,
    field_name: str = None,
    old_value=None,
    new_value=None,
    meta_data=None,
    related_entity_type=None,
    related_entity_id=None
):
    """
    Record a CRM activity event.
    """
    if description is None:
        meta = ACTION_META.get(
            action, ("⚡", action.replace("_", " ").title(), entity_type)
        )
        description = f"{meta[1]}: {entity_name}"
        
    # Map old action keys to structured actions
    structured_action = action
    if "add" in action or "create" in action:
        structured_action = "create"
    elif "update" in action or "edit" in action:
        structured_action = "update"
    elif "delete" in action or "remove" in action:
        structured_action = "delete"

    try:
        from flask_login import current_user
        if not hasattr(current_user, 'is_authenticated') or not current_user.is_authenticated:
            # Fallback if no current_user
            log = ActivityLog(
                action=structured_action,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                description=description,
                actor_id=actor_id,
                organization_id=org_id,
                field_name=field_name,
                old_value=str(old_value) if old_value is not None else None,
                new_value=str(new_value) if new_value is not None else None,
                meta_data=meta_data,
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id
            )
            db.session.add(log)
            db.session.flush()
        else:
            ActivityService.log(
                action=structured_action,
                entity_type=entity_type,
                entity_id=entity_id,
                entity_name=entity_name,
                field_name=field_name,
                old_value=old_value,
                new_value=new_value,
                meta_data=meta_data,
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id,
                description=description
            )
    except Exception as e:
        import traceback
        traceback.print_exc()
        pass  # Never let logging break the main action

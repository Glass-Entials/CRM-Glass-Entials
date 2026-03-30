"""
Utility: log_activity()
Call this after any significant CRM action to record it in the activity feed.
"""
from model import db, ActivityLog


ACTION_META = {
    # Customers
    'customer_added':   ('👤', 'Added new customer',      'customer'),
    'customer_updated': ('✏️',  'Updated customer details', 'customer'),
    'customer_deleted': ('🗑',  'Deleted customer',         'customer'),
    'document_uploaded':('📎',  'Uploaded document',        'document'),
    'document_deleted': ('🗑',  'Deleted document',         'document'),

    # Leads
    'lead_added':       ('🎯', 'Added new lead',           'lead'),
    'lead_updated':     ('✏️',  'Updated lead details',     'lead'),
    'lead_deleted':     ('🗑',  'Deleted lead',             'lead'),

    # Projects
    'project_added':    ('🏗', 'Created new project',      'project'),
    'project_updated':  ('✏️',  'Updated project',          'project'),
    'project_deleted':  ('🗑',  'Deleted project',          'project'),
}


def log_activity(action: str, entity_type: str, entity_name: str,
                 org_id: int, actor_id: int = None, entity_id: int = None,
                 description: str = None):
    """
    Record a CRM activity event.
    
    :param action: Short key like 'customer_added', 'project_updated'
    :param entity_type: 'customer', 'lead', 'project', 'document'
    :param entity_name: Display name of the record (e.g., customer name)
    :param org_id: Organization ID (multi-tenant)
    :param actor_id: Employee ID of who did it
    :param entity_id: Database ID of the record
    :param description: Optional override description
    """
    if description is None:
        meta = ACTION_META.get(action, ('⚡', action.replace('_', ' ').title(), entity_type))
        description = f"{meta[1]}: {entity_name}"

    try:
        log = ActivityLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            actor_id=actor_id,
            organization_id=org_id,
        )
        db.session.add(log)
        db.session.flush()  # Don't commit here; let the caller commit
    except Exception:
        pass  # Never let logging break the main action

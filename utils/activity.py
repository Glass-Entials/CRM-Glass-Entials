"""
utils/activity.py
Central activity logging utility.
Captures structured field-level changes and stores them in ActivityLog.meta_data.
"""

import json
from datetime import datetime
from model import db, ActivityLog, Employee

# ─── Human-readable action verbs ──────────────────────────────────────────────
ACTION_VERB = {
    "create":             "created",
    "update":             "updated",
    "delete":             "deleted",
    "restore":            "restored",
    "permanently_delete": "permanently deleted",
    "comment_added":      "added a comment to",
    "assigned":           "assigned",
    "reassigned":         "reassigned",
    "completed":          "completed",
    "converted":          "converted",
    "status_changed":     "changed the status of",
}

# ─── Entity URL resolvers ──────────────────────────────────────────────────────
def _entity_url(entity_type, entity_id):
    """Return the detail URL for an entity if possible."""
    try:
        from flask import url_for
        routes = {
            "lead":     ("leads.view_lead",        "lead_id"),
            "task":     ("tasks.view_task",         "task_id"),
            "customer": ("customers.view_customer", "customer_id"),
            "project":  ("projects.view_project",   "project_id"),
            "contact":  ("contacts.view_contact",   "contact_id"),
        }
        if entity_type in routes and entity_id:
            route, kwarg = routes[entity_type]
            return url_for(route, **{kwarg: entity_id})
    except Exception:
        pass
    return None


# ─── Core structured logger ───────────────────────────────────────────────────
def log_activity(
    action: str,
    entity_type: str,
    entity_name: str,
    org_id: int,
    actor_id: int = None,
    entity_id: int = None,
    description: str = None,
    changes: list = None,        # [{field, label, old, new}, ...]
    comment_text: str = None,    # for comment_added actions
    field_name: str = None,      # legacy single-field tracking
    old_value=None,
    new_value=None,
    meta_data: dict = None,
    related_entity_type: str = None,
    related_entity_id: int = None,
):
    """
    Record a CRM activity event with full structured change data.
    Changes are stored in meta_data.changes as a list of {field, label, old, new}.
    """
    # ── Map legacy action strings to clean action keys ──────────────────────
    structured_action = _map_action(action)

    # ── Prevent No-Op Updates ─────────────────────────────────────────────────
    if structured_action == "update":
        has_changes = bool(changes)  # non-empty list
        has_comment = bool(comment_text)
        has_legacy = bool(field_name)
        if not has_changes and not has_comment and not has_legacy:
            return  # nothing changed — skip logging entirely

    # ── Auto-detect smarter action verb based on what actually changed ────────
    if structured_action == "update" and changes:
        fields = {c["field"] for c in changes}
        # If ONLY the assignee changed → call it "reassigned"
        if fields == {"assigned_to"}:
            structured_action = "reassigned"

    # ── Build meta_data payload ───────────────────────────────────────────────
    if meta_data is None:
        meta_data = {}

    if changes:
        meta_data["changes"] = changes

    if comment_text:
        meta_data["comment"] = comment_text

    entity_url = _entity_url(entity_type, entity_id)
    if entity_url:
        meta_data["entity_url"] = entity_url

    # ── Auto-description for logging paths that don't pass description ────────
    if description is None:
        verb = ACTION_VERB.get(structured_action, structured_action.replace("_", " "))
        if structured_action == "comment_added":
            description = f'Added a comment to {entity_type} "{entity_name}"'
        else:
            description = f'{verb.capitalize()} {entity_type} "{entity_name}"'

    try:
        from flask_login import current_user
        if hasattr(current_user, "is_authenticated") and current_user.is_authenticated:
            resolved_actor_id = (
                current_user.employee.id
                if hasattr(current_user, "employee") and current_user.employee
                else actor_id
            )
            resolved_org = current_user.organization_id
        else:
            resolved_actor_id = actor_id
            resolved_org = org_id

        # Single-field legacy compat
        fv_old = str(old_value) if old_value is not None else None
        fv_new = str(new_value) if new_value is not None else None

        log = ActivityLog(
            action=structured_action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            description=description,
            actor_id=resolved_actor_id,
            organization_id=resolved_org,
            field_name=field_name,
            old_value=fv_old,
            new_value=fv_new,
            meta_data=meta_data if meta_data else None,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
        )
        db.session.add(log)
        db.session.flush()

        try:
            from utils.extensions import socketio
            from sqlalchemy import event

            # Emit real-time event only after DB commit succeeds
            room = f"org_{resolved_org}"
            
            payload = {
                'id': log.id,
                'action': log.action,
                'entity_type': log.entity_type,
                'entity_name': log.entity_name,
                'actor_id': log.actor_id,
                'created_at': datetime.utcnow().isoformat() + "Z"
            }
            
            @event.listens_for(db.session, "after_commit", once=True)
            def _emit_activity_after_commit(session):
                socketio.emit('new_activity', payload, room=room)
                
        except ImportError:
            pass # SocketIO not configured or available

    except Exception:
        import traceback
        traceback.print_exc()
        # Never crash the main request due to logging failure


def _map_action(action: str) -> str:
    """Map legacy raw action strings to clean structured action keys."""
    if action in ACTION_VERB:
        return action

    a = action.lower()
    if any(k in a for k in ("add", "creat", "new")):
        return "create"
    if "restor" in a:
        return "restore"
    if "permanent" in a:
        return "permanently_delete"
    if any(k in a for k in ("delet", "remov")):
        return "delete"
    if "comment" in a:
        return "comment_added"
    if "complet" in a:
        return "completed"
    if "convert" in a:
        return "converted"
    if "reassign" in a:
        return "reassigned"
    if "assign" in a:
        return "assigned"
    if any(k in a for k in ("updat", "edit", "chang")):
        return "update"
    return action


# ─── Diff helper ──────────────────────────────────────────────────────────────
def build_changes(field_defs: list) -> list:
    """
    Build a changes list from (label, old_val, new_val) tuples, skipping unchanged fields.

    Usage:
        changes = build_changes([
            ("Status", old_status, new_status),
            ("Assigned To", old_assignee_name, new_assignee_name),
        ])
    """
    changes = []
    for item in field_defs:
        if len(item) == 3:
            label, old, new = item
            field = label.lower().replace(" ", "_")
        else:
            field, label, old, new = item

        old_str = str(old) if old is not None else ""
        new_str = str(new) if new is not None else ""

        if old_str != new_str:
            changes.append({
                "field":  field,
                "label":  label,
                "old":    old_str,
                "new":    new_str,
            })
    return changes

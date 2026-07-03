"""
utils/lead_log.py
Helper to write automatic system log entries for lead events.
"""
from datetime import datetime


def log_lead_event(lead_id, event_type, message, icon="🔔", actor_id=None, organization_id=None):
    """
    Create a LeadSystemLog entry.
    Import lazily to avoid circular imports.
    Must be called within an active Flask app context.
    Does NOT commit — the caller's db.session.commit() will persist it.
    """
    try:
        from model import db, LeadSystemLog
        entry = LeadSystemLog(
            lead_id=lead_id,
            event_type=event_type,
            message=message,
            icon=icon,
            created_at=datetime.utcnow(),
            actor_id=actor_id,
            organization_id=organization_id,
        )
        db.session.add(entry)
    except Exception:
        pass  # Never crash the main operation because of logging

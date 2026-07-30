from flask_login import current_user
from model import db, ActivityLog

class ActivityService:
    @staticmethod
    def log(action, entity_type, entity_id, entity_name=None, field_name=None, old_value=None, new_value=None, meta_data=None, related_entity_type=None, related_entity_id=None, description=None):
        if not current_user.is_authenticated:
            return None

        # Check if we should log based on actual changes for updates
        if action == "update" and old_value == new_value and old_value is not None:
            return None

        activity = ActivityLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            meta_data=meta_data,
            related_entity_type=related_entity_type,
            related_entity_id=related_entity_id,
            description=description,
            actor_id=current_user.employee.id if hasattr(current_user, "employee") and current_user.employee else None,
            organization_id=current_user.organization_id
        )

        db.session.add(activity)
        
        # We do not commit here so it rolls back if the main action fails.
        db.session.flush()

        try:
            from app import socketio
            socketio.emit('new_activity', {
                'id': activity.id,
                'action': activity.action,
                'entity_type': activity.entity_type,
                'entity_id': activity.entity_id,
                'actor': current_user.username,
                'created_at': activity.created_at.isoformat() if activity.created_at else None
            }, room=f'org_{current_user.organization_id}')
        except ImportError:
            pass
        except Exception as e:
            pass

        return activity

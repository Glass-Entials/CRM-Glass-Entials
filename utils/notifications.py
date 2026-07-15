from model import db, Notification
from datetime import datetime
from utils.extensions import socketio
from sqlalchemy import event


class NotificationService:
    @staticmethod
    def send(recipient_id, title, message, link=None, sender_id=None, organization_id=None):
        if not organization_id:
            return None

        notification = Notification(
            recipient_id=recipient_id,
            sender_id=sender_id,
            title=title,
            message=message,
            link=link,
            organization_id=organization_id,
            created_at=datetime.utcnow(),
        )
        db.session.add(notification)

        # Emit the real-time event ONLY after the DB commit succeeds.
        # Emitting before commit was the bug: if the session flushed but commit
        # hadn't happened yet, a page refresh / socket reconnect could cause
        # duplicate delivery or the uncommitted record to persist unexpectedly.
        room = f"org_{organization_id}_user_{recipient_id}"
        payload = {
            'title': title,
            'message': message,
            'link': link,
            'created_at': notification.created_at.isoformat() + "Z"
        }

        @event.listens_for(db.session, "after_commit", once=True)
        def _emit_after_commit(session):
            payload['id'] = notification.id
            socketio.emit('new_notification', payload, room=room)

        return notification


def create_notification(
    recipient_id, title, message, link=None, sender_id=None, organization_id=None
):
    """
    Creates a new notification for a user.
    The real-time Socket.IO event is emitted AFTER the enclosing
    db.session.commit() succeeds — never on flush or page load.
    """
    return NotificationService.send(
        recipient_id=recipient_id,
        title=title,
        message=message,
        link=link,
        sender_id=sender_id,
        organization_id=organization_id
    )

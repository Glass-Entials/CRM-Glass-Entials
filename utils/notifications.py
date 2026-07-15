from model import db, Notification
from datetime import datetime
from utils.extensions import socketio

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
        
        # We need to flush so the notification gets an ID before emitting
        db.session.flush()

        # Emit to the specific user's room
        room = f"org_{organization_id}_user_{recipient_id}"
        socketio.emit('new_notification', {
            'id': notification.id,
            'title': notification.title,
            'message': notification.message,
            'link': notification.link,
            'created_at': notification.created_at.isoformat() + "Z"
        }, room=room)

        return notification

def create_notification(
    recipient_id, title, message, link=None, sender_id=None, organization_id=None
):
    """
    Legacy wrapper. Creates a new notification for a user and emits it via SocketIO.
    """
    return NotificationService.send(
        recipient_id=recipient_id,
        title=title,
        message=message,
        link=link,
        sender_id=sender_id,
        organization_id=organization_id
    )

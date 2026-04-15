from model import db, Notification
from datetime import datetime

def create_notification(recipient_id, title, message, link=None, sender_id=None, organization_id=None):
    """
    Creates a new notification for a user.
    """
    if not organization_id:
        return None
        
    notification = Notification(
        recipient_id=recipient_id,
        sender_id=sender_id,
        title=title,
        message=message,
        link=link,
        organization_id=organization_id,
        created_at=datetime.utcnow()
    )
    db.session.add(notification)
    return notification

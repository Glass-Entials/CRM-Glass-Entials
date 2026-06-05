import logging
from flask import current_app

logger = logging.getLogger(__name__)

def handle_db_error(e, user_message: str = "An error occurred. Please try again.", context: str = ""):
    """
    Log the full error internally and return a safe message for the user.
    Prevents Information Exposure by sanitizing SQLAlchemy traceback from flash messages.
    """
    if context:
        current_app.logger.error(f"DB Error [{context}]: {str(e)}", exc_info=True)
    else:
        current_app.logger.error(f"DB Error: {str(e)}", exc_info=True)
    return user_message

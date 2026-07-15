import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("REDIS_URL", "memory://"),
    default_limits=["1000 per day", "300 per hour"]
)

# Initialize SocketIO, allowing cross-origin for potential external API usage
# In production, we connect to Redis so multiple workers can emit messages across processes.
socketio = SocketIO(cors_allowed_origins="*", message_queue=os.environ.get("REDIS_URL", "memory://"))

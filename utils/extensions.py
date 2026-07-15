import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_socketio import SocketIO

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=os.environ.get("REDIS_URL", "memory://"),
    default_limits=["1000 per day", "300 per hour"]
)

# SocketIO instance — do NOT pass message_queue or async_mode here.
# These are configured in init_app() to avoid eventlet monkey-patch conflicts.
# If REDIS_URL is set in production, pass it via SOCKETIO_MESSAGE_QUEUE env var.
socketio = SocketIO()

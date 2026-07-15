# Gunicorn configuration for GlassEntials CRM — Production
# Used by: gunicorn -c gunicorn.conf.py wsgi:application

import multiprocessing
import os

# ─── Binding ───────────────────────────────────────────────────────────────────
bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# ─── Workers ───────────────────────────────────────────────────────────────────
# CRITICAL for Socket.IO + Eventlet:
# With no Redis message queue, Socket.IO sessions are stored in-process.
# Multiple workers each have their own isolated session store, causing
# HTTP 400 "Unknown session" when a different worker handles a polling request.
# Eventlet handles massive concurrency within a SINGLE process via green threads.
# Only increase workers if SOCKETIO_MESSAGE_QUEUE (Redis) is configured.
import os as _os, multiprocessing as _mp
_has_redis_queue = bool(_os.environ.get("SOCKETIO_MESSAGE_QUEUE"))
workers = int(_os.environ.get("GUNICORN_WORKERS", _mp.cpu_count() * 2 + 1 if _has_redis_queue else 1))
worker_class = "eventlet"
threads = 1  # Eventlet is single-threaded async per worker — threading is irrelevant

# ─── Timeouts ──────────────────────────────────────────────────────────────────
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 120))
keepalive = 5
graceful_timeout = 30

# ─── Logging ───────────────────────────────────────────────────────────────────
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
accesslog = os.environ.get("GUNICORN_ACCESS_LOG", "-")     # stdout → CloudWatch
errorlog  = os.environ.get("GUNICORN_ERROR_LOG",  "-")     # stderr → CloudWatch
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# ─── Security ──────────────────────────────────────────────────────────────────
limit_request_line   = 4096
limit_request_fields = 100
limit_request_field_size = 8192

# ─── Process Naming ────────────────────────────────────────────────────────────
proc_name = "glassentials_crm"

# ─── Preload App ───────────────────────────────────────────────────────────────
# Eventlet monkey-patching clashes with preload_app. Set to False.
preload_app = False

# ─── Worker Lifecycle Hooks ────────────────────────────────────────────────────
def on_starting(server):
    server.log.info("GlassEntials CRM — Gunicorn starting")

def worker_exit(server, worker):
    server.log.info(f"Worker {worker.pid} exited")

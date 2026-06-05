import os
from functools import wraps
from urllib.parse import urlparse, urljoin

from flask import abort, current_app, request
from flask_login import current_user
from werkzeug.utils import secure_filename


MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def role_value(user):
    role = getattr(user, "role", None)
    return getattr(role, "value", role)


def require_roles(*roles):
    allowed = {getattr(role, "value", role) for role in roles}

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if role_value(current_user) not in allowed:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def is_safe_redirect(target):
    if not target:
        return False
    host_url = request.host_url
    ref_url = urlparse(host_url)
    test_url = urlparse(urljoin(host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


def safe_redirect_target(target, fallback):
    return target if is_safe_redirect(target) else fallback


def parse_optional_id(value):
    if value in (None, "", "none", "unassigned"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        abort(400)


def tenant_record_id(model, raw_id, org_id, *, allow_none=True, **filters):
    record_id = parse_optional_id(raw_id)
    if record_id is None:
        if allow_none:
            return None
        abort(400)
    query = model.query.filter_by(id=record_id, organization_id=org_id, **filters)
    record = query.first()
    if not record:
        abort(404)
    return record.id


def validate_upload(file, allowed_extensions, *, max_bytes=MAX_UPLOAD_BYTES):
    if not file or not file.filename:
        return None

    filename = secure_filename(file.filename)
    if "." not in filename:
        abort(400)

    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in allowed_extensions:
        abort(400)

    pos = file.stream.tell()
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(pos)
    if size > max_bytes:
        abort(413)

    return filename, extension, size


def upload_root(*parts):
    return os.path.join(current_app.config["UPLOAD_FOLDER"], *parts)

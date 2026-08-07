"""
super_admin/decorators.py
Route protection decorator for the Super Admin portal.
Any route decorated with @super_admin_required will redirect
unauthenticated visitors to /admin/login.
"""

from functools import wraps
from flask import session, redirect, url_for


def super_admin_required(f):
    """
    Decorator that protects Super Admin routes.
    Checks for 'super_admin_authenticated' flag in Flask session.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("super_admin_authenticated"):
            return redirect(url_for("super_admin.login"))
        return f(*args, **kwargs)
    return decorated_function

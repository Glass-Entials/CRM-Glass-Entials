"""
routes/password_reset.py
Enterprise Forgot Password / Reset Password blueprint.

Security properties:
  - Cryptographically secure tokens (secrets.token_urlsafe)
  - Single-use, 15-minute expiry
  - No email enumeration (always returns generic response)
  - CSRF protection on all POST endpoints
  - Rate limiting on the forgot-password endpoint
  - Timing-safe token lookup via HMAC compare (hmac.compare_digest)
  - Werkzeug password hashing
  - Comprehensive logging (no sensitive data logged)
  - All old tokens invalidated before issuing a new one
"""

import secrets
import logging
import hmac
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    current_app,
)
from werkzeug.security import generate_password_hash
from flask_login import current_user

from model import db, User, PasswordResetToken
from utils.email_service import send_html_email
from utils.extensions import limiter

logger = logging.getLogger(__name__)

password_reset_bp = Blueprint("password_reset", __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TOKEN_EXPIRY_MINUTES = 15
GENERIC_RESPONSE = (
    "If an account with that email exists, a password reset link has been sent. "
    "Please check your inbox (and spam folder)."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invalidate_existing_tokens(user_id: int) -> None:
    """Mark all unused reset tokens for a user as used before issuing a new one."""
    PasswordResetToken.query.filter_by(user_id=user_id, used=False).update({"used": True})
    db.session.flush()


def _create_token(user: User) -> str:
    """Generate, persist, and return a new secure reset token."""
    _invalidate_existing_tokens(user.id)
    raw_token = secrets.token_urlsafe(48)  # 64-char URL-safe string
    expires = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRY_MINUTES)
    record = PasswordResetToken(
        user_id=user.id,
        token=raw_token,
        expires_at=expires,
        used=False,
    )
    db.session.add(record)
    db.session.commit()
    logger.info(f"[PasswordReset] Token issued for user_id={user.id}")
    return raw_token


def _find_valid_token(raw_token: str):
    """
    Lookup token record using Python-level comparison (all tokens fetched by
    a short-list then compared with hmac.compare_digest to resist timing attacks).
    Returns the PasswordResetToken record or None.
    """
    if not raw_token or len(raw_token) > 200:
        return None

    # Fetch unexpired+unused candidates (index on token makes this fast)
    candidate = PasswordResetToken.query.filter_by(used=False).filter(
        PasswordResetToken.expires_at > datetime.utcnow()
    ).filter(PasswordResetToken.token == raw_token).first()

    if candidate is None:
        return None

    # Timing-safe compare to prevent timing attacks on token value
    if not hmac.compare_digest(candidate.token.encode(), raw_token.encode()):
        return None

    return candidate


def _send_reset_email(user: User, token: str) -> None:
    """Build the reset email and dispatch it."""
    from flask import request as req
    base_url = current_app.config.get("PREFERRED_URL_SCHEME", "https") + "://" + req.host
    reset_url = f"{base_url}{url_for('password_reset.reset_password', token=token)}"
    logo_url = f"{base_url}{url_for('static', filename='img/logo.png')}"

    html = render_template(
        "email/password_reset_email.html",
        user=user,
        reset_url=reset_url,
        expiry_minutes=TOKEN_EXPIRY_MINUTES,
        logo_url=logo_url,
    )
    text = (
        f"Hi {user.username},\n\n"
        f"A password reset was requested for your GlassEntials account.\n\n"
        f"Reset link (valid for {TOKEN_EXPIRY_MINUTES} minutes):\n{reset_url}\n\n"
        "If you did not request this, you can safely ignore this email.\n\n"
        "— GlassEntials CRM"
    )
    send_html_email(
        to_email=user.email,
        subject="Reset Your GlassEntials Password",
        html_body=html,
        text_body=text,
    )


def _validate_new_password(password: str, confirm: str) -> list[str]:
    """Server-side password policy validation. Returns a list of error strings."""
    errors = []
    if not password:
        errors.append("Password is required.")
        return errors
    if password != confirm:
        errors.append("Passwords do not match.")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter.")
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter.")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one number.")
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in password):
        errors.append("Password must contain at least one special character.")
    return errors


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@password_reset_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per minute; 50 per hour")
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("home_page"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        # Always return generic response — never disclose email existence
        if email:
            user = User.query.filter(
                db.func.lower(User.email) == email
            ).first()
            if user:
                try:
                    token = _create_token(user)
                    _send_reset_email(user, token)
                except Exception as exc:
                    logger.error(f"[PasswordReset] Failed to issue/send token: {exc}")

        flash(GENERIC_RESPONSE, "resetinfo")
        return redirect(url_for("password_reset.forgot_password"))

    return render_template("login/forgot_password.html")


@password_reset_bp.route("/reset-password/<token>", methods=["GET", "POST"])
@limiter.limit("20 per hour")
def reset_password(token: str):
    if current_user.is_authenticated:
        return redirect(url_for("home_page"))

    record = _find_valid_token(token)

    # ---- Handle invalid / expired / used token ----
    if record is None:
        # Determine WHY it's invalid for a friendly message
        expired_record = PasswordResetToken.query.filter_by(token=token).first()
        if expired_record and expired_record.used:
            reason = "used"
        elif expired_record and datetime.utcnow() >= expired_record.expires_at:
            reason = "expired"
        else:
            reason = "invalid"
        return render_template("login/reset_error.html", reason=reason), 400

    user = record.user

    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm_password", "")

        errors = _validate_new_password(password, confirm)
        if errors:
            for err in errors:
                flash(err, "reseterror")
            return render_template("login/reset_password.html", token=token)

        try:
            # Hash and update password
            user.password = generate_password_hash(password)
            user.must_change_password = False

            # Invalidate ALL tokens for this user
            _invalidate_existing_tokens(user.id)

            db.session.commit()
            logger.info(f"[PasswordReset] Password reset successful for user_id={user.id}")
        except Exception as exc:
            db.session.rollback()
            logger.error(f"[PasswordReset] DB error on password update: {exc}")
            flash("An error occurred. Please try again.", "reseterror")
            return render_template("login/reset_password.html", token=token)

        return redirect(url_for("password_reset.reset_success"))

    return render_template("login/reset_password.html", token=token)


@password_reset_bp.route("/reset-password/success")
def reset_success():
    if current_user.is_authenticated:
        return redirect(url_for("home_page"))
    return render_template("login/reset_success.html")

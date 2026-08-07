"""
super_admin/routes.py
All HTTP routes for the Super Admin portal (Phase 1).

Blueprint URL prefix: /admin
Subdomain routing is handled at Nginx level.
"""

import logging
from datetime import datetime

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
)
from utils.extensions import limiter
from utils.email_service import send_html_email

from .auth import (
    verify_credentials,
    generate_otp,
    build_otp_session_data,
    validate_otp,
    SUPER_ADMIN_EMAIL,
    OTP_EXPIRY_MINUTES,
)
from .decorators import super_admin_required

logger = logging.getLogger(__name__)

super_admin_bp = Blueprint(
    "super_admin",
    __name__,
    url_prefix="/admin",
    template_folder="templates",
    static_folder="static",
    static_url_path="/super-admin-static",
)

# ---------------------------------------------------------------------------
# Session keys
# ---------------------------------------------------------------------------
_SA_AUTH_KEY = "super_admin_authenticated"
_SA_OTP_KEY = "super_admin_otp_data"
_SA_PENDING_KEY = "super_admin_pending_auth"  # set after creds verified, before OTP


# ---------------------------------------------------------------------------
# Helper: send OTP email
# ---------------------------------------------------------------------------
def _send_otp_email(otp: str) -> bool:
    """Send the 6-digit OTP to the configured super admin email address."""
    subject = "🔐 Super Admin OTP – GlassEntials"
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; margin: 0; padding: 40px 20px;">
      <div style="max-width: 480px; margin: 0 auto; background: #1e293b; border-radius: 16px; overflow: hidden; box-shadow: 0 25px 50px rgba(0,0,0,0.5);">
        <div style="background: linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%); padding: 32px; text-align: center;">
          <h1 style="color: #fff; margin: 0; font-size: 22px; letter-spacing: -0.5px;">GlassEntials Super Admin</h1>
          <p style="color: rgba(255,255,255,0.75); margin: 8px 0 0; font-size: 14px;">Secure Login Verification</p>
        </div>
        <div style="padding: 40px 32px; text-align: center;">
          <p style="color: #94a3b8; font-size: 15px; margin-top: 0;">Your one-time login code is:</p>
          <div style="display: inline-block; background: #0f172a; border: 2px solid #6366f1; border-radius: 12px; padding: 20px 40px; margin: 16px 0;">
            <span style="font-size: 42px; font-weight: 700; letter-spacing: 12px; color: #a5b4fc; font-family: 'Courier New', monospace;">{otp}</span>
          </div>
          <p style="color: #64748b; font-size: 13px; margin-top: 16px;">
            ⏱ Expires in <strong style="color: #94a3b8;">{OTP_EXPIRY_MINUTES} minutes</strong><br>
            🔒 This code is single-use and will be invalidated after verification.
          </p>
          <p style="color: #475569; font-size: 12px; margin-top: 24px; border-top: 1px solid #334155; padding-top: 16px;">
            If you did not request this code, your credentials may be compromised.<br>
            Change your Super Admin password immediately.
          </p>
        </div>
      </div>
    </body>
    </html>
    """
    text_body = f"Your GlassEntials Super Admin OTP is: {otp}\nExpires in {OTP_EXPIRY_MINUTES} minutes."
    return send_html_email(SUPER_ADMIN_EMAIL, subject, html_body, text_body)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@super_admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute; 100 per hour")
def login():
    """Step 1 – Username + Password."""
    # Already authenticated → dashboard
    if session.get(_SA_AUTH_KEY):
        return redirect(url_for("super_admin.dashboard"))

    error = None

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:
            error = "Username and password are required."
        elif verify_credentials(username, password):
            # Generate OTP and store in session
            otp = generate_otp()
            session[_SA_OTP_KEY] = build_otp_session_data(otp)
            session[_SA_PENDING_KEY] = True
            session.modified = True

            sent = _send_otp_email(otp)
            if not sent:
                logger.error("[SuperAdmin] Failed to send OTP email to %s", SUPER_ADMIN_EMAIL)
                # Still allow OTP page; admin will see a warning.
                flash("Warning: OTP email could not be sent. Check server mail configuration.", "sa_warning")

            logger.info("[SuperAdmin] Login credentials accepted — OTP sent to %s", SUPER_ADMIN_EMAIL)
            return redirect(url_for("super_admin.verify_otp"))
        else:
            logger.warning("[SuperAdmin] Failed login attempt for username='%s'", username)
            error = "Invalid username or password."

    return render_template("super_admin/login.html", error=error)


@super_admin_bp.route("/otp", methods=["GET", "POST"])
@limiter.limit("30 per minute")
def verify_otp():
    """Step 2 – OTP Verification."""
    # Must have gone through credentials step
    if not session.get(_SA_PENDING_KEY):
        return redirect(url_for("super_admin.login"))

    error = None

    if request.method == "POST":
        action = request.form.get("action", "verify")

        if action == "resend":
            otp = generate_otp()
            session[_SA_OTP_KEY] = build_otp_session_data(otp)
            session.modified = True
            sent = _send_otp_email(otp)
            if sent:
                flash("A new OTP has been sent to your email.", "sa_info")
            else:
                flash("Failed to send OTP email. Please check mail configuration.", "sa_warning")
            return redirect(url_for("super_admin.verify_otp"))

        # Verify action
        submitted_otp = request.form.get("otp", "").strip()
        otp_data = session.get(_SA_OTP_KEY)

        valid, reason = validate_otp(otp_data, submitted_otp)
        if valid:
            # Mark OTP as used, grant session
            if otp_data:
                otp_data["otp_used"] = True
                session[_SA_OTP_KEY] = otp_data

            session[_SA_AUTH_KEY] = True
            session[_SA_PENDING_KEY] = False
            session.permanent = True
            session.modified = True

            logger.info("[SuperAdmin] Successful login at %s", datetime.utcnow().isoformat())
            return redirect(url_for("super_admin.dashboard"))
        else:
            logger.warning("[SuperAdmin] OTP validation failed: %s", reason)
            error = reason

    return render_template("super_admin/otp.html", error=error, otp_expiry=OTP_EXPIRY_MINUTES)


@super_admin_bp.route("/dashboard")
@super_admin_required
def dashboard():
    """Phase 1 Super Admin Dashboard."""
    return render_template("super_admin/dashboard.html")


@super_admin_bp.route("/logout")
def logout():
    """Destroy the super admin session and redirect to login."""
    session.pop(_SA_AUTH_KEY, None)
    session.pop(_SA_OTP_KEY, None)
    session.pop(_SA_PENDING_KEY, None)
    session.modified = True
    logger.info("[SuperAdmin] Logout at %s", datetime.utcnow().isoformat())
    return redirect(url_for("super_admin.login"))


# ---------------------------------------------------------------------------
# Coming Soon stubs (for sidebar navigation)
# ---------------------------------------------------------------------------

@super_admin_bp.route("/organizations")
@super_admin_required
def organizations():
    return render_template("super_admin/coming_soon.html", page_title="Organizations")


@super_admin_bp.route("/users")
@super_admin_required
def users():
    return render_template("super_admin/coming_soon.html", page_title="Users")


@super_admin_bp.route("/plans")
@super_admin_required
def plans():
    return render_template("super_admin/coming_soon.html", page_title="Plans")


@super_admin_bp.route("/modules")
@super_admin_required
def modules():
    return render_template("super_admin/coming_soon.html", page_title="Modules")


@super_admin_bp.route("/analytics")
@super_admin_required
def analytics():
    return render_template("super_admin/coming_soon.html", page_title="Analytics")


@super_admin_bp.route("/settings")
@super_admin_required
def settings():
    return render_template("super_admin/coming_soon.html", page_title="Settings")

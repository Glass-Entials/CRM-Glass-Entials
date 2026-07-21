"""
routes/google_auth.py
Google OAuth 2.0 authentication blueprint for GlassEntials CRM.

Handles all three scenarios:
  1. Existing user (email found)   → login immediately
  2. New email, create org         → create user + org + employee
  3. New email, join org           → create pending user + join request

Security:
  - State token (CSRF protection) stored in server-side session
  - Verified email only accepted from Google's userinfo endpoint
  - No duplicate users created (de-duplicated by email + provider ID)
  - Cross-organization access prevented
"""

import secrets
import string
import logging
from datetime import datetime

from flask import (
    Blueprint,
    redirect,
    request,
    session,
    url_for,
    render_template,
    flash,
    current_app,
)
from flask_login import login_user, current_user
from werkzeug.security import generate_password_hash

from model import db, User, Employee, Organization, UserRole
from services.oauth_service import (
    google_build_auth_url,
    google_exchange_code,
    google_get_userinfo,
    generate_oauth_state,
    google_is_configured,
)

logger = logging.getLogger(__name__)

google_auth_bp = Blueprint("google_auth", __name__, url_prefix="/auth/google")

_OAUTH_STATE_KEY   = "_google_oauth_state"
_OAUTH_PROFILE_KEY = "_google_oauth_profile"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_redirect_uri() -> str:
    return url_for("google_auth.callback", _external=True)


def _generate_org_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if not Organization.query.filter_by(unique_code=code).first():
            return code


def _make_unique_username(base: str) -> str:
    """Ensure username is unique by appending a suffix if needed."""
    username = base[:50]
    if not User.query.filter_by(username=username).first():
        return username
    for _ in range(20):
        candidate = f"{base[:44]}_{secrets.token_hex(3)}"
        if not User.query.filter_by(username=candidate).first():
            return candidate
    return f"user_{secrets.token_hex(6)}"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@google_auth_bp.route("/login")
def login():
    """Initiate Google OAuth flow."""
    if current_user.is_authenticated:
        return redirect(url_for("home_page"))

    if not google_is_configured():
        flash("Google Sign-In is not configured on this server.", "loginerror")
        return redirect(url_for("auth.login"))

    state = generate_oauth_state()
    session[_OAUTH_STATE_KEY] = state

    auth_url = google_build_auth_url(
        redirect_uri=_get_redirect_uri(),
        state=state,
    )
    return redirect(auth_url)


@google_auth_bp.route("/callback")
def callback():
    """Handle the redirect back from Google after user consents."""
    # ---- State verification (CSRF protection) ----
    returned_state = request.args.get("state", "")
    stored_state   = session.pop(_OAUTH_STATE_KEY, None)
    if not stored_state or not secrets.compare_digest(stored_state, returned_state):
        flash("Invalid authentication state. Please try again.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- Handle user denial ----
    error = request.args.get("error")
    if error:
        flash("Google Sign-In was cancelled.", "loginerror")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        flash("Google did not return an authorization code.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- Exchange code for tokens ----
    try:
        token_data = google_exchange_code(code, _get_redirect_uri())
    except Exception as exc:
        logger.error(f"[GoogleOAuth] Token exchange error: {exc}")
        flash("Failed to complete Google Sign-In. Please try again.", "loginerror")
        return redirect(url_for("auth.login"))

    access_token = token_data.get("access_token")
    if not access_token:
        flash("Google Sign-In failed: no access token returned.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- Fetch user info from Google ----
    try:
        profile = google_get_userinfo(access_token)
    except Exception as exc:
        logger.error(f"[GoogleOAuth] Userinfo error: {exc}")
        flash("Failed to fetch your Google profile. Please try again.", "loginerror")
        return redirect(url_for("auth.login"))

    # Google guarantees email_verified for OAuth2 userinfo endpoint
    if not profile.get("email_verified"):
        flash("Your Google email is not verified. Please verify your Google account first.", "loginerror")
        return redirect(url_for("auth.login"))

    google_id    = profile.get("sub")       # Google's stable user ID
    email        = profile.get("email", "").lower().strip()
    display_name = profile.get("name", email.split("@")[0])

    if not google_id or not email:
        flash("Google returned incomplete profile information.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- CASE 1: Existing user with same provider ID ----
    existing_by_provider = User.query.filter_by(
        oauth_provider="google",
        oauth_provider_id=google_id,
    ).first()
    if existing_by_provider:
        if not getattr(existing_by_provider, 'is_active', True):
            flash("Your account is pending administrator approval.", "loginerror")
            return redirect(url_for("auth.login"))
            
        login_user(existing_by_provider)
        flash(f"Welcome back, {existing_by_provider.username}!", "loginsuccess")
        logger.info(f"[GoogleOAuth] Login via provider ID: user_id={existing_by_provider.id}")
        return redirect(url_for("home_page"))

    # ---- CASE 2: Existing user matched by email → link account ----
    existing_by_email = User.query.filter(
        db.func.lower(User.email) == email
    ).first()
    if existing_by_email:
        if not getattr(existing_by_email, 'is_active', True):
            flash("Your account is pending administrator approval.", "loginerror")
            return redirect(url_for("auth.login"))

        # Link Google to existing account
        existing_by_email.oauth_provider    = "google"
        existing_by_email.oauth_provider_id = google_id
        db.session.commit()
        login_user(existing_by_email)
        flash(f"Google account linked and you are now logged in, {existing_by_email.username}!", "loginsuccess")
        logger.info(f"[GoogleOAuth] Linked Google to existing user_id={existing_by_email.id}")
        return redirect(url_for("home_page"))

    # ---- CASE 3: New email — start onboarding ----
    # Store Google profile in session temporarily for the onboarding step
    session[_OAUTH_PROFILE_KEY] = {
        "google_id":    google_id,
        "email":        email,
        "display_name": display_name,
    }
    return redirect(url_for("google_auth.onboard"))


@google_auth_bp.route("/onboard", methods=["GET", "POST"])
def onboard():
    """
    New user onboarding page after Google sign-in.
    Shows Create Organization / Join Organization options.
    """
    profile = session.get(_OAUTH_PROFILE_KEY)
    if not profile:
        flash("Session expired. Please sign in with Google again.", "loginerror")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        org_option = request.form.get("org_option")

        if org_option == "create":
            org_name = request.form.get("org_name", "").strip()
            if not org_name:
                flash("Please enter an organization name.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

            try:
                # Create organization
                org = Organization(
                    name=org_name,
                    unique_code=_generate_org_code(),
                )
                db.session.add(org)
                db.session.flush()

                # Race condition check: Ensure email isn't taken while onboarding
                if User.query.filter_by(email=profile["email"]).first():
                    flash("An account with this email was just registered. Please log in normally.", "oautherror")
                    return render_template("login/google_onboard.html", profile=profile)

                # Create user (no password for OAuth-only accounts)
                username = _make_unique_username(profile["display_name"])
                new_user = User(
                    username=username,
                    email=profile["email"],
                    password=generate_password_hash(secrets.token_hex(32)),  # unusable placeholder
                    role=UserRole.ADMIN,
                    organization_id=org.id,
                    oauth_provider="google",
                    oauth_provider_id=profile["google_id"],
                    is_active=True,  # Creator is always active
                )
                db.session.add(new_user)
                db.session.flush()

                # Set org creator
                org.created_by = new_user.id

                # Create employee profile
                employee = Employee(
                    name=profile["display_name"],
                    email=profile["email"],
                    user_id=new_user.id,
                    organization_id=org.id,
                )
                db.session.add(employee)
                db.session.commit()

                session.pop(_OAUTH_PROFILE_KEY, None)
                login_user(new_user)
                flash(f"Welcome! Your organization '{org_name}' has been created.", "loginsuccess")
                logger.info(f"[GoogleOAuth] New org created: org_id={org.id}, user_id={new_user.id}")
                return redirect(url_for("home_page"))

            except Exception as exc:
                db.session.rollback()
                logger.error(f"[GoogleOAuth] Org creation error: {exc}", exc_info=True)
                flash("An error occurred. Please try again.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

        elif org_option == "join":
            org_code = request.form.get("org_code", "").strip().upper()
            role_pref = request.form.get("role", "employee")

            org = Organization.query.filter_by(unique_code=org_code).first()
            if not org:
                flash("Invalid organization code. Please check and try again.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

            try:
                # Race condition check
                if User.query.filter_by(email=profile["email"]).first():
                    flash("An account with this email was just registered. Please log in normally.", "oautherror")
                    return render_template("login/google_onboard.html", profile=profile)

                role_mapped = UserRole.MANAGER if role_pref == "manager" else UserRole.EMPLOYEE
                username    = _make_unique_username(profile["display_name"])

                new_user = User(
                    username=username,
                    email=profile["email"],
                    password=generate_password_hash(secrets.token_hex(32)),
                    role=role_mapped,
                    organization_id=org.id,
                    oauth_provider="google",
                    oauth_provider_id=profile["google_id"],
                    must_change_password=False,
                    is_active=False,  # <--- Fix: Pending Admin Approval!
                )
                db.session.add(new_user)
                db.session.flush()

                employee = Employee(
                    name=profile["display_name"],
                    email=profile["email"],
                    user_id=new_user.id,
                    organization_id=org.id,
                )
                db.session.add(employee)

                # Create join request notification for org admin (Avoid N+1)
                from model import Notification
                admins = User.query.options(db.joinedload(User.employee)).filter_by(
                    organization_id=org.id,
                    role=UserRole.ADMIN,
                ).all()
                for admin in admins:
                    if admin.employee:
                        notif = Notification(
                            title="New Google Sign-In Join Request",
                            message=f"{profile['display_name']} ({profile['email']}) wants to join your organization via Google Sign-In.",
                            recipient_id=admin.employee.id,
                            organization_id=org.id,
                        )
                        db.session.add(notif)

                db.session.commit()
                session.pop(_OAUTH_PROFILE_KEY, None)

                logger.info(f"[GoogleOAuth] Join request created: user_id={new_user.id}, org_id={org.id}")
                return render_template(
                    "login/google_pending.html",
                    display_name=profile["display_name"],
                    org_name=org.name,
                )

            except Exception as exc:
                db.session.rollback()
                logger.error(f"[GoogleOAuth] Join error: {exc}", exc_info=True)
                flash("An error occurred. Please try again.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

        else:
            flash("Please select an option.", "oautherror")
            return render_template("login/google_onboard.html", profile=profile)

    return render_template("login/google_onboard.html", profile=profile)

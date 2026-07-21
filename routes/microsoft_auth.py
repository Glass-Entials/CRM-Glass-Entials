"""
routes/microsoft_auth.py
Microsoft Entra ID (Azure AD) OAuth 2.0 authentication blueprint for GlassEntials CRM.

Handles all three scenarios (identical flow to google_auth.py):
  1. Existing user (Microsoft OID found)  → login immediately
  2. New email, existing password account  → link Microsoft account and login
  3. Brand new email                       → redirect to shared onboarding page

Security:
  - State token (CSRF protection) stored in server-side session
  - Verified email only accepted (external/guest accounts rejected)
  - No duplicate users created (de-duplicated by email + provider ID)
  - is_active check enforces pending approval for join-org users
"""

import secrets
import string
import logging

from flask import (
    Blueprint,
    redirect,
    request,
    session,
    url_for,
    render_template,
    flash,
)
from flask_login import login_user, current_user
from werkzeug.security import generate_password_hash

from model import db, User, Employee, Organization, UserRole
from services.oauth_service import (
    microsoft_build_auth_url,
    microsoft_exchange_code,
    microsoft_get_userinfo,
    generate_oauth_state,
    microsoft_is_configured,
)

logger = logging.getLogger(__name__)

microsoft_auth_bp = Blueprint("microsoft_auth", __name__, url_prefix="/auth/microsoft")

_OAUTH_STATE_KEY   = "_microsoft_oauth_state"
_OAUTH_PROFILE_KEY = "_microsoft_oauth_profile"


# ---------------------------------------------------------------------------
# Helpers (mirrors google_auth.py helpers)
# ---------------------------------------------------------------------------

def _get_redirect_uri() -> str:
    return url_for("microsoft_auth.callback", _external=True)


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

@microsoft_auth_bp.route("/login")
def login():
    """Initiate Microsoft OAuth flow."""
    if current_user.is_authenticated:
        return redirect(url_for("home_page"))

    if not microsoft_is_configured():
        flash("Microsoft Sign-In is not configured on this server.", "loginerror")
        return redirect(url_for("auth.login"))

    state = generate_oauth_state()
    session[_OAUTH_STATE_KEY] = state

    auth_url = microsoft_build_auth_url(
        redirect_uri=_get_redirect_uri(),
        state=state,
    )
    return redirect(auth_url)


@microsoft_auth_bp.route("/callback")
def callback():
    """Handle the redirect back from Microsoft after user consents."""
    # ---- State verification (CSRF protection) ----
    returned_state = request.args.get("state", "")
    stored_state   = session.pop(_OAUTH_STATE_KEY, None)
    if not stored_state or not secrets.compare_digest(stored_state, returned_state):
        flash("Invalid authentication state. Please try again.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- Handle user denial ----
    error = request.args.get("error")
    if error:
        flash("Microsoft Sign-In was cancelled.", "loginerror")
        return redirect(url_for("auth.login"))

    code = request.args.get("code")
    if not code:
        flash("Microsoft did not return an authorization code.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- Exchange code for tokens ----
    try:
        token_data = microsoft_exchange_code(code, _get_redirect_uri())
    except Exception as exc:
        logger.error(f"[MicrosoftOAuth] Token exchange error: {exc}")
        flash("Failed to complete Microsoft Sign-In. Please try again.", "loginerror")
        return redirect(url_for("auth.login"))

    access_token = token_data.get("access_token")
    if not access_token:
        flash("Microsoft Sign-In failed: no access token returned.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- Fetch user info from Microsoft Graph ----
    try:
        profile = microsoft_get_userinfo(access_token)
    except Exception as exc:
        logger.error(f"[MicrosoftOAuth] Userinfo error: {exc}")
        flash("Failed to fetch your Microsoft profile. Please try again.", "loginerror")
        return redirect(url_for("auth.login"))

    # Enforce verified email (external/guest accounts are rejected in the service layer)
    if not profile.get("email_verified"):
        flash("Your Microsoft account does not have a verified email address. Please use a standard Microsoft account.", "loginerror")
        return redirect(url_for("auth.login"))

    microsoft_id  = profile.get("sub")        # Stable Microsoft OID
    email         = profile.get("email", "").lower().strip()
    display_name  = profile.get("name", email.split("@")[0])

    if not microsoft_id or not email:
        flash("Microsoft returned incomplete profile information.", "loginerror")
        return redirect(url_for("auth.login"))

    # ---- CASE 1: Existing user with same Microsoft provider ID ----
    existing_by_provider = User.query.filter_by(
        oauth_provider="microsoft",
        oauth_provider_id=microsoft_id,
    ).first()
    if existing_by_provider:
        if not getattr(existing_by_provider, "is_active", True):
            flash("Your account is pending administrator approval.", "loginerror")
            return redirect(url_for("auth.login"))

        login_user(existing_by_provider)
        flash(f"Welcome back, {existing_by_provider.username}!", "loginsuccess")
        logger.info(f"[MicrosoftOAuth] Login via provider ID: user_id={existing_by_provider.id}")
        return redirect(url_for("home_page"))

    # ---- CASE 2: Existing user matched by email → link account ----
    existing_by_email = User.query.filter(
        db.func.lower(User.email) == email
    ).first()
    if existing_by_email:
        if not getattr(existing_by_email, "is_active", True):
            flash("Your account is pending administrator approval.", "loginerror")
            return redirect(url_for("auth.login"))

        # Link Microsoft to existing account
        existing_by_email.oauth_provider    = "microsoft"
        existing_by_email.oauth_provider_id = microsoft_id
        db.session.commit()
        login_user(existing_by_email)
        flash(f"Microsoft account linked and you are now logged in, {existing_by_email.username}!", "loginsuccess")
        logger.info(f"[MicrosoftOAuth] Linked Microsoft to existing user_id={existing_by_email.id}")
        return redirect(url_for("home_page"))

    # ---- CASE 3: New email — start onboarding ----
    # Store Microsoft profile in session temporarily for the onboarding step.
    # Reuses the exact same onboarding template and route as Google OAuth.
    session[_OAUTH_PROFILE_KEY] = {
        "microsoft_id": microsoft_id,
        "email":        email,
        "display_name": display_name,
    }
    return redirect(url_for("microsoft_auth.onboard"))


@microsoft_auth_bp.route("/onboard", methods=["GET", "POST"])
def onboard():
    """
    New user onboarding page after Microsoft sign-in.
    Reuses the same google_onboard.html template and Create/Join flow.
    """
    profile = session.get(_OAUTH_PROFILE_KEY)
    if not profile:
        flash("Session expired. Please sign in with Microsoft again.", "loginerror")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        org_option = request.form.get("org_option")

        if org_option == "create":
            org_name = request.form.get("org_name", "").strip()
            if not org_name:
                flash("Please enter an organization name.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

            try:
                org = Organization(
                    name=org_name,
                    unique_code=_generate_org_code(),
                )
                db.session.add(org)
                db.session.flush()

                # Race condition check: ensure email is not taken mid-onboarding
                if User.query.filter_by(email=profile["email"]).first():
                    flash("An account with this email was just registered. Please log in normally.", "oautherror")
                    return render_template("login/google_onboard.html", profile=profile)

                username = _make_unique_username(profile["display_name"])
                new_user = User(
                    username=username,
                    email=profile["email"],
                    password=generate_password_hash(secrets.token_hex(32)),  # unusable placeholder
                    role=UserRole.ADMIN,
                    organization_id=org.id,
                    oauth_provider="microsoft",
                    oauth_provider_id=profile["microsoft_id"],
                    is_active=True,  # Org creator is always immediately active
                )
                db.session.add(new_user)
                db.session.flush()

                org.created_by = new_user.id

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
                logger.info(f"[MicrosoftOAuth] New org created: org_id={org.id}, user_id={new_user.id}")
                return redirect(url_for("home_page"))

            except Exception as exc:
                db.session.rollback()
                logger.error(f"[MicrosoftOAuth] Org creation error: {exc}", exc_info=True)
                flash("An error occurred. Please try again.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

        elif org_option == "join":
            org_code  = request.form.get("org_code", "").strip().upper()
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
                    oauth_provider="microsoft",
                    oauth_provider_id=profile["microsoft_id"],
                    must_change_password=False,
                    is_active=False,  # Pending Admin Approval
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

                # Notify all org admins (joinedload avoids N+1)
                from model import Notification
                admins = User.query.options(db.joinedload(User.employee)).filter_by(
                    organization_id=org.id,
                    role=UserRole.ADMIN,
                ).all()
                for admin in admins:
                    if admin.employee:
                        notif = Notification(
                            title="New Microsoft Sign-In Join Request",
                            message=f"{profile['display_name']} ({profile['email']}) wants to join your organization via Microsoft Sign-In.",
                            recipient_id=admin.employee.id,
                            organization_id=org.id,
                        )
                        db.session.add(notif)

                db.session.commit()
                session.pop(_OAUTH_PROFILE_KEY, None)

                logger.info(f"[MicrosoftOAuth] Join request created: user_id={new_user.id}, org_id={org.id}")
                return render_template(
                    "login/google_pending.html",
                    display_name=profile["display_name"],
                    org_name=org.name,
                )

            except Exception as exc:
                db.session.rollback()
                logger.error(f"[MicrosoftOAuth] Join error: {exc}", exc_info=True)
                flash("An error occurred. Please try again.", "oautherror")
                return render_template("login/google_onboard.html", profile=profile)

        else:
            flash("Please select an option.", "oautherror")
            return render_template("login/google_onboard.html", profile=profile)

    return render_template("login/google_onboard.html", profile=profile)

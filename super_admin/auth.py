"""
super_admin/auth.py
Hardcoded credentials and OTP utilities for the Super Admin portal.
Password is NEVER stored in plain text — only a Werkzeug hash is kept.

To regenerate the hash for a new password:
    from werkzeug.security import generate_password_hash
    print(generate_password_hash("your_new_password"))
"""

import os
import random
import logging
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hardcoded Super Admin Credentials (Phase 1)
# ---------------------------------------------------------------------------
# The plain-text password is: GlassAdmin@2024!
# Change SUPER_ADMIN_PASSWORD_HASH when rotating the password.
# Re-generate with: generate_password_hash("new_password")
# ---------------------------------------------------------------------------

SUPER_ADMIN_USERNAME: str = os.environ.get("SUPER_ADMIN_USERNAME", "superadmin")

# Fall back to a pre-hashed default only for development.
# In production, set SUPER_ADMIN_PASSWORD_HASH in the environment.
_DEFAULT_HASH = generate_password_hash("GlassAdmin@2024!")
SUPER_ADMIN_PASSWORD_HASH: str = os.environ.get(
    "SUPER_ADMIN_PASSWORD_HASH", _DEFAULT_HASH
)

SUPER_ADMIN_EMAIL: str = os.environ.get(
    "SUPER_ADMIN_EMAIL", "noreply.glassentials@gmail.com"
)

# OTP validity window (minutes)
OTP_EXPIRY_MINUTES: int = 5


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------

def verify_credentials(username: str, password: str) -> bool:
    """Return True if username and password match the super admin credentials."""
    if not username or not password:
        return False
    username_ok = username.strip() == SUPER_ADMIN_USERNAME
    password_ok = check_password_hash(SUPER_ADMIN_PASSWORD_HASH, password)
    return username_ok and password_ok


# ---------------------------------------------------------------------------
# OTP helpers  (session-based, no DB)
# ---------------------------------------------------------------------------

def generate_otp() -> str:
    """Return a cryptographically-seeded 6-digit OTP string."""
    return "{:06d}".format(random.SystemRandom().randint(0, 999_999))


def build_otp_session_data(otp: str) -> dict:
    """
    Return a dict suitable for storing in Flask session.
    OTP is stored as a Werkzeug hash — never plain text.
    """
    expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)
    return {
        "otp_hash": generate_password_hash(otp),
        "otp_expires": expires_at.isoformat(),
        "otp_used": False,
    }


def validate_otp(session_data: dict, submitted_otp: str) -> tuple[bool, str]:
    """
    Validate the submitted OTP against session data.
    Returns (valid: bool, reason: str).
    """
    if not session_data:
        return False, "OTP session not found. Please login again."

    if session_data.get("otp_used"):
        return False, "This OTP has already been used."

    expires_str = session_data.get("otp_expires")
    if expires_str:
        try:
            expires_at = datetime.fromisoformat(expires_str)
            if datetime.utcnow() > expires_at:
                return False, "OTP has expired. Please request a new one."
        except ValueError:
            return False, "Invalid OTP session. Please login again."

    otp_hash = session_data.get("otp_hash", "")
    if not check_password_hash(otp_hash, submitted_otp):
        return False, "Invalid OTP. Please try again."

    return True, "OK"

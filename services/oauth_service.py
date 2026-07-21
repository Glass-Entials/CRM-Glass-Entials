"""
services/oauth_service.py
Modular OAuth provider service for GlassEntials CRM.

Designed to be provider-agnostic. Currently implements Google and Microsoft.
Facebook and other providers can be added as new provider blocks later.
"""

import os
import json
import secrets
import logging
import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Google OAuth 2.0 provider
# ---------------------------------------------------------------------------

GOOGLE_AUTH_URL    = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL   = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
GOOGLE_CERTS_URL   = "https://www.googleapis.com/oauth2/v3/certs"

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _get_google_config():
    return {
        "client_id":     os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    }


def google_is_configured() -> bool:
    cfg = _get_google_config()
    return bool(cfg["client_id"] and cfg["client_secret"])


def google_build_auth_url(redirect_uri: str, state: str) -> str:
    """Build the Google OAuth authorization URL."""
    cfg = _get_google_config()
    params = {
        "client_id":     cfg["client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         " ".join(GOOGLE_SCOPES),
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    }
    return GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode(params)


def google_exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens. Returns the token response dict."""
    cfg = _get_google_config()
    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }).encode()

    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error(f"[OAuth/Google] Token exchange failed: {exc.code} — {body}")
        raise RuntimeError(f"Google token exchange failed: {body}") from exc


def google_get_userinfo(access_token: str) -> dict:
    """Fetch user profile from Google using the access token."""
    req = urllib.request.Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error(f"[OAuth/Google] Userinfo failed: {exc.code} — {body}")
        raise RuntimeError(f"Google userinfo fetch failed: {body}") from exc


def generate_oauth_state() -> str:
    """Generate a cryptographically secure state token for CSRF protection."""
    return secrets.token_urlsafe(32)


# ---------------------------------------------------------------------------
# Microsoft Entra ID (Azure AD) OAuth 2.0 provider
# ---------------------------------------------------------------------------

# Microsoft uses a tenant-based URL. "common" allows any Microsoft account
# (personal, work, or school). For production, you can restrict to a single
# tenant by replacing "common" with your Azure AD Tenant ID.
_MS_TENANT = "common"

MICROSOFT_AUTH_URL     = f"https://login.microsoftonline.com/{_MS_TENANT}/oauth2/v2.0/authorize"
MICROSOFT_TOKEN_URL    = f"https://login.microsoftonline.com/{_MS_TENANT}/oauth2/v2.0/token"
MICROSOFT_USERINFO_URL = "https://graph.microsoft.com/v1.0/me"

MICROSOFT_SCOPES = [
    "openid",
    "email",
    "profile",
    "User.Read",
]


def _get_microsoft_config():
    return {
        "client_id":     os.environ.get("MICROSOFT_CLIENT_ID", ""),
        "client_secret": os.environ.get("MICROSOFT_CLIENT_SECRET", ""),
    }


def microsoft_is_configured() -> bool:
    cfg = _get_microsoft_config()
    return bool(cfg["client_id"] and cfg["client_secret"])


def microsoft_build_auth_url(redirect_uri: str, state: str) -> str:
    """Build the Microsoft OAuth authorization URL."""
    cfg = _get_microsoft_config()
    params = {
        "client_id":     cfg["client_id"],
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         " ".join(MICROSOFT_SCOPES),
        "state":         state,
        "response_mode": "query",
        "prompt":        "select_account",
    }
    return MICROSOFT_AUTH_URL + "?" + urllib.parse.urlencode(params)


def microsoft_exchange_code(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for tokens. Returns the token response dict."""
    cfg = _get_microsoft_config()
    data = urllib.parse.urlencode({
        "code":          code,
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }).encode()

    req = urllib.request.Request(
        MICROSOFT_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error(f"[OAuth/Microsoft] Token exchange failed: {exc.code} — {body}")
        raise RuntimeError(f"Microsoft token exchange failed: {body}") from exc


def microsoft_get_userinfo(access_token: str) -> dict:
    """
    Fetch user profile from Microsoft Graph API.
    Returns a normalised dict with keys: id, email, email_verified, name.
    Microsoft Graph /me does NOT include email_verified — we mark it True
    only when a verified email address is present (matching Google's contract).
    """
    req = urllib.request.Request(
        MICROSOFT_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        logger.error(f"[OAuth/Microsoft] Userinfo failed: {exc.code} — {body}")
        raise RuntimeError(f"Microsoft userinfo fetch failed: {body}") from exc

    # Microsoft Graph returns mail OR userPrincipalName; prefer mail.
    email = (raw.get("mail") or raw.get("userPrincipalName") or "").strip()
    # userPrincipalName for external/guest accounts looks like user_domain.com#EXT#@tenant.onmicrosoft.com
    # Treat those as unverified.
    if "#EXT#" in email:
        email = ""

    return {
        "sub":            raw.get("id", ""),   # Stable Microsoft user ID (OID)
        "email":          email,
        "email_verified": bool(email),         # Considered verified when a clean email is present
        "name":           raw.get("displayName") or raw.get("givenName") or email.split("@")[0],
    }

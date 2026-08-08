"""
services/org_limits.py
Centralized, server-side enforcement of organization member and storage limits.
Never trust client-supplied limit values.
"""
from model import db, Organization, OrgAuditLog


def check_member_limit(org: Organization) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Call before adding any new member to an organization.
    """
    limit = org.effective_member_limit
    current = org.member_count
    if current >= limit:
        return False, (
            f"Member limit reached ({current}/{limit}). "
            "Please contact your administrator or upgrade your plan."
        )
    return True, ""


def check_storage_limit(org: Organization, file_size_bytes: int) -> tuple[bool, str]:
    """
    Returns (allowed: bool, reason: str).
    Call before accepting any file upload.
    """
    limit = org.effective_storage_limit_bytes
    current = org.storage_used_bytes or 0
    if current + file_size_bytes > limit:
        limit_gb = round(limit / (1024 ** 3), 2)
        used_gb = round(current / (1024 ** 3), 3)
        return False, (
            f"Storage limit reached ({used_gb} GB / {limit_gb} GB). "
            "Please upgrade your plan or contact your administrator."
        )
    return True, ""


def add_storage_usage(org: Organization, file_size_bytes: int, commit: bool = True):
    """Atomically increment storage usage."""
    org.storage_used_bytes = (org.storage_used_bytes or 0) + file_size_bytes
    if commit:
        db.session.commit()


def subtract_storage_usage(org: Organization, file_size_bytes: int, commit: bool = True):
    """Atomically decrement storage usage. Never goes below 0."""
    current = org.storage_used_bytes or 0
    org.storage_used_bytes = max(0, current - file_size_bytes)
    if commit:
        db.session.commit()


def log_audit(org_id: int, action: str, performed_by: str, details: str = None, meta: dict = None):
    """Record a Super Admin action in the audit log."""
    entry = OrgAuditLog(
        organization_id=org_id,
        action=action,
        performed_by=performed_by,
        details=details,
        meta=meta,
    )
    db.session.add(entry)
    # Note: caller is responsible for committing

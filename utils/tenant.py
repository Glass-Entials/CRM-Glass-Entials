from flask import session
from flask_login import current_user
from model import db, Organization, OrganizationMember, OrganizationStatus


def get_user_orgs(user=None):
    """Return list of (org, role) tuples the user belongs to."""
    u = user or current_user
    if not u or not u.is_authenticated:
        return []
    memberships = (
        OrganizationMember.query
        .filter_by(user_id=u.id, status='active')
        .join(Organization)
        .all()
    )
    return [(m.organization, m.role) for m in memberships]


def get_active_org_id(user=None):
    """
    Server-side: resolve the currently active organization ID for a user.
    Validates session value against actual membership.
    """
    u = user or current_user
    if not u or not u.is_authenticated:
        return None
    candidate = session.get('active_org_id') or u.organization_id
    if not candidate:
        return None
    member = OrganizationMember.query.filter_by(
        user_id=u.id, organization_id=candidate, status='active'
    ).first()
    if member:
        return candidate
    # Fallback: primary org from user record
    if u.organization_id:
        session.pop('active_org_id', None)
        return u.organization_id
    return None


def get_active_org(user=None):
    """Return the active Organization object."""
    org_id = get_active_org_id(user)
    if not org_id:
        return None
    return db.session.get(Organization, org_id)


def switch_org(org_id, user=None):
    """
    Switch active organization. Server-side enforcement:
    - User must be an active member of the org.
    - Org must not be suspended.
    Returns True on success, False if unauthorized.
    """
    u = user or current_user
    if not u or not u.is_authenticated:
        return False
    org = db.session.get(Organization, org_id)
    if not org or org.is_suspended:
        return False
    member = OrganizationMember.query.filter_by(
        user_id=u.id, organization_id=org_id, status='active'
    ).first()
    if not member:
        return False
    session['active_org_id'] = org_id
    if u.organization_id != org_id:
        u.organization_id = org_id
        if u.employee:
            u.employee.organization_id = org_id
        db.session.commit()
    return True


def ensure_org_membership(user, org, role=None):
    """Create OrganizationMember row if it doesn't exist."""
    from model import OrgMemberRole
    member = OrganizationMember.query.filter_by(
        user_id=user.id, organization_id=org.id
    ).first()
    if not member:
        r = role or OrgMemberRole.MEMBER
        member = OrganizationMember(
            user_id=user.id,
            organization_id=org.id,
            role=r,
            status='active'
        )
        db.session.add(member)
        db.session.commit()
    return member


def suspended_org_guard():
    """
    Call in before_request to block CRM access for suspended organizations.
    Returns (response, 403) if org is suspended, else None.
    """
    if not current_user.is_authenticated:
        return None
    org_id = get_active_org_id()
    if not org_id:
        return None
    org = db.session.get(Organization, org_id)
    if org and org.is_suspended:
        from flask import render_template
        return render_template('errors/org_suspended.html', org=org), 403
    return None

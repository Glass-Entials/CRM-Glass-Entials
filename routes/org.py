"""
routes/org.py
Organization management routes (User-facing):
- View Settings
- Create Organization
- Join Organization
- Switch Organization (already in app.py or here)
"""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from model import db, Organization, OrganizationMember, OrgMemberRole
from utils.tenant import get_active_org, get_user_orgs, switch_org
import random
import string

org_bp = Blueprint("org", __name__, url_prefix="/org")

@org_bp.route("/sync-members")
def sync_members():
    from model import User, OrganizationMember, OrgMemberRole, UserRole
    # Find all users with an organization_id
    users = User.query.filter(User.organization_id.isnot(None)).all()
    count = 0
    for u in users:
        # Check if they exist in organization_member
        member = OrganizationMember.query.filter_by(
            organization_id=u.organization_id, 
            user_id=u.id
        ).first()
        
        # Determine correct Phase 2 role based on Phase 1 user role
        if u.role in [UserRole.ADMIN, UserRole.MANAGER]:
            new_role = OrgMemberRole.OWNER
        else:
            new_role = OrgMemberRole.MEMBER
            
        if not member:
            # Create missing member
            member = OrganizationMember(
                organization_id=u.organization_id,
                user_id=u.id,
                role=new_role,
                status='active'
            )
            db.session.add(member)
            count += 1
        else:
            # Update role to match what user set in the User table
            member.role = new_role
            
    db.session.commit()
    return f"Synced {count} missing members and updated roles perfectly! You can go back to the dashboard now."

def generate_unique_code(length=6):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=length))
        if not Organization.query.filter_by(unique_code=code).first():
            return code

@org_bp.route("/settings", methods=["GET"])
@login_required
def organization_settings():
    active_org = get_active_org()
    user_orgs = get_user_orgs()
    
    if active_org:
        members = OrganizationMember.query.filter_by(
            organization_id=active_org.id, status='active'
        ).all()
    else:
        members = []
        
    return render_template(
        "org/settings.html", 
        active_org=active_org, 
        user_orgs=user_orgs, 
        members=members
    )


@org_bp.route("/create", methods=["POST"])
@login_required
def create_organization():
    if get_active_org():
        flash("You are already part of an organization. You cannot create another one.", "error")
        return redirect(url_for("org.organization_settings"))

    name = request.form.get("name", "").strip()
    if not name:
        flash("Organization name is required.", "error")
        return redirect(url_for("org.organization_settings"))
        
    # Generate slug
    import re
    base_slug = re.sub(r'[^a-zA-Z0-9]+', '-', name.lower()).strip('-')
    
    org = Organization(
        name=name,
        unique_code=generate_unique_code(),
        created_by=current_user.id
    )
    db.session.add(org)
    db.session.flush() # To get org.id
    
    org.slug = f"{base_slug}-{org.id}"
    
    # Add creator as owner
    member = OrganizationMember(
        organization_id=org.id,
        user_id=current_user.id,
        role=OrgMemberRole.OWNER,
        status='active'
    )
    db.session.add(member)
    db.session.commit()
    
    # Switch to the new org
    switch_org(org.id)
    flash(f"Organization '{name}' created successfully!", "success")
    return redirect(url_for("org.organization_settings"))


@org_bp.route("/join", methods=["POST"])
@login_required
def join_organization():
    if get_active_org():
        flash("You are already part of an organization. You cannot join another one.", "error")
        return redirect(url_for("org.organization_settings"))

    code = request.form.get("code", "").strip().upper()
    if not code:
        flash("Unique code is required to join.", "error")
        return redirect(url_for("org.organization_settings"))
        
    org = Organization.query.filter_by(unique_code=code).first()
    if not org:
        flash("Invalid organization code.", "error")
        return redirect(url_for("org.organization_settings"))
        
    if org.is_suspended:
        flash("This organization is currently suspended.", "error")
        return redirect(url_for("org.organization_settings"))
        
    # Check if already a member
    existing = OrganizationMember.query.filter_by(
        user_id=current_user.id,
        organization_id=org.id
    ).first()
    
    if existing:
        if existing.status != 'active':
            existing.status = 'active'
            db.session.commit()
            switch_org(org.id)
            flash(f"Rejoined '{org.name}' successfully.", "success")
        else:
            flash(f"You are already a member of '{org.name}'.", "info")
            switch_org(org.id)
    else:
        member = OrganizationMember(
            organization_id=org.id,
            user_id=current_user.id,
            role=OrgMemberRole.MEMBER,
            status='active'
        )
        db.session.add(member)
        db.session.commit()
        switch_org(org.id)
        flash(f"Successfully joined '{org.name}'.", "success")
        
    return redirect(url_for("org.organization_settings"))

"""
ai/crm_query.py
---------------
Controlled structured CRM query engine.
ALL functions enforce organization_id.
The LLM NEVER executes SQL — these are pre-built safe query functions.
"""

from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any


def resolve_creator_in_org(name: str, org_id: int) -> Optional[int]:
    """
    Look up a User by username within ONLY the given organization.
    Returns the user's id, or None if not found.
    NEVER searches across organizations — tenant isolation enforced here.

    Match order:
    1. Exact case-insensitive match on username
    2. username STARTS WITH the name (for 'Ratandeep' matching 'Ratandeep Putohit')
    """
    from model import User

    # 1. Exact match
    user = User.query.filter(
        User.organization_id == org_id,
        User.is_active == True,
        User.username.ilike(name),
    ).first()
    if user:
        return user.id

    # 2. Starts-with match (first name / prefix)
    user = User.query.filter(
        User.organization_id == org_id,
        User.is_active == True,
        User.username.ilike(f"{name} %"),
    ).first()
    return user.id if user else None


def get_customers(org_id: int, limit: int = 10, status: Optional[str] = None) -> List[Dict]:
    from model import Customer, CustomerStatus
    q = Customer.query.filter_by(organization_id=org_id, is_deleted=False)
    if status:
        try:
            q = q.filter(Customer.status == CustomerStatus(status))
        except ValueError:
            pass
    customers = q.order_by(Customer.id.desc()).limit(limit).all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "company": c.company,
            "email": c.email,
            "phone": c.phone_number,
            "status": c.status.value if c.status else None,
            "city": c.city,
            "state": c.state,
        }
        for c in customers
    ]


def count_customers(org_id: int) -> int:
    from model import Customer
    return Customer.query.filter_by(organization_id=org_id, is_deleted=False).count()


def get_leads(
    org_id: int,
    limit: int = 10,
    status: Optional[str] = None,
    created_by_id: Optional[int] = None,
) -> List[Dict]:
    from model import Lead, LeadStatus
    q = Lead.query.filter_by(organization_id=org_id, is_deleted=False)
    if status:
        try:
            q = q.filter(Lead.status == LeadStatus(status))
        except ValueError:
            pass
    if created_by_id is not None:
        q = q.filter(Lead.created_by == created_by_id)
    leads = q.order_by(Lead.id.desc()).limit(limit).all()
    return [
        {
            "id": l.id,
            "name": l.name,
            "company": l.company,
            "email": l.email,
            "phone": l.phone_number,
            "status": l.status.value if l.status else None,
            "city": l.city,
        }
        for l in leads
    ]


def count_leads(org_id: int, created_by_id: Optional[int] = None) -> int:
    from model import Lead
    q = Lead.query.filter_by(organization_id=org_id, is_deleted=False)
    if created_by_id is not None:
        q = q.filter(Lead.created_by == created_by_id)
    return q.count()


def get_quotations(org_id: int, limit: int = 10, status: Optional[str] = None) -> List[Dict]:
    from model import Quotation, QuotationStatus
    q = Quotation.query.filter_by(organization_id=org_id, is_deleted=False)
    if status:
        try:
            q = q.filter(Quotation.status == QuotationStatus(status))
        except ValueError:
            pass
    quotations = q.order_by(Quotation.issue_date.desc()).limit(limit).all()
    result = []
    for qt in quotations:
        result.append({
            "quotation_number": qt.quotation_number,
            "title": qt.quotation_title,
            "status": qt.status.value if qt.status else None,
            "issue_date": qt.issue_date.strftime("%Y-%m-%d") if qt.issue_date else None,
            "total_amount": qt.total_amount,
            "customer": qt.customer.name if qt.customer else None,
            "project": qt.project.name if qt.project else None,
        })
    return result


def count_quotations(org_id: int, status: Optional[str] = None) -> int:
    from model import Quotation, QuotationStatus
    q = Quotation.query.filter_by(organization_id=org_id, is_deleted=False)
    if status:
        try:
            q = q.filter(Quotation.status == QuotationStatus(status))
        except ValueError:
            pass
    return q.count()


def get_invoices(org_id: int, limit: int = 10, paid: Optional[bool] = None) -> List[Dict]:
    from model import Invoice
    q = Invoice.query.filter_by(organization_id=org_id, is_deleted=False)
    if paid is True:
        q = q.filter(Invoice.is_paid == True)
    elif paid is False:
        q = q.filter(Invoice.is_paid == False)
    invoices = q.order_by(Invoice.created_at.desc()).limit(limit).all()
    return [
        {
            "invoice_number": inv.invoice_number,
            "customer": inv.customer.name if inv.customer else None,
            "total": inv.total,
            "is_paid": inv.is_paid,
            "created_at": inv.created_at.strftime("%Y-%m-%d") if inv.created_at else None,
        }
        for inv in invoices
    ]


def count_invoices(org_id: int, paid: Optional[bool] = None) -> int:
    from model import Invoice
    q = Invoice.query.filter_by(organization_id=org_id, is_deleted=False)
    if paid is True:
        q = q.filter(Invoice.is_paid == True)
    elif paid is False:
        q = q.filter(Invoice.is_paid == False)
    return q.count()


def get_tasks(org_id: int, limit: int = 10, status: Optional[str] = None) -> List[Dict]:
    from model import Task, TaskStatus
    q = Task.query.filter_by(organization_id=org_id, is_deleted=False)
    if status:
        try:
            q = q.filter(Task.status == TaskStatus(status))
        except ValueError:
            pass
    tasks = q.order_by(Task.due_date.asc()).limit(limit).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status.value if t.status else None,
            "due_date": t.due_date.strftime("%Y-%m-%d") if t.due_date else None,
            "description": t.description,
        }
        for t in tasks
    ]


def get_overdue_tasks(org_id: int) -> List[Dict]:
    from model import Task, TaskStatus
    now = datetime.utcnow()
    tasks = Task.query.filter(
        Task.organization_id == org_id,
        Task.is_deleted == False,
        Task.status == TaskStatus.PENDING,
        Task.due_date < now,
    ).order_by(Task.due_date.asc()).limit(20).all()
    return [
        {
            "id": t.id,
            "title": t.title,
            "due_date": t.due_date.strftime("%Y-%m-%d %H:%M") if t.due_date else None,
            "description": t.description,
        }
        for t in tasks
    ]


def get_projects(org_id: int, limit: int = 10, status: Optional[str] = None) -> List[Dict]:
    from model import Project, ProjectStatus
    q = Project.query.filter_by(organization_id=org_id, is_deleted=False)
    if status:
        try:
            q = q.filter(Project.status == ProjectStatus(status))
        except ValueError:
            pass
    projects = q.order_by(Project.id.desc()).limit(limit).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "status": p.status.value if p.status else None,
            "description": p.description,
            "customer": p.customer.name if p.customer else None,
        }
        for p in projects
    ]


def get_recent_activities(org_id: int, limit: int = 10) -> List[Dict]:
    from model import ActivityLog
    logs = ActivityLog.query.filter_by(organization_id=org_id).order_by(
        ActivityLog.created_at.desc()
    ).limit(limit).all()
    return [
        {
            "action": l.action,
            "entity_type": l.entity_type,
            "entity_name": l.entity_name,
            "created_at": l.created_at.strftime("%Y-%m-%d %H:%M") if l.created_at else None,
        }
        for l in logs
    ]


def get_monthly_quotation_summary(org_id: int) -> Dict:
    from model import Quotation
    from sqlalchemy import func
    from model import db
    now = datetime.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = db.session.query(
        func.count(Quotation.id).label("count"),
        func.sum(Quotation.total_amount).label("total"),
    ).filter(
        Quotation.organization_id == org_id,
        Quotation.is_deleted == False,
        Quotation.issue_date >= start,
    ).one()
    return {
        "month": now.strftime("%B %Y"),
        "count": result.count or 0,
        "total_amount": float(result.total or 0),
    }

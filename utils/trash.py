"""
Enterprise Trash Management System
====================================
Handles soft-delete, restore, dependency scanning, and safe cascade-delete
for all CRM modules in a single reusable service.
"""

from datetime import datetime
from model import (
    db, Customer, Lead, Contact, Project, Task,
    LeadActivity, LeadComment, LeadSystemLog, LeadFollowUp,
    CustomerDocument, TaskActivity, TaskFollowupRequest, TaskFollowupResponse,
    ActivityLog, Invoice, InvoiceItem, Quotation, QuotationItem,
    QuotationCustomFieldValue, QuotationAttachment, QuotationSignature,
    QuotationTaxSummary, QuotationTermLink, CRMDocument,
    ContactActivity, ContactNote, ContactSystemLog, ContactDocument,
    Payment, PaymentRemark, PaymentDocument,
)
from utils.activity import log_activity


# ---------------------------------------------------------------------------
# Pagination helper for merged queries across multiple models
# ---------------------------------------------------------------------------
class MockPagination:
    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, (total + per_page - 1) // per_page)
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1


# ---------------------------------------------------------------------------
# Module registry
# ---------------------------------------------------------------------------
TRASH_MODELS = {
    "customers": Customer,
    "leads":     Lead,
    "contacts":  Contact,
    "projects":  Project,
    "tasks":     Task,
}


# ---------------------------------------------------------------------------
# Maps intermediate FK field -> which parent label holds their IDs
# Used to delete through-children before direct children
# ---------------------------------------------------------------------------
_THROUGH_PARENT_MAP = {
    "quotation_id": "Quotations",
    "invoice_id":   "Invoices",
    "payment_id":   "Payments",
    "request_id":   "Followup Requests",
}


# ---------------------------------------------------------------------------
# Dependency Map per module
# Tuple: (Model, fk_field, direct_fk_to_parent, display_label, is_through)
# is_through=True  -> child of an intermediate parent (e.g. QuotationItem)
# is_through=False -> direct FK to the record being deleted
# Order matters: through-children are deleted before their direct parents
# ---------------------------------------------------------------------------
DEPENDENCY_MAP = {
    "leads": [
        # through-children (deleted first via parent IDs)
        (QuotationAttachment,       "quotation_id", None,          "Quotation Attachments",   True),
        (QuotationSignature,        "quotation_id", None,          "Quotation Signatures",    True),
        (QuotationTaxSummary,       "quotation_id", None,          "Quotation Tax Summaries", True),
        (QuotationTermLink,         "quotation_id", None,          "Quotation Term Links",    True),
        (QuotationCustomFieldValue, "quotation_id", None,          "Quotation Fields",        True),
        (QuotationItem,             "quotation_id", None,          "Quotation Items",         True),
        # direct children
        (Quotation,                 "lead_id",      "lead_id",     "Quotations",              False),
        (LeadActivity,              "lead_id",      "lead_id",     "Activities",              False),
        (LeadComment,               "lead_id",      "lead_id",     "Comments",                False),
        (LeadSystemLog,             "lead_id",      "lead_id",     "System Logs",             False),
        (LeadFollowUp,              "lead_id",      "lead_id",     "Follow-ups",              False),
        (ActivityLog,               "lead_id",      "lead_id",     "Audit Logs",              False),
        (CRMDocument,               "lead_id",      "lead_id",     "Documents",               False),
    ],
    "customers": [
        (QuotationAttachment,       "quotation_id", None,          "Quotation Attachments",   True),
        (QuotationSignature,        "quotation_id", None,          "Quotation Signatures",    True),
        (QuotationTaxSummary,       "quotation_id", None,          "Quotation Tax Summaries", True),
        (QuotationTermLink,         "quotation_id", None,          "Quotation Term Links",    True),
        (QuotationCustomFieldValue, "quotation_id", None,          "Quotation Fields",        True),
        (QuotationItem,             "quotation_id", None,          "Quotation Items",         True),
        (Quotation,                 "customer_id",  "customer_id", "Quotations",              False),
        (PaymentDocument,           "payment_id",   None,          "Payment Documents",       True),
        (PaymentRemark,             "payment_id",   None,          "Payment Remarks",         True),
        (Payment,                   "customer_id",  "customer_id", "Payments",                False),
        (InvoiceItem,               "invoice_id",   None,          "Invoice Items",           True),
        (Invoice,                   "customer_id",  "customer_id", "Invoices",                False),
        (CustomerDocument,          "customer_id",  "customer_id", "Documents",               False),
        (ActivityLog,               "customer_id",  "customer_id", "Audit Logs",              False),
    ],
    "contacts": [
        (ContactActivity,           "contact_id",   "contact_id",  "Activities",              False),
        (ContactNote,               "contact_id",   "contact_id",  "Notes",                   False),
        (ContactSystemLog,          "contact_id",   "contact_id",  "System Logs",             False),
        (ContactDocument,           "contact_id",   "contact_id",  "Documents",               False),
    ],
    "projects": [
        (QuotationAttachment,       "quotation_id", None,          "Quotation Attachments",   True),
        (QuotationSignature,        "quotation_id", None,          "Quotation Signatures",    True),
        (QuotationTaxSummary,       "quotation_id", None,          "Quotation Tax Summaries", True),
        (QuotationTermLink,         "quotation_id", None,          "Quotation Term Links",    True),
        (QuotationCustomFieldValue, "quotation_id", None,          "Quotation Fields",        True),
        (QuotationItem,             "quotation_id", None,          "Quotation Items",         True),
        (Quotation,                 "project_id",   "project_id",  "Quotations",              False),
        (InvoiceItem,               "invoice_id",   None,          "Invoice Items",           True),
        (Invoice,                   "project_id",   "project_id",  "Invoices",                False),
        (ActivityLog,               "project_id",   "project_id",  "Audit Logs",              False),
    ],
    "tasks": [
        (TaskFollowupResponse,      "request_id",   None,          "Followup Responses",      True),
        (TaskFollowupRequest,       "task_id",       "task_id",     "Followup Requests",       False),
        (TaskActivity,              "task_id",       "task_id",     "Activities",              False),
        (ActivityLog,               "task_id",       "task_id",     "Audit Logs",              False),
    ],
}


# ---------------------------------------------------------------------------
# TrashManager
# ---------------------------------------------------------------------------
class TrashManager:

    @staticmethod
    def _get_model(module_name):
        return TRASH_MODELS.get(module_name)

    @staticmethod
    def _record_display_name(module_name, record):
        if module_name == "contacts":
            return f"{record.first_name} {record.last_name or ''}".strip()
        return getattr(record, "name", None) or getattr(record, "title", None) or f"#{record.id}"

    @staticmethod
    def scan_dependencies(module_name, record_id, org_id):
        """
        Returns (display_name: str, deps: dict {label: count})
        """
        model = TrashManager._get_model(module_name)
        if not model:
            return "", {}

        record = model.query.filter_by(id=record_id, organization_id=org_id, is_deleted=True).first()
        if not record:
            return "", {}

        name_display = TrashManager._record_display_name(module_name, record)
        dep_list = DEPENDENCY_MAP.get(module_name, [])

        # Collect direct child IDs first (needed to query through-children)
        intermediate_ids = {}
        for entry in dep_list:
            model_cls, fk_field, direct_fk, label, is_through = entry
            if not is_through:
                ids = [r.id for r in model_cls.query.filter(
                    getattr(model_cls, direct_fk) == record_id
                ).with_entities(model_cls.id).all()]
                intermediate_ids[label] = ids

        deps = {}
        for entry in dep_list:
            model_cls, fk_field, direct_fk, label, is_through = entry
            if is_through:
                parent_label = _THROUGH_PARENT_MAP.get(fk_field)
                parent_ids = intermediate_ids.get(parent_label, [])
                if parent_ids:
                    count = model_cls.query.filter(
                        getattr(model_cls, fk_field).in_(parent_ids)
                    ).count()
                    if count:
                        deps[label] = deps.get(label, 0) + count
            else:
                ids = intermediate_ids.get(label, [])
                if ids:
                    deps[label] = len(ids)

        return name_display, deps

    @staticmethod
    def permanently_delete(module_name, record_id, org_id, actor_id):
        """
        Returns (success: bool, message: str, dep_summary: dict)
        """
        model = TrashManager._get_model(module_name)
        if not model:
            return False, "Invalid module", {}

        record = model.query.filter_by(id=record_id, organization_id=org_id, is_deleted=True).first()
        if not record:
            return False, "Record not found or not in trash", {}

        name_display = TrashManager._record_display_name(module_name, record)
        dep_list = DEPENDENCY_MAP.get(module_name, [])

        try:
            # Phase 1: collect intermediate parent IDs
            intermediate_ids = {}
            for entry in dep_list:
                model_cls, fk_field, direct_fk, label, is_through = entry
                if not is_through:
                    ids = [r.id for r in model_cls.query.filter(
                        getattr(model_cls, direct_fk) == record_id
                    ).with_entities(model_cls.id).all()]
                    intermediate_ids[label] = ids

            children_deleted = {}

            # Phase 2: delete through-children first
            for entry in dep_list:
                model_cls, fk_field, direct_fk, label, is_through = entry
                if is_through:
                    parent_label = _THROUGH_PARENT_MAP.get(fk_field)
                    parent_ids = intermediate_ids.get(parent_label, [])
                    if parent_ids:
                        count = model_cls.query.filter(
                            getattr(model_cls, fk_field).in_(parent_ids)
                        ).delete(synchronize_session=False)
                        if count:
                            children_deleted[label] = children_deleted.get(label, 0) + count

            # Phase 3: delete direct children
            for entry in dep_list:
                model_cls, fk_field, direct_fk, label, is_through = entry
                if not is_through:
                    count = model_cls.query.filter(
                        getattr(model_cls, direct_fk) == record_id
                    ).delete(synchronize_session=False)
                    if count:
                        children_deleted[label] = count

            # Phase 4: delete parent
            db.session.delete(record)
            db.session.commit()

            # Audit
            try:
                log_activity(
                    f"{module_name}_permanently_deleted",
                    org_id=org_id,
                    actor_id=actor_id,
                    extra=str({
                        "record_id": record_id,
                        "record_name": name_display,
                        "related_deleted": children_deleted,
                    }),
                )
            except Exception:
                pass

            return True, f"{name_display} and all related data permanently deleted.", children_deleted

        except Exception:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            return False, "Unable to delete record because an unexpected error occurred.", {}

    @staticmethod
    def restore(module_name, record_id, org_id, actor_id):
        model = TrashManager._get_model(module_name)
        if not model:
            return False, "Invalid module"

        record = model.query.filter_by(id=record_id, organization_id=org_id, is_deleted=True).first()
        if not record:
            return False, "Record not found or already restored"

        name_display = TrashManager._record_display_name(module_name, record)

        # Duplicate check for customers
        if module_name == "customers":
            phone = getattr(record, "phone_number", None)
            email = getattr(record, "email", None)
            q = Customer.query.filter(
                Customer.id != record_id,
                Customer.organization_id == org_id,
                Customer.is_deleted == False,
            )
            if phone and email:
                q = q.filter((Customer.phone_number == phone) | (Customer.email == email))
            elif phone:
                q = q.filter(Customer.phone_number == phone)
            elif email:
                q = q.filter(Customer.email == email)
            if q.count() > 0:
                return False, "Cannot restore: an active customer with the same phone/email already exists."

        try:
            record.is_deleted = False
            record.deleted_at = None
            record.deleted_by = None
            db.session.commit()
            log_activity(f"{module_name}_restored", org_id=org_id, actor_id=actor_id, extra=f"Restored {name_display}")
            return True, f"{name_display} restored successfully."
        except Exception:
            db.session.rollback()
            return False, "Unable to restore record. Please try again."


# ---------------------------------------------------------------------------
# Backward-compatible wrappers used by existing routes
# ---------------------------------------------------------------------------
def get_trashed_records(module_name, org_id, page=1, per_page=30, search=None):
    if module_name == "all":
        all_records = []
        for mod_name, model in TRASH_MODELS.items():
            query = model.query.filter_by(organization_id=org_id, is_deleted=True)
            if search:
                if hasattr(model, 'name'):
                    query = query.filter(model.name.ilike(f"%{search}%"))
                elif hasattr(model, 'title'):
                    query = query.filter(model.title.ilike(f"%{search}%"))
                elif mod_name == "contacts":
                    query = query.filter(
                        (model.first_name.ilike(f"%{search}%")) |
                        (model.last_name.ilike(f"%{search}%"))
                    )
            records = query.all()
            for r in records:
                r.module_name = mod_name
            all_records.extend(records)
        all_records.sort(key=lambda x: x.deleted_at or datetime.min, reverse=True)
        total = len(all_records)
        start = (page - 1) * per_page
        return MockPagination(all_records[start:start + per_page], page, per_page, total)

    model = TRASH_MODELS.get(module_name)
    if not model:
        return None
    query = model.query.filter_by(organization_id=org_id, is_deleted=True)
    if search:
        if hasattr(model, 'name'):
            query = query.filter(model.name.ilike(f"%{search}%"))
        elif hasattr(model, 'title'):
            query = query.filter(model.title.ilike(f"%{search}%"))
        elif module_name == "contacts":
            query = query.filter(
                (model.first_name.ilike(f"%{search}%")) |
                (model.last_name.ilike(f"%{search}%"))
            )
    pagination = query.order_by(model.deleted_at.desc()).paginate(page=page, per_page=per_page, error_out=False)
    for item in pagination.items:
        item.module_name = module_name
    return pagination


def restore_record(module_name, record_id, org_id, actor_id):
    return TrashManager.restore(module_name, record_id, org_id, actor_id)


def permanently_delete_record(module_name, record_id, org_id, actor_id):
    success, message, _ = TrashManager.permanently_delete(module_name, record_id, org_id, actor_id)
    return success, message

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


from enum import IntEnum

class DeletePriority(IntEnum):
    # Priorities ensure deletion in correct order as requested:
    # Activities -> Notes -> Tasks -> Files -> Contacts -> Parent Record
    THROUGH_CHILD = 5   # Must go first
    ACTIVITY = 10
    NOTE = 20
    SYSTEM_LOG = 25
    TASK = 30
    FILE = 40
    CONTACT = 50
    TRANSACTION = 60
    OTHER = 90

class DependencyRegistry:
    _registry = {}

    @classmethod
    def register(cls, module, model_cls, fk_field, direct_fk, label, is_through=False, priority=DeletePriority.OTHER):
        if module not in cls._registry:
            cls._registry[module] = []
        cls._registry[module].append({
            "model_cls": model_cls,
            "fk_field": fk_field,
            "direct_fk": direct_fk,
            "label": label,
            "is_through": is_through,
            "priority": priority
        })

    @classmethod
    def get_dependencies(cls, module):
        deps = cls._registry.get(module, [])
        return sorted(deps, key=lambda x: x["priority"])

# ---------------------------------------------------------------------------
# Register Leads
DependencyRegistry.register("leads", QuotationAttachment, "quotation_id", None, "Quotation Attachments", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("leads", QuotationSignature, "quotation_id", None, "Quotation Signatures", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("leads", QuotationTaxSummary, "quotation_id", None, "Quotation Tax Summaries", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("leads", QuotationTermLink, "quotation_id", None, "Quotation Term Links", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("leads", QuotationCustomFieldValue, "quotation_id", None, "Quotation Fields", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("leads", QuotationItem, "quotation_id", None, "Quotation Items", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("leads", LeadActivity, "lead_id", "lead_id", "Activities", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("leads", ActivityLog, "lead_id", "lead_id", "Audit Logs", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("leads", LeadComment, "lead_id", "lead_id", "Comments", False, DeletePriority.NOTE)
DependencyRegistry.register("leads", LeadSystemLog, "lead_id", "lead_id", "System Logs", False, DeletePriority.SYSTEM_LOG)
DependencyRegistry.register("leads", LeadFollowUp, "lead_id", "lead_id", "Follow-ups", False, DeletePriority.TASK)
DependencyRegistry.register("leads", CRMDocument, "lead_id", "lead_id", "Documents", False, DeletePriority.FILE)
DependencyRegistry.register("leads", Task, "lead_id", "lead_id", "Tasks", False, DeletePriority.TASK)
DependencyRegistry.register("leads", Contact, "lead_id", "lead_id", "Contacts", False, DeletePriority.CONTACT)
DependencyRegistry.register("leads", Quotation, "lead_id", "lead_id", "Quotations", False, DeletePriority.TRANSACTION)

# Register Customers
DependencyRegistry.register("customers", QuotationAttachment, "quotation_id", None, "Quotation Attachments", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", QuotationSignature, "quotation_id", None, "Quotation Signatures", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", QuotationTaxSummary, "quotation_id", None, "Quotation Tax Summaries", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", QuotationTermLink, "quotation_id", None, "Quotation Term Links", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", QuotationCustomFieldValue, "quotation_id", None, "Quotation Fields", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", QuotationItem, "quotation_id", None, "Quotation Items", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", PaymentDocument, "payment_id", None, "Payment Documents", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", PaymentRemark, "payment_id", None, "Payment Remarks", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", InvoiceItem, "invoice_id", None, "Invoice Items", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("customers", ActivityLog, "customer_id", "customer_id", "Audit Logs", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("customers", CustomerDocument, "customer_id", "customer_id", "Documents", False, DeletePriority.FILE)
DependencyRegistry.register("customers", Quotation, "customer_id", "customer_id", "Quotations", False, DeletePriority.TRANSACTION)
DependencyRegistry.register("customers", Payment, "customer_id", "customer_id", "Payments", False, DeletePriority.TRANSACTION)
DependencyRegistry.register("customers", Invoice, "customer_id", "customer_id", "Invoices", False, DeletePriority.TRANSACTION)

# Register Contacts
DependencyRegistry.register("contacts", ContactActivity, "contact_id", "contact_id", "Activities", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("contacts", ContactNote, "contact_id", "contact_id", "Notes", False, DeletePriority.NOTE)
DependencyRegistry.register("contacts", ContactSystemLog, "contact_id", "contact_id", "System Logs", False, DeletePriority.SYSTEM_LOG)
DependencyRegistry.register("contacts", ContactDocument, "contact_id", "contact_id", "Documents", False, DeletePriority.FILE)

# Register Projects
DependencyRegistry.register("projects", QuotationAttachment, "quotation_id", None, "Quotation Attachments", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", QuotationSignature, "quotation_id", None, "Quotation Signatures", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", QuotationTaxSummary, "quotation_id", None, "Quotation Tax Summaries", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", QuotationTermLink, "quotation_id", None, "Quotation Term Links", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", QuotationCustomFieldValue, "quotation_id", None, "Quotation Fields", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", QuotationItem, "quotation_id", None, "Quotation Items", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", InvoiceItem, "invoice_id", None, "Invoice Items", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("projects", ActivityLog, "project_id", "project_id", "Audit Logs", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("projects", Quotation, "project_id", "project_id", "Quotations", False, DeletePriority.TRANSACTION)
DependencyRegistry.register("projects", Invoice, "project_id", "project_id", "Invoices", False, DeletePriority.TRANSACTION)

# Register Tasks
DependencyRegistry.register("tasks", TaskFollowupResponse, "request_id", None, "Followup Responses", True, DeletePriority.THROUGH_CHILD)
DependencyRegistry.register("tasks", TaskActivity, "task_id", "task_id", "Activities", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("tasks", ActivityLog, "task_id", "task_id", "Audit Logs", False, DeletePriority.ACTIVITY)
DependencyRegistry.register("tasks", TaskFollowupRequest, "task_id", "task_id", "Followup Requests", False, DeletePriority.TASK)


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
    def _get_direct_query(model_cls, direct_fk, module_name, record_id):
        """Safely build a query for direct children, handling polymorphic models."""
        # Special polymorphic mapping for ActivityLog
        if model_cls.__name__ == 'ActivityLog':
            entity_type_map = {
                "leads": "lead", "customers": "customer", "contacts": "contact", 
                "projects": "project", "tasks": "task"
            }
            etype = entity_type_map.get(module_name)
            if etype and hasattr(model_cls, "entity_type") and hasattr(model_cls, "entity_id"):
                return model_cls.query.filter_by(entity_type=etype, entity_id=record_id)
            return None
            
        # Standard foreign key validation
        if direct_fk and hasattr(model_cls, direct_fk):
            return model_cls.query.filter(getattr(model_cls, direct_fk) == record_id)
            
        return None

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
        dep_list = DependencyRegistry.get_dependencies(module_name)

        # Collect direct child IDs first (needed to query through-children)
        intermediate_ids = {}
        for entry in dep_list:
            if not entry["is_through"]:
                q = TrashManager._get_direct_query(entry["model_cls"], entry["direct_fk"], module_name, record_id)
                if q is not None:
                    ids = [r.id for r in q.with_entities(entry["model_cls"].id).all()]
                    intermediate_ids[entry["label"]] = ids

        deps = {}
        for entry in dep_list:
            if entry["is_through"]:
                parent_label = _THROUGH_PARENT_MAP.get(entry["fk_field"])
                parent_ids = intermediate_ids.get(parent_label, [])
                if parent_ids and hasattr(entry["model_cls"], entry["fk_field"]):
                    count = entry["model_cls"].query.filter(
                        getattr(entry["model_cls"], entry["fk_field"]).in_(parent_ids)
                    ).count()
                    if count:
                        deps[entry["label"]] = deps.get(entry["label"], 0) + count
            else:
                ids = intermediate_ids.get(entry["label"], [])
                if ids:
                    deps[entry["label"]] = len(ids)

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
        dep_list = DependencyRegistry.get_dependencies(module_name)

        try:
            import logging
            logging.info(f"--- START PERMANENT DELETE: {module_name} #{record_id} ({name_display}) ---")
            
            # Phase 1: collect intermediate parent IDs
            intermediate_ids = {}
            logging.info(">>> Phase 1: Collecting IDs")
            for entry in dep_list:
                if not entry["is_through"]:
                    q = TrashManager._get_direct_query(entry["model_cls"], entry["direct_fk"], module_name, record_id)
                    if q is not None:
                        ids = [r.id for r in q.with_entities(entry["model_cls"].id).all()]
                        intermediate_ids[entry["label"]] = ids
                        logging.info(f"  [Scan] {entry['model_cls'].__name__} ({entry['label']}): found {len(ids)} record(s) -> IDs: {ids}")

            children_deleted = {}

            # Phase 2: delete through-children first (e.g. QuotationItem via Quotation)
            logging.info(">>> Phase 2: Deleting Through Children")
            for entry in dep_list:
                if entry["is_through"]:
                    parent_label = _THROUGH_PARENT_MAP.get(entry["fk_field"])
                    parent_ids = intermediate_ids.get(parent_label, [])
                    if parent_ids and hasattr(entry["model_cls"], entry["fk_field"]):
                        q = entry["model_cls"].query.filter(
                            getattr(entry["model_cls"], entry["fk_field"]).in_(parent_ids)
                        )
                        matched = q.count()
                        count = q.delete(synchronize_session=False)
                        if count:
                            children_deleted[entry["label"]] = children_deleted.get(entry["label"], 0) + count
                        logging.info(f"  [Delete Through] {entry['model_cls'].__name__} ({entry['label']}) via parents {parent_ids}: {count}/{matched} deleted.")

            # Phase 2.5: Break circular FK references before child deletion
            # Lead has lead.contact_id -> contact.id (circular with contact.lead_id -> lead.id)
            # We must NULL out lead.contact_id before we can delete any Contact rows.
            logging.info(">>> Phase 2.5: Breaking Circular FK References")
            CIRCULAR_NULLIFIERS = {
                # module -> list of (own_column_to_null,)
                # Whenever deleting a Lead, NULL its contact_id first so Contact rows can be deleted
                "leads": ["contact_id"],
            }
            for col in CIRCULAR_NULLIFIERS.get(module_name, []):
                if hasattr(record, col) and getattr(record, col) is not None:
                    old_val = getattr(record, col)
                    setattr(record, col, None)
                    db.session.flush()   # write UPDATE lead SET contact_id=NULL immediately
                    logging.info(f"  [Circular NULL] {model.__name__}.{col} set NULL (was {old_val})")

            # Phase 3: delete direct children
            logging.info(">>> Phase 3: Deleting Direct Children (in priority order)")
            for entry in dep_list:
                if not entry["is_through"]:
                    q = TrashManager._get_direct_query(entry["model_cls"], entry["direct_fk"], module_name, record_id)
                    if q is not None:
                        matched = q.count()
                        count = q.delete(synchronize_session=False)
                        if count:
                            children_deleted[entry["label"]] = count
                        logging.info(f"  [Delete Direct] {entry['model_cls'].__name__} ({entry['label']}) Priority:{entry['priority']}: {count}/{matched} deleted.")

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

        except Exception as e:
            db.session.rollback()
            import logging
            logging.error(f"Permanent delete failed for {module_name} ID {record_id}: {str(e)}", exc_info=True)
            return False, "Unable to prepare this record for permanent deletion. Please try again later.", {}

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

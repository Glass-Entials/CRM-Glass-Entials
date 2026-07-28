import re

with open('utils/trash.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Define replacement
replacement = '''from enum import IntEnum

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
'''

content = re.sub(r"# ---------------------------------------------------------------------------\n# Dependency Map per module.*?DEPENDENCY_MAP = \{.*?\n\}\n", replacement, content, flags=re.DOTALL)

with open('utils/trash.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done rewriting registry')

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from enum import Enum
from datetime import datetime


db = SQLAlchemy()

# --- ENUM DEFINITIONS ---

class UserRole(Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    EMPLOYEE = "employee"

class LeadStatus(Enum):
    NEW = "New"
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    PROSPECT = "Prospect"

class CustomerStatus(Enum):
    NEW = "New"
    REQUIREMENT_UNDERSTOOD = "Requirement Understood"
    MEASUREMENT_SCHEDULED = "Measurement Scheduled"
    QUOTATION_SENT = "Quotation Sent"
    FOLLOW_UP = "Follow Up"
    ORDER_CONFIRMED = "Order Confirmed"
    IN_PRODUCTION = "In Production"
    READY_FOR_DISPATCH = "Ready for Dispatch"
    INSTALLATION_SCHEDULED = "Installation Scheduled"
    INSTALLATION_DONE = "Installation Done"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"

class ProjectStatus(Enum):
    PLANNING = "Planning"
    IN_PROGRESS = "In Progress"
    ON_HOLD = "On Hold"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"

class QuotationStatus(Enum):
    DRAFT = "Draft"
    SENT = "Sent"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"

class ProjectWorkType(Enum):
    GLASS = "Glass"
    HARDWARE = "Hardware"
    MIRROR = "Mirror"

class ProjectCategory(Enum):
    COMMERCIAL = "Commercial"
    RESIDENTIAL = "Residential"

class LeadSource(Enum):
    WEBSITE = "Website"
    GOOGLE = "Google"
    SOCIAL_MEDIA = "Social Media"
    REFERRAL = "Referral"
    WALK_IN = "Walk-in"
    OTHER = "Other"

class ActivityType(Enum):
    CALL = "Call"
    MEETING = "Meeting"
    EMAIL = "Email"
    NOTE = "Note"
    TASK = "Task"

class TaskStatus(Enum):
    PENDING = "Pending"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"

class InvoiceStatus(Enum):
    UNPAID = "Unpaid"
    PAID = "Paid"
    PARTIALLY_PAID = "Partially Paid"
    CANCELLED = "Cancelled"
    OVERDUE = "Overdue"

class ExpenseCategory(Enum):
    TRAVEL = "Travel"
    MATERIALS = "Materials"
    HARDWARE = "Hardware"
    SALARY = "Salary"
    SITE_EXPENSE = "Site Expense"
    OFFICE_SUPPLIES = "Office Supplies"
    OTHER = "Other"

class ExpenseStatus(Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    PAID = "Paid"

class Organization(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    unique_code = db.Column(db.String(10), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True) # User ID who created it
    is_active = db.Column(db.Boolean, default=True)

    # Relationships
    users = db.relationship('User', back_populates='organization', foreign_keys='User.organization_id')
    employees = db.relationship('Employee', back_populates='organization')
    leads = db.relationship('Lead', back_populates='organization')
    customers = db.relationship('Customer', back_populates='organization')
    activities = db.relationship('LeadActivity', back_populates='organization')
    expenses = db.relationship('Expense', back_populates='organization')

    def __repr__(self):
        return f'<Organization {self.name}>'

# --- MODELS ---

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False) # Supports Argon2/BCrypt hashes
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=True)
    role = db.Column(db.Enum(UserRole, values_callable=lambda x: [e.value for e in x]), nullable=False, default=UserRole.EMPLOYEE)
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    # 1-to-1 relationship with Employee profile
    employee = db.relationship('Employee', back_populates='user', uselist=False, cascade="all, delete-orphan")
    organization = db.relationship('Organization', back_populates='users', foreign_keys=[organization_id])

    def __repr__(self):
        return f'<User {self.username}>'

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    position = db.Column(db.String(50), nullable=True)
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    email = db.Column(db.String(120), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)
    profile_pic = db.Column(db.String(255), nullable=True)  # Filename of profile picture
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    is_deleted = db.Column(db.Boolean, default=False)
    
    # Relationship to User
    user = db.relationship('User', back_populates='employee')
    organization = db.relationship('Organization', back_populates='employees')


    def __repr__(self):
        return f'<Employee {self.name}>'

class Lead(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    company = db.Column(db.String(100), nullable=True)
    source = db.Column(db.Enum(LeadSource, values_callable=lambda x: [e.value for e in x]), default=LeadSource.OTHER)
    status = db.Column(db.Enum(LeadStatus, values_callable=lambda x: [e.value for e in x]), default=LeadStatus.NEW, index=True)
    notes = db.Column(db.Text, nullable=True)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(100), nullable=True)
    
    # GST Details
    gst_number = db.Column(db.String(15), nullable=True, index=True)
    trade_name = db.Column(db.String(200), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    pincode = db.Column(db.String(10), nullable=True)
    business_type = db.Column(db.String(100), nullable=True)
    gst_status = db.Column(db.String(50), nullable=True)
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    is_deleted = db.Column(db.Boolean, default=False)

    # Foreign Keys
    assigned_to = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    updated_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)

    # Relationships
    assignee = db.relationship('Employee', foreign_keys=[assigned_to], backref='assigned_leads')
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='leads_created')
    updater = db.relationship('Employee', foreign_keys=[updated_by], backref='leads_updated')
    documents = db.relationship('CRMDocument', backref='lead', lazy='dynamic', cascade='all, delete-orphan')
    organization = db.relationship('Organization', back_populates='leads')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Lead {self.name}>'

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), unique=True, nullable=True, index=True)
    
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(50), nullable=True)
    company = db.Column(db.String(100), nullable=True)
    source = db.Column(db.Enum(LeadSource, values_callable=lambda x: [e.value for e in x]), default=LeadSource.OTHER)
    status = db.Column(db.Enum(CustomerStatus, values_callable=lambda x: [e.value for e in x]), default=CustomerStatus.NEW, index=True)
    notes = db.Column(db.Text, nullable=True)
    
    # GST Details
    gst_number = db.Column(db.String(15), nullable=True, index=True)
    trade_name = db.Column(db.String(200), nullable=True)
    state = db.Column(db.String(100), nullable=True)
    pincode = db.Column(db.String(10), nullable=True)
    business_type = db.Column(db.String(100), nullable=True)
    gst_status = db.Column(db.String(50), nullable=True)
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    is_deleted = db.Column(db.Boolean, default=False)

    # Foreign Keys
    assigned_to = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    updated_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)

    # Relationships
    lead = db.relationship('Lead', backref=db.backref('converted_customer', uselist=False))
    assignee = db.relationship('Employee', foreign_keys=[assigned_to], backref='assigned_customers')
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='customers_created')
    updater = db.relationship('Employee', foreign_keys=[updated_by], backref='customers_updated')
    organization = db.relationship('Organization', back_populates='customers')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Customer {self.name}>'

class CustomerDocument(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False) # Stored filename
    original_name = db.Column(db.String(255), nullable=False) # Original user filename
    file_type = db.Column(db.String(50), nullable=True) # e.g. 'pdf', 'docx', 'png'
    uploaded_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    # Relationships
    customer = db.relationship('Customer', backref=db.backref('documents', cascade='all, delete-orphan'))
    
    def __repr__(self):
        return f'<CustomerDocument {self.original_name}>'

class LeadActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=False, index=True)
    activity_type = db.Column(db.Enum(ActivityType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)

    # Relationships
    lead = db.relationship('Lead', backref=db.backref('activities', cascade='all, delete-orphan'))
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='activities_created')
    organization = db.relationship('Organization', back_populates='activities')

    def __repr__(self):
        return f'<LeadActivity {self.activity_type.name} for Lead {self.lead_id}>'

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_date = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.Enum(TaskStatus, values_callable=lambda x: [e.value for e in x]), default=TaskStatus.PENDING, index=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    # Foreign Keys
    assigned_to = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    
    # Relationships
    assignee = db.relationship('Employee', foreign_keys=[assigned_to], backref=db.backref('tasks_assigned', lazy='dynamic'))
    creator = db.relationship('User', foreign_keys=[created_by], backref=db.backref('tasks_created', lazy='dynamic'))
    lead_record = db.relationship('Lead', foreign_keys=[lead_id], backref=db.backref('tasks_related', lazy='dynamic'))
    project = db.relationship('Project', foreign_keys=[project_id], backref=db.backref('tasks_related', lazy='dynamic'))
    organization = db.relationship('Organization', foreign_keys=[organization_id], backref=db.backref('tasks', lazy='dynamic'))
    documents = db.relationship('CRMDocument', backref='task', lazy='dynamic', cascade='all, delete-orphan')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Task {self.title}>'

class DailyTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    date = db.Column(db.Date, default=datetime.utcnow().date, nullable=False)
    task_description = db.Column(db.Text, nullable=False)
    hours_spent = db.Column(db.Float, nullable=True)
    work_category = db.Column(db.String(50), nullable=True, default="General") # Site visit, fabrication, office, etc.
    
    # Tenancy
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())

    # Relationships
    employee = db.relationship('Employee', backref=db.backref('daily_tasks', lazy='dynamic'))
    organization = db.relationship('Organization', backref=db.backref('daily_tasks', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('daily_tasks', lazy='dynamic'))
    documents = db.relationship('CRMDocument', backref='daily_task', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<DailyTask {self.id} for Employee {self.employee_id}>'
class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.Enum(ProjectStatus, values_callable=lambda x: [e.value for e in x]), default=ProjectStatus.PLANNING, index=True)
    work_type = db.Column(db.Enum(ProjectWorkType, values_callable=lambda x: [e.value for e in x]), default=ProjectWorkType.GLASS, nullable=True)
    category = db.Column(db.Enum(ProjectCategory, values_callable=lambda x: [e.value for e in x]), default=ProjectCategory.COMMERCIAL, nullable=True)
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    # Optional link to customer
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True, index=True)
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    is_deleted = db.Column(db.Boolean, default=False)
    
    # Foreign Keys
    assigned_to = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    updated_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)

    # Relationships
    customer = db.relationship('Customer', backref=db.backref('projects', lazy='dynamic'))
    assignee = db.relationship('Employee', foreign_keys=[assigned_to], backref='assigned_projects')
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='projects_created')
    updater = db.relationship('Employee', foreign_keys=[updated_by], backref='projects_updated')
    documents = db.relationship('CRMDocument', backref='project', lazy='dynamic', cascade='all, delete-orphan')
    organization = db.relationship('Organization', backref='projects')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Project {self.name}>'

class ActivityLog(db.Model):
    """Universal activity log for all CRM actions."""
    __tablename__ = 'activity_log'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # What happened
    action = db.Column(db.String(50), nullable=False)          # e.g. 'customer_added', 'project_updated'
    entity_type = db.Column(db.String(30), nullable=False)     # 'customer', 'lead', 'project', 'document'
    entity_id = db.Column(db.Integer, nullable=True)           # ID of the affected record
    entity_name = db.Column(db.String(200), nullable=True)     # Name snapshot for display
    description = db.Column(db.Text, nullable=True)            # Full human-readable description
    
    # Who did it
    actor_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    
    # Multi-tenancy
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    # Relationships
    actor = db.relationship('Employee', foreign_keys=[actor_id], backref='activity_logs')
    organization = db.relationship('Organization', backref='activity_logs')
    
    def __repr__(self):
        return f'<ActivityLog {self.action} on {self.entity_type} {self.entity_id}>'

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    sender_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    
    title = db.Column(db.String(150), nullable=False)
    message = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(255), nullable=True) # URL to redirect when clicked
    
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    # Tenancy
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    
    # Relationships
    recipient = db.relationship('Employee', foreign_keys=[recipient_id], backref=db.backref('notifications', lazy='dynamic'))
    sender = db.relationship('Employee', foreign_keys=[sender_id], backref=db.backref('sent_notifications', lazy='dynamic'))
    organization = db.relationship('Organization', backref='notifications_list')

    def __repr__(self):
        return f'<Notification {self.title} for {self.recipient_id}>'

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    invoice_title = db.Column(db.String(100), default="Tax Invoice")
    
    subtotal = db.Column(db.Float, default=0.0)
    amount = db.Column(db.Float, default=0.0) # Used as taxable amount in some contexts
    total_discount = db.Column(db.Float, default=0.0)
    total_discount_type = db.Column(db.String(10), default='flat') # 'flat' | 'percent'
    
    additional_charges = db.Column(db.Float, default=0.0)
    additional_charges_taxable = db.Column(db.Boolean, default=False)
    additional_charges_label = db.Column(db.String(100), nullable=True)
    
    sgst = db.Column(db.Float, default=0.0)
    cgst = db.Column(db.Float, default=0.0)
    igst = db.Column(db.Float, default=0.0)
    gst_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    total_in_words = db.Column(db.String(255), nullable=True)
    total_quantity = db.Column(db.Float, default=0.0)
    
    status = db.Column(db.Enum(InvoiceStatus, values_callable=lambda x: [e.value for e in x]), default=InvoiceStatus.UNPAID, index=True)
    
    issue_date = db.Column(db.DateTime, default=db.func.current_timestamp())
    due_date = db.Column(db.DateTime, nullable=True)
    
    # GST type for auto split
    is_igst = db.Column(db.Boolean, default=False)
    
    notes = db.Column(db.Text, nullable=True)
    terms_conditions = db.Column(db.Text, nullable=True)
    
    # Signature
    signature_label = db.Column(db.String(100), default="Authorised Signatory")
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    
    # Relationships
    customer = db.relationship('Customer', backref=db.backref('invoices', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('invoices', lazy='dynamic'))
    organization = db.relationship('Organization', backref='invoices')
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='invoices_created')
    items = db.relationship('InvoiceItem', backref='invoice', cascade='all, delete-orphan')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Invoice {self.invoice_number}>'

class InvoiceItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False, index=True)
    
    sort_order = db.Column(db.Integer, default=0)
    group_name = db.Column(db.String(100), nullable=True)
    item_name = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    
    width = db.Column(db.Float, nullable=True)
    height = db.Column(db.Float, nullable=True)
    dimensions = db.Column(db.String(100), nullable=True)
    
    formula_type = db.Column(db.String(50), nullable=True, default='standard') # 'sqft', 'pcs', 'custom'
    quantity = db.Column(db.Float, default=1.0)
    chargeable_quantity = db.Column(db.Float, nullable=True)
    
    unit = db.Column(db.String(20), nullable=True, default='Sq.Ft')
    rate = db.Column(db.Float, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    discount_type = db.Column(db.String(10), default='flat') # 'flat' | 'percent'
    
    # Tax
    gst_percentage = db.Column(db.Float, default=18.0)
    sgst_rate = db.Column(db.Float, default=9.0)
    cgst_rate = db.Column(db.Float, default=9.0)
    igst_rate = db.Column(db.Float, default=0.0)
    
    # Amounts
    amount = db.Column(db.Float, default=0.0)     # pre-tax subtotal
    gst_amount = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)      # amount + gst
    
    def __repr__(self):
        return f'<InvoiceItem {self.description}>'

class Expense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=db.func.current_timestamp())
    category = db.Column(db.Enum(ExpenseCategory, values_callable=lambda x: [e.value for e in x]), default=ExpenseCategory.OTHER)
    status = db.Column(db.Enum(ExpenseStatus, values_callable=lambda x: [e.value for e in x]), default=ExpenseStatus.PENDING)
    description = db.Column(db.Text, nullable=True)
    receipt_path = db.Column(db.String(255), nullable=True) # Filename of the proof
    is_deleted = db.Column(db.Boolean, default=False)
    
    # Foreign Keys
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    
    # Relationships
    organization = db.relationship('Organization', back_populates='expenses')
    employee = db.relationship('Employee', backref=db.backref('expenses', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('expenses', lazy='dynamic'))

    @property
    def category_display(self):
        return self.category.value if self.category else "Other"
    
    @property
    def status_display(self):
        return self.status.value if self.status else "Pending"

    def __repr__(self):
        return f'<Expense {self.title} - {self.amount}>'

class QuotationDocType(Enum):
    QUOTATION = "Quotation"
    PROFORMA_INVOICE = "Proforma Invoice"
    SALES_ORDER = "Sales Order"

class ValidTillType(Enum):
    DATE = "date"
    DAYS = "days"
    NONE = "none"


class Quotation(db.Model):
    __tablename__ = 'quotation'
    id = db.Column(db.Integer, primary_key=True)

    # Header
    quotation_title = db.Column(db.String(100), default="Quotation")
    doc_type = db.Column(db.Enum(QuotationDocType, values_callable=lambda x: [e.value for e in x]), default=QuotationDocType.QUOTATION)
    quotation_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    issue_date = db.Column(db.DateTime, default=db.func.current_timestamp())
    due_date = db.Column(db.DateTime, nullable=True)
    valid_till_type = db.Column(db.String(10), default='date')   # 'date' | 'days' | 'none'
    valid_till_days = db.Column(db.Integer, nullable=True)

    # Links
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)

    # Standard custom fields
    source = db.Column(db.String(255), nullable=True)
    timeline = db.Column(db.String(255), nullable=True)
    amendment_no = db.Column(db.String(50), nullable=True)
    measurements = db.Column(db.String(255), nullable=True)
    quote_level = db.Column(db.String(255), nullable=True)
    sales_source = db.Column(db.String(255), nullable=True)
    delivery_terms = db.Column(db.String(500), nullable=True)
    payment_terms = db.Column(db.String(500), nullable=True)
    shop_drawings = db.Column(db.String(255), nullable=True)
    project_lead_name = db.Column(db.String(255), nullable=True)
    application = db.Column(db.String(255), nullable=True)
    manager_in_charge = db.Column(db.String(255), nullable=True)
    references = db.Column(db.String(255), nullable=True)
    delivery_tat = db.Column(db.String(255), nullable=True)
    mode_of_delivery = db.Column(db.String(255), nullable=True)
    unloading = db.Column(db.String(255), nullable=True)
    freight_unloading = db.Column(db.String(255), nullable=True)

    # Financials
    subtotal = db.Column(db.Float, default=0.0)
    total_discount = db.Column(db.Float, default=0.0)
    total_discount_type = db.Column(db.String(10), default='flat')   # 'flat' | 'percent'
    additional_charges = db.Column(db.Float, default=0.0)
    additional_charges_taxable = db.Column(db.Boolean, default=False)
    additional_charges_label = db.Column(db.String(100), nullable=True)
    sgst = db.Column(db.Float, default=0.0)
    cgst = db.Column(db.Float, default=0.0)
    igst = db.Column(db.Float, default=0.0)
    gst_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    total_in_words = db.Column(db.String(255), nullable=True)
    total_quantity = db.Column(db.Float, default=0.0)

    # Payment
    advance_payment = db.Column(db.Float, default=0.0)
    mode_of_payment = db.Column(db.String(100), nullable=True)
    balance_payment = db.Column(db.Float, default=0.0)

    # GST type for auto split
    is_igst = db.Column(db.Boolean, default=False)  # If True → IGST, else SGST+CGST

    # Notes / Terms / Annexure
    notes = db.Column(db.Text, nullable=True)
    additional_info = db.Column(db.Text, nullable=True)
    terms_conditions = db.Column(db.Text, nullable=True)

    # Signature
    signature_label = db.Column(db.String(100), default="Authorised Signatory")

    # Status
    status = db.Column(db.Enum(QuotationStatus, values_callable=lambda x: [e.value for e in x]), default=QuotationStatus.DRAFT, index=True)

    # Audit
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)

    # Relationships
    customer = db.relationship('Customer', backref=db.backref('quotations', lazy='dynamic'))
    lead = db.relationship('Lead', backref=db.backref('quotations', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('quotations', lazy='dynamic'))
    organization = db.relationship('Organization', backref='quotations')
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='quotations_created')
    items = db.relationship('QuotationItem', backref='quotation', cascade='all, delete-orphan', order_by='QuotationItem.sort_order')
    custom_field_values = db.relationship('QuotationCustomFieldValue', backref='quotation', cascade='all, delete-orphan')
    attachments = db.relationship('QuotationAttachment', backref='quotation', cascade='all, delete-orphan')
    signatures = db.relationship('QuotationSignature', backref='quotation', cascade='all, delete-orphan')
    tax_summary = db.relationship('QuotationTaxSummary', backref='quotation', cascade='all, delete-orphan')
    term_links = db.relationship('QuotationTermLink', backref='quotation', cascade='all, delete-orphan')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    @property
    def doc_type_display(self):
        return self.doc_type.value if self.doc_type else "Quotation"

    def __repr__(self):
        return f'<Quotation {self.quotation_number}>'


class QuotationItem(db.Model):
    __tablename__ = 'quotation_item'
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    
    sort_order = db.Column(db.Integer, default=0)
    group_name = db.Column(db.String(100), nullable=True) # E.g., 'Glass', 'Hardware'
    item_name = db.Column(db.String(255), nullable=True)
    description = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(255), nullable=True) # Path to uploaded picture
    
    width = db.Column(db.Float, nullable=True)
    height = db.Column(db.Float, nullable=True)
    dimensions = db.Column(db.String(100), nullable=True)
    
    formula_type = db.Column(db.String(50), nullable=True, default='standard') # 'sqft', 'pcs', 'custom'
    quantity = db.Column(db.Float, nullable=False, default=1.0)
    chargeable_quantity = db.Column(db.Float, nullable=True) # E.g., Area if sqft formula
    
    unit = db.Column(db.String(20), nullable=True, default='Sq.Ft')
    rate = db.Column(db.Float, nullable=False, default=0.0)
    discount = db.Column(db.Float, default=0.0)
    discount_type = db.Column(db.String(10), default='flat')   # 'flat' | 'percent'

    # Tax
    gst_percentage = db.Column(db.Float, default=18.0)
    sgst_rate = db.Column(db.Float, default=9.0)
    cgst_rate = db.Column(db.Float, default=9.0)
    igst_rate = db.Column(db.Float, default=0.0)

    # Amounts
    amount = db.Column(db.Float, default=0.0)     # pre-tax subtotal
    gst_amount = db.Column(db.Float, default=0.0)
    total = db.Column(db.Float, default=0.0)      # amount + gst

    # Flags
    is_undefined_size = db.Column(db.Boolean, default=False)
    is_all_selection = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<QuotationItem {self.item_name} for Quotation {self.quotation_id}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# QUOTATION SETTINGS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QuotationSettings(db.Model):
    __tablename__ = 'quotation_settings'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, unique=True, index=True)

    # Numbering
    number_prefix = db.Column(db.String(10), default='GL')
    number_counter = db.Column(db.Integer, default=1)
    number_format = db.Column(db.String(30), default='{prefix}/{year}/{seq:04d}')  # e.g. GL/26/0001

    # Defaults
    validity_days = db.Column(db.Integer, default=30)
    default_gst_rate = db.Column(db.Float, default=18.0)
    default_sgst_rate = db.Column(db.Float, default=9.0)
    default_cgst_rate = db.Column(db.Float, default=9.0)
    default_igst_rate = db.Column(db.Float, default=18.0)
    default_payment_terms = db.Column(db.Text, nullable=True)
    default_delivery_terms = db.Column(db.Text, nullable=True)
    default_notes = db.Column(db.Text, nullable=True)

    # Company profile for quotation header
    company_name = db.Column(db.String(200), nullable=True)
    company_address = db.Column(db.Text, nullable=True)
    company_gstin = db.Column(db.String(15), nullable=True)
    company_pan = db.Column(db.String(10), nullable=True)
    company_email = db.Column(db.String(120), nullable=True)
    company_phone = db.Column(db.String(20), nullable=True)
    company_logo = db.Column(db.String(255), nullable=True)
    company_state = db.Column(db.String(100), nullable=True)

    # Bank details
    bank_name = db.Column(db.String(100), nullable=True)
    bank_account_no = db.Column(db.String(50), nullable=True)
    bank_ifsc = db.Column(db.String(20), nullable=True)
    bank_branch = db.Column(db.String(100), nullable=True)
    beneficiary_name = db.Column(db.String(100), nullable=True)

    # PDF footer
    pdf_footer_text = db.Column(db.Text, nullable=True)
    show_bank_details_on_pdf = db.Column(db.Boolean, default=True)
    show_signature_on_pdf = db.Column(db.Boolean, default=True)

    # Default signature
    default_signature_path = db.Column(db.String(255), nullable=True)
    default_signature_label = db.Column(db.String(100), default="Authorised Signatory")

    organization = db.relationship('Organization', backref=db.backref('quotation_settings', uselist=False))

    def __repr__(self):
        return f'<QuotationSettings org={self.organization_id}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CUSTOM FIELDS SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QuotationCustomField(db.Model):
    """Field definitions — reusable blueprint (like a field schema)"""
    __tablename__ = 'quotation_custom_field'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)

    label = db.Column(db.String(100), nullable=False)
    field_key = db.Column(db.String(50), nullable=False)   # snake_case key used in forms
    field_type = db.Column(db.String(20), default='text')  # text | number | date | select | textarea
    options = db.Column(db.Text, nullable=True)            # JSON array for select type
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_default_visible = db.Column(db.Boolean, default=False)
    is_system = db.Column(db.Boolean, default=False)       # System fields can't be deleted

    organization = db.relationship('Organization', backref=db.backref('quotation_custom_fields', lazy='dynamic'))

    def __repr__(self):
        return f'<QuotationCustomField {self.label}>'


class QuotationCustomFieldValue(db.Model):
    """Per-quotation values of custom fields"""
    __tablename__ = 'quotation_custom_field_value'
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    field_id = db.Column(db.Integer, db.ForeignKey('quotation_custom_field.id'), nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)

    field = db.relationship('QuotationCustomField', backref='field_values')

    def __repr__(self):
        return f'<QuotationCustomFieldValue quotation={self.quotation_id} field={self.field_id}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TERMS & CONDITIONS SYSTEM
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QuotationTermGroup(db.Model):
    __tablename__ = 'quotation_term_group'
    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)           # e.g. "Annexure-1"
    description = db.Column(db.String(255), nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    is_default = db.Column(db.Boolean, default=False)          # auto-attach to new quotations

    organization = db.relationship('Organization', backref=db.backref('quotation_term_groups', lazy='dynamic'))
    terms = db.relationship('QuotationTerm', backref='group', cascade='all, delete-orphan',
                            order_by='QuotationTerm.sort_order')

    def __repr__(self):
        return f'<QuotationTermGroup {self.name}>'


class QuotationTerm(db.Model):
    __tablename__ = 'quotation_term'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('quotation_term_group.id'), nullable=False, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    term_title = db.Column(db.String(255), nullable=False)
    term_body = db.Column(db.Text, nullable=False)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    version = db.Column(db.Integer, default=1)

    organization = db.relationship('Organization', backref=db.backref('quotation_terms', lazy='dynamic'))

    def __repr__(self):
        return f'<QuotationTerm {self.term_title}>'


class QuotationTermLink(db.Model):
    """Links a specific term (or override) to a quotation"""
    __tablename__ = 'quotation_term_link'
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    term_id = db.Column(db.Integer, db.ForeignKey('quotation_term.id'), nullable=True, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('quotation_term_group.id'), nullable=True, index=True)
    # Override / quotation-specific text
    custom_title = db.Column(db.String(255), nullable=True)
    custom_body = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0)

    term = db.relationship('QuotationTerm', backref='term_links')
    group = db.relationship('QuotationTermGroup', backref='term_links')

    def __repr__(self):
        return f'<QuotationTermLink quotation={self.quotation_id} term={self.term_id}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ATTACHMENTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QuotationAttachment(db.Model):
    __tablename__ = 'quotation_attachment'
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)        # bytes
    uploaded_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True)

    def __repr__(self):
        return f'<QuotationAttachment {self.original_name}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SIGNATURE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QuotationSignature(db.Model):
    __tablename__ = 'quotation_signature'
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    sig_type = db.Column(db.String(10), default='upload')   # 'upload' | 'pad'
    image_path = db.Column(db.String(255), nullable=True)   # for uploaded image
    pad_data = db.Column(db.Text, nullable=True)            # base64 data URL from pad
    label = db.Column(db.String(100), default="Authorised Signatory")
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    def __repr__(self):
        return f'<QuotationSignature quotation={self.quotation_id}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TAX SUMMARY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class QuotationTaxSummary(db.Model):
    __tablename__ = 'quotation_tax_summary'
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    gst_rate = db.Column(db.Float, nullable=False)          # e.g. 18.0
    taxable_amount = db.Column(db.Float, default=0.0)
    sgst_amount = db.Column(db.Float, default=0.0)
    cgst_amount = db.Column(db.Float, default=0.0)
    igst_amount = db.Column(db.Float, default=0.0)
    total_tax = db.Column(db.Float, default=0.0)

    def __repr__(self):
        return f'<QuotationTaxSummary quotation={self.quotation_id} rate={self.gst_rate}%>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PRODUCTS / CATALOGUE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ProductCategory(Enum):
    GLASS = "Glass"
    HARDWARE = "Hardware"
    MIRROR = "Mirror"
    ALUMINIUM = "Aluminium"
    ACCESSORIES = "Accessories"
    RAW_MATERIAL = "Raw Material"
    OTHER = "Other"


class ProductStatus(Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    DISCONTINUED = "Discontinued"


class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)

    # Identity
    name = db.Column(db.String(200), nullable=False)
    sku = db.Column(db.String(50), nullable=True, index=True)          # Stock Keeping Unit code
    description = db.Column(db.Text, nullable=True)
    image = db.Column(db.String(255), nullable=True)                    # Stored image filename

    # Classification
    category = db.Column(db.Enum(ProductCategory, values_callable=lambda x: [e.value for e in x]),
                         default=ProductCategory.OTHER)
    unit = db.Column(db.String(30), nullable=True, default='Sq.Ft')    # Sq.Ft, Pcs, Rft, Kg …
    status = db.Column(db.Enum(ProductStatus, values_callable=lambda x: [e.value for e in x]),
                       default=ProductStatus.ACTIVE, index=True)

    # Pricing
    cost_price = db.Column(db.Float, default=0.0)                      # Purchase/cost price
    selling_price = db.Column(db.Float, default=0.0)                   # Default selling/rate
    min_price = db.Column(db.Float, nullable=True)                     # Floor price (optional)
    gst_rate = db.Column(db.Float, default=18.0)                       # GST %
    hsn_code = db.Column(db.String(20), nullable=True)                 # HSN/SAC code for GST

    # Stock
    stock_quantity = db.Column(db.Float, default=0.0)
    min_stock_alert = db.Column(db.Float, nullable=True)               # Alert when stock < this

    # Additional info
    notes = db.Column(db.Text, nullable=True)
    tags = db.Column(db.String(255), nullable=True)                     # Comma-separated tags

    # Multi-tenant
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)

    # Audit
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(),
                           onupdate=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)

    # Relationships
    organization = db.relationship('Organization', backref=db.backref('products', lazy='dynamic'))
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='products_created')

    @property
    def category_display(self):
        return self.category.value if self.category else 'Other'

    @property
    def status_display(self):
        return self.status.value if self.status else 'Active'

    @property
    def margin_percent(self):
        if self.cost_price and self.cost_price > 0:
            return round(((self.selling_price - self.cost_price) / self.cost_price) * 100, 1)
        return None

    @property
    def is_low_stock(self):
        if self.min_stock_alert is not None:
            return self.stock_quantity <= self.min_stock_alert
        return False

    def __repr__(self):
        return f'<Product {self.name}>'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CRM DOCUMENT MANAGEMENT (LEADS & PROJECTS)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CRMDocument(db.Model):
    __tablename__ = 'crm_document'
    id = db.Column(db.Integer, primary_key=True)
    
    # Links to standard entities
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    task_id = db.Column(db.Integer, db.ForeignKey('task.id'), nullable=True, index=True)
    daily_task_id = db.Column(db.Integer, db.ForeignKey('daily_task.id'), nullable=True, index=True)
    
    # File details
    filename = db.Column(db.String(255), nullable=False)        # Unique stored name
    original_name = db.Column(db.String(255), nullable=False)   # User-uploaded name
    file_type = db.Column(db.String(50), nullable=True)         # Extension or Mime
    file_size = db.Column(db.Integer, nullable=True)            # In bytes
    
    # Meta
    description = db.Column(db.String(255), nullable=True)
    uploaded_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    uploaded_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    
    # Tenancy
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    
    # Relationships
    uploader = db.relationship('Employee', foreign_keys=[uploaded_by], backref='uploaded_docs')
    organization = db.relationship('Organization', backref='crm_documents')

    def __repr__(self):
        return f'<CRMDocument {self.original_name}>'
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

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Task {self.title}>'
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

class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_number = db.Column(db.String(20), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    
    amount = db.Column(db.Float, default=0.0)
    gst_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    
    status = db.Column(db.Enum(InvoiceStatus, values_callable=lambda x: [e.value for e in x]), default=InvoiceStatus.UNPAID, index=True)
    
    issue_date = db.Column(db.DateTime, default=db.func.current_timestamp())
    due_date = db.Column(db.DateTime, nullable=True)
    
    notes = db.Column(db.Text, nullable=True)
    
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
    description = db.Column(db.String(255), nullable=False)
    quantity = db.Column(db.Float, default=1.0)
    rate = db.Column(db.Float, default=0.0)
    amount = db.Column(db.Float, default=0.0)
    
    def __repr__(self):
        return f'<InvoiceItem {self.description} for Invoice {self.invoice_id}>'

class Quotation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quotation_number = db.Column(db.String(50), unique=True, nullable=False, index=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True, index=True)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True, index=True)
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False, index=True)
    
    # Custom quotation fields modeled after Refrens
    source = db.Column(db.String(255), nullable=True)
    amendment_no = db.Column(db.String(50), nullable=True)
    measurements = db.Column(db.String(255), nullable=True)
    quote_level = db.Column(db.String(255), nullable=True)
    
    subtotal = db.Column(db.Float, default=0.0)
    gst_amount = db.Column(db.Float, default=0.0)
    total_amount = db.Column(db.Float, default=0.0)
    
    status = db.Column(db.Enum(QuotationStatus, values_callable=lambda x: [e.value for e in x]), default=QuotationStatus.DRAFT, index=True)
    
    issue_date = db.Column(db.DateTime, default=db.func.current_timestamp())
    due_date = db.Column(db.DateTime, nullable=True)
    
    notes = db.Column(db.Text, nullable=True)
    terms_conditions = db.Column(db.Text, nullable=True) 
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    
    # Relationships
    customer = db.relationship('Customer', backref=db.backref('quotations', lazy='dynamic'))
    lead = db.relationship('Lead', backref=db.backref('quotations', lazy='dynamic'))
    project = db.relationship('Project', backref=db.backref('quotations', lazy='dynamic'))
    organization = db.relationship('Organization', backref='quotations')
    creator = db.relationship('Employee', foreign_keys=[created_by], backref='quotations_created')
    items = db.relationship('QuotationItem', backref='quotation', cascade='all, delete-orphan')

    @property
    def status_display(self):
        return self.status.value if self.status else ""

    def __repr__(self):
        return f'<Quotation {self.quotation_number}>'

class QuotationItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quotation_id = db.Column(db.Integer, db.ForeignKey('quotation.id'), nullable=False, index=True)
    
    description = db.Column(db.String(255), nullable=False)
    width = db.Column(db.Float, default=0.0)
    height = db.Column(db.Float, default=0.0)
    quantity = db.Column(db.Float, default=1.0)
    
    gst_percentage = db.Column(db.Float, default=18.0)
    unit = db.Column(db.String(50), nullable=True)
    rate = db.Column(db.Float, default=0.0)
    
    amount = db.Column(db.Float, default=0.0) # subtotal for item
    total = db.Column(db.Float, default=0.0) # total including tax
    
    def __repr__(self):
        return f'<QuotationItem {self.description} for Quotation {self.quotation_id}>'
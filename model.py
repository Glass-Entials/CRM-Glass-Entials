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
    description = db.Column(db.String(255), nullable=False)
    due_date = db.Column(db.DateTime, nullable=True)
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    # Multi-tenant field
    organization_id = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=True, index=True)
    
    # Foreign Keys
    assigned_to = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False, index=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('lead.id'), nullable=True, index=True)
    
    # Relationships
    assignee = db.relationship('Employee', foreign_keys=[assigned_to], backref=db.backref('tasks_assigned', lazy='dynamic'))
    lead_record = db.relationship('Lead', foreign_keys=[lead_id], backref=db.backref('tasks_related', lazy='dynamic'))
    org = db.relationship('Organization', foreign_keys=[organization_id], backref=db.backref('tasks_org', lazy='dynamic'))

    def __repr__(self):
        return f'<Task {self.description}>'
class Project(db.model):
    id=db.Column(db.Integer, primary_key=True)
    Project_name=db.Column(db.String(100), nullable=False)
    description=db.Column(db.String(255), nullable=True)
    
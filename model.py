from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin

db = SQLAlchemy()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False, default='employee')
    password = db.Column(db.String(255), nullable=False)
    
    
    def __repr__(self):
        return f'<User {self.username}>'

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), nullable=False, index=True)
    phone_number = db.Column(db.String(20), nullable=False)
    address = db.Column(db.String(200), nullable=True)
    city = db.Column(db.String(50), nullable=True)
    company = db.Column(db.String(100), nullable=True)
    source = db.Column(db.String(50), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='New')
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    assigned_to = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=True)
    is_deleted = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f'<Customer {self.name}>'

class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, unique=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    position = db.Column(db.String(50), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())
    is_deleted = db.Column(db.Boolean, default=False)
    
    user=db.relationship('User', backref=db.backref('employee', uselist=False))
    
    customers_created = db.relationship('Customer', foreign_keys='Customer.created_by', backref='creator', lazy=True, cascade='save-update, merge')
    customers_updated = db.relationship('Customer', foreign_keys='Customer.updated_by', backref='updater', lazy=True, cascade='save-update, merge')
    assigned_customers = db.relationship('Customer', foreign_keys='Customer.assigned_to', backref='assignee', lazy=True, cascade='save-update, merge')

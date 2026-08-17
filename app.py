import os
from datetime import datetime
from flask import Flask, render_template, request, flash, redirect, url_for, send_file
from model import db, User, Customer, Employee, Lead, Project, Expense
from config import Config
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_talisman import Talisman
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import (
    LoginManager,
    login_user,
    logout_user,
    login_required,
    current_user,
)
from flask_migrate import Migrate

# Route Blueprints
from routes.auth import auth_bp
from routes.customers import customers_bp
from routes.leads import leads_bp
from routes.employees import employees_bp
from routes.projects import projects_bp
from routes.api import api_bp
from routes.tasks import tasks_bp
from routes.accounts import accounts
from routes.expenses import expenses_bp
from routes.quotations import quotations_bp
from routes.quotation_settings import quotation_settings_bp
from routes.products import products_bp
from routes.documents import documents_bp
from routes.contacts import contacts_bp
from routes.password_reset import password_reset_bp
from routes.google_auth import google_auth_bp
from routes.microsoft_auth import microsoft_auth_bp
from routes.payments import payments_bp
from routes.trash import trash_bp
from super_admin.routes import super_admin_bp
from routes.org import org_bp
from routes.call_logger import call_logger_bp
from routes.call_logger_api import call_logger_api_bp

from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__, template_folder="templates",static_folder="static")
app.config.from_object(Config)
app.config["TEMPLATES_AUTO_RELOAD"] = True  # Always reload templates on change


# Add ProxyFix for AWS ALB / Nginx
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Add CSRF Protection
csrf = CSRFProtect(app)

# Add Talisman for Security Headers
# Only enforce HTTPS in production
force_https_enabled = os.environ.get("FLASK_ENV", "development") == "production"

csp = {
    'default-src': ["'self'"],
    'script-src': ["'self'", "'unsafe-inline'", "'unsafe-eval'", "https://cdn.jsdelivr.net", "https://code.jquery.com"],
    'style-src': ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://fonts.googleapis.com"],
    'font-src': ["'self'", "https://fonts.gstatic.com", "https://cdn.jsdelivr.net"],
    'img-src': ["'self'", "data:", "blob:", "https://images.unsplash.com", "https://lh3.googleusercontent.com"],
    'media-src': ["'self'", "data:", "https://assets.mixkit.co"],
    # Explicitly allow WebSocket connections (ws: and wss:) for Socket.IO
    'connect-src': ["'self'", "ws:", "wss:", "https://accounts.google.com", "https://oauth2.googleapis.com", "https://login.microsoftonline.com", "https://graph.microsoft.com"],
    'frame-ancestors': ["'self'"]
}

Talisman(app, content_security_policy=csp, force_https=force_https_enabled, strict_transport_security=force_https_enabled, session_cookie_secure=force_https_enabled)

if force_https_enabled:
    app.config['PREFERRED_URL_SCHEME'] = 'https'

from utils.extensions import limiter, socketio

# Initialize Limiter
limiter.init_app(app)

# Initialize SocketIO with eventlet async mode.
# CRITICAL: Do NOT pass message_queue here unless Redis is configured.
# With message_queue=None (default), all sessions stay in-process which
# requires workers=1 in gunicorn.conf.py. If Redis is available, set
# SOCKETIO_MESSAGE_QUEUE env var and pass it here to enable multi-worker support.
_socketio_queue = os.environ.get("SOCKETIO_MESSAGE_QUEUE") or None
_async_mode = "threading" if __name__ == "__main__" else None

socketio.init_app(
    app,
    cors_allowed_origins="*",
    message_queue=_socketio_queue,
    logger=True,
    engineio_logger=True,
    async_mode=_async_mode
)

from flask_socketio import join_room
@socketio.on('join')
def on_join(data):
    if current_user.is_authenticated and current_user.employee:
        room = f"org_{current_user.organization_id}_user_{current_user.employee.id}"
        join_room(room)

@app.route("/health")
@csrf.exempt
@limiter.exempt
def health_check():
    return {"status": "healthy"}, 200

# Ensure asset directories exist
upload_root = app.config.get("UPLOAD_FOLDER")

if upload_root:
    try:
        os.makedirs(os.path.join(upload_root, "profile_pics"), exist_ok=True)
        os.makedirs(os.path.join(upload_root, "customer_docs"), exist_ok=True)
        os.makedirs(os.path.join(upload_root, "receipts"), exist_ok=True)
        os.makedirs(os.path.join(upload_root, "crm_docs"), exist_ok=True)
    except Exception as e:
        print("Upload folder creation failed:", e)


# Context Processor for Avatars
@app.context_processor
def utility_processor():
    def get_profile_pic(employee):
        if employee and employee.profile_pic:
            file_path = os.path.join(
                app.root_path, "static", "uploads", "profile_pics", employee.profile_pic
            )
            if os.path.exists(file_path):
                return url_for(
                    "static", filename="uploads/profile_pics/" + employee.profile_pic
                )
        return url_for("static", filename="img/default_avatar.png")

    def time_ago(dt):
        from utils.timezone import time_ago_ist
        return time_ago_ist(dt)

    def unread_notifications_count():
        if current_user.is_authenticated and current_user.employee:
            from model import Notification

            return Notification.query.filter_by(
                recipient_id=current_user.employee.id, is_read=False
            ).count()
        return 0

    from utils.tenant import get_user_orgs, get_active_org
    user_orgs = get_user_orgs()
    active_org = get_active_org()

    return dict(
        get_profile_pic=get_profile_pic,
        time_ago=time_ago,
        unread_notifications_count=unread_notifications_count,
        user_orgs=user_orgs,
        active_org=active_org,
    )


# ── IST timezone display filter ──────────────────────────────────────────────
from utils.timezone import ist_filter as _ist_filter
app.add_template_filter(_ist_filter, "ist")

@app.template_filter("nl2br")
def nl2br_filter(s):
    if not s:
        return ""
    import markupsafe

    # Escape user content first, then convert newlines to <br> and mark safe
    escaped = markupsafe.escape(s)
    return markupsafe.Markup(escaped.replace("\n", "<br>\n"))

@app.template_filter("format_tasks")
def format_tasks_filter(s):
    if not s:
        return ""
    import markupsafe
    import re

    s = str(s).strip()
    lines = []

    # Split by newlines first
    if '\n' in s:
        raw_lines = [l.strip() for l in s.split('\n') if l.strip()]
        for l in raw_lines:
            l = re.sub(r'^[\u2022\-\*]\s*', '', l)
            l = re.sub(r'^\d+\.\s*', '', l)
            lines.append(l)
    # Split by bullet characters
    elif re.search(r'[\u2022\-\*]\s', s):
        parts = re.split(r'[\u2022\-\*]\s', s)
        lines = [p.strip() for p in parts if p.strip()]
    # Split by numbered list
    elif re.search(r'\d+\.\s', s):
        parts = re.split(r'\d+\.\s', s)
        lines = [p.strip() for p in parts if p.strip()]
    # Fallback: split by period-space
    else:
        parts = re.split(r'\.\s+', s)
        lines = [p.strip() for p in parts if p.strip()]

    if not lines:
        return ""

    escaped_lines = [str(markupsafe.escape(l)) for l in lines]

    # 2 or fewer: show all
    if len(escaped_lines) <= 2:
        items = ''.join('<li>' + l + '</li>' for l in escaped_lines)
        return markupsafe.Markup('<ul class="saas-task-list">' + items + '</ul>')

    # Build list: first 2 visible, rest hidden with display:none
    items = ''
    for l in escaped_lines[:2]:
        items += '<li>' + l + '</li>'
    for l in escaped_lines[2:]:
        items += '<li class="saas-extra" style="display:none;">' + l + '</li>'

    return markupsafe.Markup(
        '<div class="saas-task-container">'
        '<ul class="saas-task-list">' + items + '</ul>'
        '<a href="javascript:void(0)" class="saas-show-more" onclick="saasTasks(this)">Show more</a>'
        '</div>'
    )


# Initialize Plugins
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth.login"
db.init_app(app)
Migrate(app, db)

# Register Blueprints
app.register_blueprint(auth_bp, url_prefix="/auth")
app.register_blueprint(customers_bp)
app.register_blueprint(leads_bp)
app.register_blueprint(employees_bp)
app.register_blueprint(projects_bp)
app.register_blueprint(api_bp)
app.register_blueprint(tasks_bp)
app.register_blueprint(accounts)
app.register_blueprint(expenses_bp)
app.register_blueprint(quotations_bp)
app.register_blueprint(quotation_settings_bp)
app.register_blueprint(products_bp)
app.register_blueprint(documents_bp)
app.register_blueprint(contacts_bp)
app.register_blueprint(password_reset_bp)
app.register_blueprint(google_auth_bp)
app.register_blueprint(microsoft_auth_bp)
app.register_blueprint(payments_bp)
app.register_blueprint(trash_bp)
app.register_blueprint(super_admin_bp)
app.register_blueprint(org_bp)
app.register_blueprint(call_logger_bp)
app.register_blueprint(call_logger_api_bp)
# Exempt the Android device API from CSRF (uses Bearer token auth instead)
csrf.exempt(call_logger_api_bp)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.before_request
def enforce_password_change():
    if current_user.is_authenticated:
        if getattr(current_user, 'must_change_password', False):
            if request.endpoint not in ('auth.change_password', 'auth.logout', 'static'):
                return redirect(url_for('auth.change_password'))


@app.before_request
def enforce_org_active():
    """Block access to CRM for users whose organization is suspended."""
    exempt_endpoints = {
        'static', 'health_check', 'home', 'about', 'pricing_page',
        'auth.login', 'auth.register', 'auth.logout', 'auth.verify_otp',
        'auth.resend_otp', 'auth.forgot_password', 'auth.reset_password',
        'password_reset.forgot_password', 'password_reset.reset_password',
        'google_auth.google_login', 'google_auth.google_callback',
        'microsoft_auth.microsoft_login', 'microsoft_auth.microsoft_callback',
        'org.switch_organization', 'org.join_organization', 'org.create_organization',
        'org.organization_settings',
        'call_logger_api.receive_call_log', 'call_logger_api.ping',
    }
    if request.endpoint in exempt_endpoints:
        return None
    if request.endpoint and request.endpoint.startswith('super_admin.'):
        return None
    from utils.tenant import suspended_org_guard
    result = suspended_org_guard()
    if result:
        return result


@app.before_request
def enforce_storage_limit():
    """Globally intercept file uploads and enforce storage limits."""
    if request.method in ['POST', 'PUT', 'PATCH'] and request.files:
        from utils.tenant import get_active_org
        from services.org_limits import check_storage_limit, add_storage_usage
        org = get_active_org()
        if org:
            total_size = 0
            # Get size of all uploaded files in this request
            for key, file_storage in request.files.items():
                if file_storage.filename:
                    file_storage.seek(0, 2)  # Seek to end
                    total_size += file_storage.tell()
                    file_storage.seek(0)     # Reset to beginning

            if total_size > 0:
                allowed, reason = check_storage_limit(org, total_size)
                if not allowed:
                    from flask import flash, redirect
                    flash(reason, "error")
                    return redirect(request.url)
                
                # If we get here, upload is allowed.
                # Strictly speaking, if the route fails we might over-count, 
                # but for simplicity we increment here to enforce limits.
                add_storage_usage(org, total_size)


@app.route('/switch-org', methods=['POST'])
@login_required
def switch_organization():
    """Securely switch the user's active organization."""
    from utils.tenant import switch_org
    from flask_wtf.csrf import validate_csrf
    try:
        validate_csrf(request.form.get('csrf_token'))
    except Exception:
        return redirect(url_for('home_page'))
    org_id = request.form.get('org_id', type=int)
    if org_id and switch_org(org_id):
        flash('Switched organization successfully.', 'success')
    else:
        flash('Unable to switch organization.', 'error')
    next_url = request.form.get('next') or url_for('home_page')
    return redirect(next_url)

# --- Error Handling ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template("errors/404.html"), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template("errors/403.html"), 403


@app.errorhandler(500)
def internal_server_error(e):
    return render_template("errors/500.html"), 500


# --- Core Routes ---
@app.route("/")
def home():
    return render_template("home/index.html")


@app.route("/about")
def about():
    return render_template("home/about.html")


@app.route("/pricing")
def pricing_page():
    return render_template("home/pricing.html")


@app.route("/home")
@login_required
def home_page():
    from model import ActivityLog, Project, Task, TaskStatus, ExpenseStatus, DailyTask, Contact, Payment, PaymentStatus
    from datetime import date

    org_id = current_user.organization_id

    # Get total counts/stats
    total_customers_all = Customer.query.filter_by(
        organization_id=org_id, is_deleted=False
    ).count()
    # Lead Filtering Logic
    from model import LeadStatus

    lead_filter = request.args.get("lead_status", "Total")
    lead_query = Lead.query.filter_by(organization_id=org_id, is_deleted=False)

    if lead_filter != "Total":
        status_map = {e.value: e for e in LeadStatus}
        if lead_filter in status_map:
            lead_query = lead_query.filter(Lead.status == status_map[lead_filter])

    total_leads = lead_query.count()

    # Task Filtering Logic
    from model import TaskStatus, Task

    task_filter = request.args.get("task_status", "Pending")
    task_query = Task.query.filter_by(organization_id=org_id)

    if task_filter == "Pending":
        task_query = task_query.filter(Task.status == TaskStatus.PENDING)
    elif task_filter != "Total":
        status_map = {e.value: e for e in TaskStatus}
        if task_filter in status_map:
            task_query = task_query.filter(Task.status == status_map[task_filter])

    pending_tasks_count = task_query.count()

    # Customer Filtering Logic
    from model import ProjectStatus

    project_filter = request.args.get("project_status", "Active")
    project_query = Project.query.filter_by(organization_id=org_id, is_deleted=False)

    if project_filter == "Active":
        project_query = project_query.filter(Project.status != ProjectStatus.COMPLETED)
    elif project_filter != "Total":
        status_map = {e.value: e for e in ProjectStatus}
        if project_filter in status_map:
            project_query = project_query.filter(
                Project.status == status_map[project_filter]
            )

    active_projects_count = project_query.count()

    # Customer Filtering Logic
    from model import CustomerStatus

    customer_filter = request.args.get("customer_status", "Total")
    customer_query = Customer.query.filter_by(organization_id=org_id, is_deleted=False)

    if customer_filter != "Total":
        status_map = {e.value: e for e in CustomerStatus}
        if customer_filter in status_map:
            customer_query = customer_query.filter(
                Customer.status == status_map[customer_filter]
            )

    total_customers = customer_query.count()

    # Expense Filtering Logic
    expense_filter = request.args.get("expense_status", "Total")
    expense_query = db.session.query(db.func.sum(Expense.amount)).filter(
        Expense.organization_id == org_id, Expense.is_deleted == False
    )

    if expense_filter == "Approved":
        expense_query = expense_query.filter(Expense.status == ExpenseStatus.APPROVED)
    elif expense_filter == "Rejected":
        expense_query = expense_query.filter(Expense.status == ExpenseStatus.REJECTED)
    elif expense_filter == "Paid":
        expense_query = expense_query.filter(Expense.status == ExpenseStatus.PAID)

    total_Expenses = expense_query.scalar() or 0

    # Restoring Dashboard Content Lists
    all_customers = (
        Customer.query.filter_by(organization_id=org_id, is_deleted=False)
        .order_by(Customer.created_at.desc())
        .all()
    )
    recent_leads = (
        Lead.query.filter_by(organization_id=org_id, is_deleted=False)
        .order_by(Lead.created_at.desc())
        .limit(5)
        .all()
    )
    recent_projects = (
        Project.query.filter_by(organization_id=org_id, is_deleted=False)
        .order_by(Project.created_at.desc())
        .limit(5)
        .all()
    )

    # Activity logs
    recent_activity = (
        ActivityLog.query.filter_by(organization_id=org_id)
        .order_by(ActivityLog.created_at.desc())
        .limit(10)
        .all()
    )

    # Daily Task Stats
    today = date.today()
    emp = current_user.employee
    emp_id = emp.id if emp else None

    # Filter work logs based on role
    if current_user.role.value in ["admin", "manager"]:
        todays_work_logs = DailyTask.query.filter_by(
            organization_id=org_id, date=today
        ).all()
        recent_work_logs = (
            DailyTask.query.filter_by(organization_id=org_id)
            .order_by(DailyTask.created_at.desc())
            .limit(5)
            .all()
        )
    else:
        todays_work_logs = (
            DailyTask.query.filter_by(
                organization_id=org_id, date=today, employee_id=emp_id
            ).all()
            if emp_id
            else []
        )
        recent_work_logs = (
            DailyTask.query.filter_by(organization_id=org_id, employee_id=emp_id)
            .order_by(DailyTask.created_at.desc())
            .limit(5)
            .all()
            if emp_id
            else []
        )

    todays_hours = sum(log.hours_spent for log in todays_work_logs if log.hours_spent)

    # --- My Workspace Metrics ---
    my_workspace = {
        "tasks_assigned": 0,
        "pending_tasks": 0,
        "in_progress_tasks": 0,
        "completed_today": 0,
        "overdue_tasks": 0,
        "todays_followups": 0,
        "leads_assigned": 0,
        "projects_assigned": 0,
    }

    if emp_id:
        from model import LeadFollowUp
        my_workspace["tasks_assigned"] = Task.query.filter_by(organization_id=org_id, assigned_to=emp_id).count()
        my_workspace["pending_tasks"] = Task.query.filter_by(organization_id=org_id, assigned_to=emp_id, status=TaskStatus.PENDING).count()
        my_workspace["in_progress_tasks"] = Task.query.filter_by(organization_id=org_id, assigned_to=emp_id, status=TaskStatus.IN_PROGRESS).count()
        
        my_workspace["completed_today"] = Task.query.filter(
            Task.organization_id == org_id,
            Task.assigned_to == emp_id,
            Task.status == TaskStatus.COMPLETED,
            db.func.date(Task.updated_at) == today
        ).count()
        
        my_workspace["overdue_tasks"] = Task.query.filter(
            Task.organization_id == org_id,
            Task.assigned_to == emp_id,
            Task.status.notin_([TaskStatus.COMPLETED, TaskStatus.CANCELLED]),
            db.func.date(Task.due_date) < today
        ).count()
        
        my_workspace["todays_followups"] = LeadFollowUp.query.join(Lead).filter(
            LeadFollowUp.organization_id == org_id,
            LeadFollowUp.is_done == False,
            db.func.date(LeadFollowUp.follow_up_date) <= today,
            Lead.is_deleted == False,
            Lead.assigned_to == emp_id
        ).count()
        
        my_workspace["leads_assigned"] = Lead.query.filter_by(organization_id=org_id, is_deleted=False, assigned_to=emp_id).count()
        my_workspace["projects_assigned"] = Project.query.filter_by(organization_id=org_id, is_deleted=False, assigned_to=emp_id).count()

    # --- Contact Metrics ---
    total_contacts = Contact.query.filter_by(organization_id=org_id, is_deleted=False).count()

    # --- Payment Metrics ---
    payment_filter = request.args.get("payment_status", "Total")
    payment_query = Payment.query.filter_by(organization_id=org_id, is_deleted=False)
    
    if payment_filter == "Due":
        payment_query = payment_query.filter(Payment.status == PaymentStatus.PENDING)
    elif payment_filter == "Received":
        payment_query = payment_query.filter(Payment.status == PaymentStatus.RECEIVED)
        
    # We will display the total amount for the selected filter
    total_payments_amount = db.session.query(db.func.sum(Payment.amount)).filter(
        Payment.organization_id == org_id, 
        Payment.is_deleted == False
    )
    if payment_filter == "Due":
        total_payments_amount = total_payments_amount.filter(Payment.status == PaymentStatus.PENDING)
    elif payment_filter == "Received":
        total_payments_amount = total_payments_amount.filter(Payment.status == PaymentStatus.RECEIVED)
        
    total_payments = total_payments_amount.scalar() or 0



    return render_template(
        "home/home.html",
        total_customers=total_customers,
        total_leads=total_leads,
        active_projects_count=active_projects_count,
        pending_tasks_count=pending_tasks_count,
        recent_activity=recent_activity,
        total_Expenses=total_Expenses,
        expense_filter=expense_filter,
        customer_filter=customer_filter,
        project_filter=project_filter,
        lead_filter=lead_filter,
        task_filter=task_filter,
        all_customers=all_customers,
        recent_leads=recent_leads,
        recent_projects=recent_projects,
        todays_hours=todays_hours,
        recent_work_logs=recent_work_logs,
        my_workspace=my_workspace,
        total_contacts=total_contacts,
        payment_filter=payment_filter,
        total_payments=total_payments,
    )


@app.route("/activity-logs")
@login_required
def activity_logs():
    from model import ActivityLog
    
    org_id = current_user.organization_id
    page = request.args.get('page', 1, type=int)
    per_page = 50
    
    pagination = ActivityLog.query.filter_by(organization_id=org_id)\
        .order_by(ActivityLog.created_at.desc())\
        .paginate(page=page, per_page=per_page, error_out=False)
        
    return render_template("home/activity_logs.html", pagination=pagination)



# Bulk Upload Templates (Shared Utilities)
@app.route("/download-template")
@login_required
def download_template():
    return send_file(
        os.path.join(app.root_path, "static", "templates", "bulk_upload_template.csv"),
        as_attachment=True,
        download_name="bulk_upload_template.csv",
    )


@app.route("/download-lead-template")
@login_required
def download_lead_template():
    return send_file(
        os.path.join(app.root_path, "static", "templates", "bulk_upload_lead_template.csv"),
        as_attachment=True,
        download_name="bulk_upload_lead_template.csv",
    )


# Backwards compatibility redirects
@app.route("/login", methods=["GET", "POST"])
def login():
    return redirect(url_for("auth.login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    return redirect(url_for("auth.register"))

if __name__ == "__main__":
    # Use environment variable for debug mode, default to False for production safety
    debug_mode = os.environ.get("FLASK_DEBUG", "False").lower() == "true"
    app.run(debug=debug_mode, port=5000)

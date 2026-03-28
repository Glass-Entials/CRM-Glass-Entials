import os
from flask import Flask, render_template, request, flash, redirect, url_for, send_file
from model import db, User, Customer, Employee, Lead
from config import Config
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate

# Route Blueprints
from routes.auth import auth_bp
from routes.customers import customers_bp
from routes.leads import leads_bp
from routes.employees import employees_bp

app = Flask(__name__, template_folder='Templates')
app.config.from_object(Config)

# Ensure upload directory exists
upload_path = app.config.get('UPLOAD_FOLDER')
if upload_path and not os.path.exists(upload_path):
    os.makedirs(upload_path, exist_ok=True)

# Context Processor for Avatars
@app.context_processor
def utility_processor():
    def get_profile_pic(employee):
        if employee and employee.profile_pic:
            file_path = os.path.join(app.root_path, 'static', 'uploads', 'profile_pics', employee.profile_pic)
            if os.path.exists(file_path):
                return url_for('static', filename='uploads/profile_pics/' + employee.profile_pic)
        return url_for('static', filename='img/default_avatar.jpg')
    return dict(get_profile_pic=get_profile_pic)

# Initialize Plugins
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
Migrate(app, db)
db.init_app(app)

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(customers_bp)
app.register_blueprint(leads_bp)
app.register_blueprint(employees_bp)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Error Handling ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors/500.html'), 500

# --- Core Routes ---
@app.route('/')
def home():
    return render_template('Home/index.html')

@app.route('/about')
def about():
    return render_template('Home/about.html')

@app.route('/home')
@login_required
def home_page():
    org_id = current_user.organization_id
    all_customers = Customer.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Customer.created_at.desc()).all()
    recent_leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).order_by(Lead.created_at.desc()).limit(5).all()
    total_leads = Lead.query.filter_by(organization_id=org_id, is_deleted=False).count()
    return render_template('Home/home.html', all_customers=all_customers, recent_leads=recent_leads, total_leads=total_leads)

# Bulk Upload Templates (Shared Utilities)
@app.route('/download-template')
@login_required
def download_template():
    return send_file('static/templates/bulk_upload_template.csv', as_attachment=True, download_name='bulk_upload_template.csv')

@app.route('/download-lead-template')
@login_required
def download_lead_template():
    return send_file('static/templates/bulk_upload_lead_template.csv', as_attachment=True, download_name='bulk_upload_lead_template.csv')

# Backwards compatibility redirects
@app.route('/login', methods=['GET', 'POST'])
def login(): return redirect(url_for('auth.login'))

@app.route('/register', methods=['GET', 'POST'])
def register(): return redirect(url_for('auth.register'))

@app.route('/logout')
def logout(): return redirect(url_for('auth.logout'))

if __name__ == '__main__':
    app.run(debug=True)
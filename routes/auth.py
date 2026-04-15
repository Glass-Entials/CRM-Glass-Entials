import os
from flask import Blueprint, render_template, request, flash, redirect, url_for, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_login import login_user, logout_user, login_required, current_user
from model import db, User, Employee, Organization, UserRole, Task, Lead, LeadActivity, Customer

auth_bp = Blueprint('auth', __name__)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'gif'}

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Login successful!', 'loginsuccess')
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('home_page'))
        else:
            flash('Invalid email or password.', 'loginerror')
            return redirect(url_for('auth.login'))
    return render_template('login/login.html')

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.login'))

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        phone_number = request.form.get('phone_number')
        role_pref = request.form.get('role')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        
        org_option = request.form.get('org_option')
        org_name = request.form.get('org_name')
        org_code = request.form.get('org_code')

        if password != confirm_password:
            flash('Passwords do not match.', 'registererror')
            return redirect(url_for('auth.register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered.', 'registererror')
            return redirect(url_for('auth.register'))

        org = None
        if org_option == 'create':
            import random, string
            def generate_org_code(length=8):
                while True:
                    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))
                    if not Organization.query.filter_by(unique_code=code).first():
                        return code
            org = Organization(name=org_name, unique_code=generate_org_code())
            db.session.add(org)
            db.session.flush()
        else:
            org = Organization.query.filter_by(unique_code=org_code).first()
            if not org:
                flash('Invalid Organization Code.', 'registererror')
                return redirect(url_for('auth.register'))

        # DUPLICATE CHECKS
        existing_user = User.query.filter(
            (User.email == email) | 
            (User.phone_number == phone_number) | 
            (User.username == username)
        ).first()

        if existing_user:
            if existing_user.email == email:
                flash('Email already registered.', 'registererror')
            elif existing_user.phone_number == phone_number:
                flash('Phone number already registered.', 'registererror')
            else:
                flash('Username already taken.', 'registererror')
            return redirect(url_for('auth.register'))

        role_pref_mapped = UserRole.MANAGER if role_pref == 'manager' else UserRole.EMPLOYEE
        
        try:
            hashed_pw = generate_password_hash(password)
            new_user = User(username=username, email=email, phone_number=phone_number, 
                            password=hashed_pw, role=UserRole.ADMIN if org_option == 'create' else role_pref_mapped,
                            organization_id=org.id)
            
            db.session.add(new_user)
            db.session.flush()

            employee = Employee(name=username, email=email, phone_number=phone_number, 
                                user_id=new_user.id, organization_id=org.id)
            db.session.add(employee)
            db.session.commit()

            flash('Registration successful!', 'registersuccess')
            return redirect(url_for('auth.login'))
        except Exception as e:
            db.session.rollback()
            flash(f'An error occurred: {str(e)}', 'registererror')
            return redirect(url_for('auth.register'))

    return render_template('login/register.html')

@auth_bp.route('/user-profile', methods=['GET', 'POST'])
@login_required
def user_profile():
    if not current_user.employee:
        flash('Employee profile not found.', 'profileerror')
        return redirect(url_for('home_page'))

    employee = current_user.employee
    org_id = current_user.organization_id

    if request.method == 'POST':
        # Only update text fields if they were submitted in the form (Edit Profile Modal)
        if 'name' in request.form:
            employee.name = request.form.get('name')
            employee.email = request.form.get('email')
            employee.phone_number = request.form.get('phone_number')
            employee.position = request.form.get('position')
            
            current_user.username = request.form.get('username') or employee.name
            current_user.email = employee.email
            current_user.phone_number = employee.phone_number
        
        # Always process profile picture if it's sent
        if 'profile_pic' in request.files:
            file = request.files['profile_pic']
            if file and file.filename != '' and allowed_file(file.filename):
                if employee.profile_pic:
                    old_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'profile_pics', employee.profile_pic)
                    if os.path.exists(old_path):
                        try: os.remove(old_path)
                        except Exception: pass
                
                ext = file.filename.rsplit('.', 1)[1].lower()
                filename = secure_filename(f"user_{current_user.id}.{ext}")
                # Save to specific subfolder
                save_dir = os.path.join(current_app.config['UPLOAD_FOLDER'], 'profile_pics')
                os.makedirs(save_dir, exist_ok=True)
                file.save(os.path.join(save_dir, filename))
                employee.profile_pic = filename
        
        try:
            db.session.commit()
            flash('Profile updated successfully!', 'profilesuccess')
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'profileerror')
        
        return redirect(url_for('auth.user_profile'))

    my_tasks = Task.query.filter_by(assigned_to=employee.id, organization_id=org_id).order_by(Task.created_at.desc()).all()
    my_leads = Lead.query.filter_by(assigned_to=employee.id, organization_id=org_id, is_deleted=False).order_by(Lead.created_at.desc()).all()
    my_customers = Customer.query.filter_by(assigned_to=employee.id, organization_id=org_id, is_deleted=False).order_by(Customer.created_at.desc()).all()
    my_activities = LeadActivity.query.filter_by(created_by=employee.id, organization_id=org_id).order_by(LeadActivity.created_at.desc()).all()

    return render_template('login/user_profile.html', 
                           employee=employee, my_tasks=my_tasks, 
                           my_leads=my_leads, my_customers=my_customers, 
                           my_activities=my_activities)

@auth_bp.route('/remove-profile-pic')
@login_required
def remove_profile_pic():
    employee = current_user.employee
    if employee and employee.profile_pic:
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], 'profile_pics', employee.profile_pic)
        if os.path.exists(file_path):
            try: os.remove(file_path)
            except Exception: pass
        employee.profile_pic = None
        db.session.commit()
        flash('Profile picture removed!', 'profilesuccess')
    return redirect(url_for('auth.user_profile'))
@auth_bp.route('/notifications')
@login_required
def notifications():
    from model import Notification
    if not current_user.employee:
        return redirect(url_for('home_page'))
    
    # Get all notifications for current user
    user_notifications = Notification.query.filter_by(
        recipient_id=current_user.employee.id,
        organization_id=current_user.organization_id
    ).order_by(Notification.created_at.desc()).all()
    
    return render_template('login/notifications.html', notifications=user_notifications)

@auth_bp.route('/notifications/mark-read/<int:id>')
@login_required
def mark_notification_read(id):
    from model import Notification
    notification = Notification.query.get_or_404(id)
    
    # Security check
    if notification.recipient_id != current_user.employee.id:
        flash('Unauthorized action.', 'error')
        return redirect(url_for('auth.notifications'))
    
    notification.is_read = True
    db.session.commit()
    
    if notification.link:
        return redirect(notification.link)
    
    return redirect(url_for('auth.notifications'))

@auth_bp.route('/notifications/mark-all-read')
@login_required
def mark_all_notifications_read():
    from model import Notification
    Notification.query.filter_by(
        recipient_id=current_user.employee.id, 
        is_read=False,
        organization_id=current_user.organization_id
    ).update({Notification.is_read: True})
    db.session.commit()
    flash('All notifications marked as read.', 'success')
    return redirect(url_for('auth.notifications'))

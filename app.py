import re
import csv
import pandas as pd
from io import BytesIO, StringIO
from fpdf import FPDF
from flask import Flask, render_template, request, flash, redirect, url_for, Response, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from model import db, User, Customer, Employee
from config import Config
from flask_login import logout_user, login_required, LoginManager, UserMixin, login_user, current_user
from flask_migrate import Migrate

app = Flask(__name__, template_folder='Templates')
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
app.config.from_object(Config)
ming = Migrate(app, db)

db.init_app(app)
app.secret_key = 'glassentials_secure_secret_key'

@app.route('/')
def home():
    return render_template('Home/index.html')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- Suggestion 2: Error Handling ---
@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors/500.html'), 500

@app.route('/about')
def about():
    return render_template('Home/about.html')

@app.route('/home')
@login_required
def home_page():
    all_customers = Customer.query.filter_by(is_deleted=False).order_by(Customer.created_at.desc()).all()
    return render_template('Home/home.html', all_customers=all_customers)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/login')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        #check if user exists
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            flash('Login successful!','loginsuccess')
            next_page = request.args.get('next')
            return redirect(next_page if next_page else url_for('home_page'))
        else:
            flash('Invalid email or password.','loginerror')
            return redirect('/login')
    return render_template('login/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        phone_number = request.form.get('phone_number')
        role = request.form.get('role')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')

        # 1. Empty Field Check
        if not all([username, email, phone_number, role, password, confirm_password]):
            flash('All fields are required.', 'registererror')
            return redirect(url_for('register'))

        # 2. Password Match Check
        if password != confirm_password:
            flash('Passwords do not match.', 'registererror')
            return redirect(url_for('register'))

        # 3. Email Validation
        email_pattern = r'^[\w\.-]+@[\w\.-]+\.\w+$'
        if not re.match(email_pattern, email):
            flash('Invalid email format.', 'registererror')
            return redirect(url_for('register'))

        # 4. Phone Validation
        if not phone_number.isdigit() or len(phone_number) != 10:
            flash('Invalid phone number. Enter 10 digits.', 'registererror')
            return redirect(url_for('register'))
        
        # 5. Password Strength Check
        if len(password) < 8 or len(password) > 12:
            flash('Password must be 8-12 characters long.', 'registererror')
            return redirect(url_for('register'))

        if not re.search(r'[A-Z]', password):
            flash('Password must contain at least 1 uppercase letter.', 'registererror')
            return redirect(url_for('register'))

        if not re.search(r'[0-9]', password):
            flash('Password must contain at least 1 number.', 'registererror')
            return redirect(url_for('register'))

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            flash('Password must contain at least 1 special character.', 'registererror')
            return redirect(url_for('register'))

        # 6. Duplicate Check (Optimized)
        existing_user = User.query.filter(
            (User.username == username) |
            (User.email == email) |
            (User.phone_number == phone_number)
        ).first()

        if existing_user:
            flash('User already exists with same username/email/phone.', 'registererror')
            return redirect(url_for('register'))

        # 7. Hash Password
        hashed_password = generate_password_hash(password)

        # 8. Create User
        new_user = User(
            username=username,
            email=email,
            phone_number=phone_number,
            role=role,
            password=hashed_password
        )
        db.session.add(new_user)
        db.session.flush() # Ensure new_user.id is available

        # 9. Create Employee record for the user
        new_employee = Employee(
            user_id=new_user.id,
            name=username,
            email=email,
            phone_number=phone_number,
            position=role
        )
        db.session.add(new_employee)
        db.session.commit()
        flash('Registration successful! Please log in.', 'registersuccess')
        return redirect(url_for('login'))

    return render_template('login/register.html')
@app.route('/customers')
@login_required
def customers():
    all_customers = Customer.query.filter_by(is_deleted=False).order_by(Customer.created_at.desc()).all()
    return render_template('customer/customer.html', customers=all_customers)

@app.route('/export-customers/<string:format>')
@login_required
def export_customer(format):
    customers = Customer.query.filter_by(is_deleted=False).order_by(Customer.created_at.desc()).all()
    
    data = []
    for c in customers:
        data.append({
            'id': str(c.id),
            'name': str(c.name or ''),
            'email': str(c.email or ''),
            'phone': str(c.phone_number or ''),
            'address': str(c.address or '—'),
            'city': str(c.city or '—'),
            'company': str(c.company or '—'),
            'source': str(c.source or '—'),
            'status': str(c.status or 'New'),
            'created_date': c.created_at.strftime('%Y-%m-%d') if c.created_at else '—',
            'updated_date': c.updated_at.strftime('%Y-%m-%d') if c.updated_at else '—',
            'assigned_to': c.assignee.username if c.assignee else 'Unassigned',
            'created_by': c.creator.username if c.creator else 'Unknown',
            'updated_by': c.updater.username if c.updater else 'Unknown'
        })
    
    headers = ['ID', 'Name', 'Email', 'Phone','Address' ,'City', 'Company', 'Source','Status', 'Created_Date', 'Updated_Date','Assigned_To', 'Created_By', 'Updated_By']
    
    if format == 'csv':
        si = StringIO()
        cw = csv.writer(si)
        cw.writerow(headers)
        for row in data:
            cw.writerow([row[h.lower()] for h in headers])
        return Response(
            si.getvalue(),
            mimetype='text/csv',
            headers={"Content-Disposition": "attachment;filename=customers.csv"}
        )
    
    elif format == 'excel':
        df = pd.DataFrame(data)
        output = BytesIO()

        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Customers')
            workbook = writer.book
            worksheet = writer.sheets['Customers']

            gold_color = '#D4AF37'
            navy_color = '#0D1B2A'
            
            header_fmt = workbook.add_format({
                'bold': True,
                'bg_color': gold_color,
                'font_color': '#FFFFFF',
                'border': 1,
                'align': 'center',
                'valign': 'middle'
            })
            
            cell_fmt = workbook.add_format({
                'border': 1,
                'valign': 'middle',
                'font_name': 'Arial',
                'font_size': 10
            })
            
            alt_row_fmt = workbook.add_format({
                'border': 1,
                'bg_color': '#FBFAFA', 
                'valign': 'middle',
                'font_name': 'Arial',
                'font_size': 10
            })

            for col_num, value in enumerate(headers):
                worksheet.write(0, col_num, value, header_fmt)

            col_settings = [
                (0, 8, 'center'),   # ID
                (1, 20, 'left'),    # Name
                (2, 25, 'left'),    # Email
                (3, 15, 'center'),  # Phone
                (4, 30, 'left'),    # Address
                (5, 15, 'left'),    # City
                (6, 20, 'left'),    # Company
                (7, 12, 'center'),  # Source
                (8, 12, 'center'),  # Status
                (9, 15, 'center'),  # Created Date
                (10, 15, 'center'), # Updated Date
                (11, 15, 'left'),   # Assigned To
                (12, 12, 'left'),   # Created By
                (13, 12, 'left'),   # Updated By
            ]

            for col_idx, width, align in col_settings:
                fmt = workbook.add_format({'border': 1, 'align': align, 'valign': 'middle'})
                worksheet.set_column(col_idx, col_idx, width, fmt)

            worksheet.freeze_panes(1, 0) 
            worksheet.autofilter(0, 0, len(df), len(headers) - 1)
            
            for row_idx in range(1, len(df) + 1):
                if row_idx % 2 == 0:
                    for col_idx in range(len(headers)):
                        align = next(s[2] for s in col_settings if s[0] == col_idx)
                        row_fmt = workbook.add_format({
                            'bg_color': '#F8F9FA', 
                            'border': 1, 
                            'align': align, 
                            'valign': 'middle'
                        })
                        val = df.iloc[row_idx-1, col_idx]
                        worksheet.write(row_idx, col_idx, val, row_fmt)

        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name="customers_report.xlsx",
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    
    elif format == 'pdf':
        from datetime import datetime
        class PDF(FPDF):
            def footer(self):
                self.set_y(-15)
                self.set_font('helvetica', 'I', 8)
                self.set_text_color(128)
                self.cell(0, 10, f'Page {self.page_no()}', 0, 0, 'C')

        pdf = PDF(orientation='L', unit='mm', format='A4')
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        pdf.set_font("helvetica", 'B', 16)
        pdf.set_text_color(13, 27, 42) # Navy
        pdf.cell(0, 15, "Customer Management Report", 0, 1, 'L')
        pdf.set_font("helvetica", size=9)
        pdf.set_text_color(100)
        current_date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pdf.cell(0, 5, f"Date Generated: {current_date_str}", 0, 1, 'L')
        pdf.ln(10)
        widths = [8, 25, 30, 18, 35, 18, 25, 15, 15, 18, 18, 18, 18, 18]
        
        pdf.set_font("helvetica", 'B', 7)
        pdf.set_fill_color(212, 175, 55) 
        pdf.set_text_color(255)
        h = 8
        for i, header in enumerate(headers):
            pdf.cell(widths[i], h, header, border=1, fill=True, align='C')
        pdf.ln()

        pdf.set_font("helvetica", size=7)
        pdf.set_text_color(0)
        
        for idx, row in enumerate(data):
            if idx % 2 == 0:
                pdf.set_fill_color(248, 249, 250)
            else:
                pdf.set_fill_color(255, 255, 255)
            
            for i, header in enumerate(headers):
                val = str(row.get(header.lower(), ''))
                pdf.cell(widths[i], h, val[:25], border=1, fill=True, align='L')
            pdf.ln()
            
        pdf_output = pdf.output()
        buffer = BytesIO(pdf_output)
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name="customers_report.pdf",
            mimetype='application/pdf'
        )
    
    flash('Invalid format or export error.', 'customererror')
    return redirect(url_for('customers'))

@app.route('/add-customer', methods=['GET', 'POST'])
@login_required
def add_customer():
    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        email        = request.form.get('email', '').strip()
        phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))  # strip non-digits
        address      = request.form.get('address', '').strip()
        city         = request.form.get('city', '').strip()
        company      = request.form.get('company', '').strip()
        source       = request.form.get('source', '').strip()
        status       = request.form.get('status', 'New')
        notes        = request.form.get('notes', '').strip()
        assigned_to_id = request.form.get('assigned_to')
        if not assigned_to_id or assigned_to_id == 'unassigned':
            assigned_to_id = None
        else:
            assigned_to_id = int(assigned_to_id)

        # Basic validation
        if not all([name, email, phone_number]):
            flash('Name, Email, and Phone Number are required.', 'customererror')
            return redirect(url_for('add_customer'))

        if len(phone_number) != 10:
            flash('Phone number must be exactly 10 digits (digits only).', 'customererror')
            return redirect(url_for('add_customer'))

        # Check duplicate email
        if Customer.query.filter_by(email=email, is_deleted=False).first():
            flash('A customer with this email already exists.', 'customererror')
            return redirect(url_for('add_customer'))

        new_customer = Customer(
            name=name,
            email=email,
            phone_number=phone_number,
            address=address,
            city=city,
            company=company,
            source=source,
            status=status,
            notes=notes,
            created_by=current_user.employee.id,
            assigned_to=assigned_to_id
        )
        try:
            db.session.add(new_customer)
            db.session.commit()
            flash('Customer added successfully!', 'customersuccess')
            return redirect(url_for('customers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error saving customer: {str(e)}', 'customererror')
            return redirect(url_for('add_customer'))

    employees = Employee.query.filter_by(is_deleted=False).all()
    return render_template('customer/addcustomer.html', employees=employees)
@app.route('/edit-customer/<int:customer_id>', methods=['GET', 'POST'])
@login_required
def edit_customer(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        email        = request.form.get('email', '').strip()
        phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))  # strip non-digits
        address      = request.form.get('address', '').strip()
        city         = request.form.get('city', '').strip()
        company      = request.form.get('company', '').strip()
        source       = request.form.get('source', '').strip()
        status       = request.form.get('status', 'New')
        notes        = request.form.get('notes', '').strip()
        assigned_to_id = request.form.get('assigned_to')
        if not assigned_to_id or assigned_to_id == 'unassigned':
            assigned_to_id = None
        else:
            assigned_to_id = int(assigned_to_id)

        # Validation
        if not all([name, email, phone_number]):
            flash('Name, Email, and Phone Number are required.', 'customererror')
            return redirect(url_for('edit_customer', customer_id=customer_id))

        if len(phone_number) != 10:
            flash('Phone number must be exactly 10 digits.', 'customererror')
            return redirect(url_for('edit_customer', customer_id=customer_id))

        # Check for duplicate email excluding current customer
        existing = Customer.query.filter(Customer.email == email, Customer.id != customer_id, Customer.is_deleted == False).first()
        if existing:
            flash('Another customer with this email already exists.', 'customererror')
            return redirect(url_for('edit_customer', customer_id=customer_id))

        customer.name = name
        customer.email = email
        customer.phone_number = phone_number
        customer.address = address
        customer.city = city
        customer.company = company
        customer.source = source
        customer.status = status
        customer.notes = notes
        customer.assigned_to = assigned_to_id
        customer.updated_by = current_user.employee.id

        try:
            db.session.commit()
            flash('Customer updated successfully!', 'customersuccess')
            return redirect(url_for('customers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating customer: {str(e)}', 'customererror')
            return redirect(url_for('edit_customer', customer_id=customer_id))

    employees = Employee.query.filter_by(is_deleted=False).all()
    return render_template('customer/editcustomer.html', customer=customer, customer_id=customer_id, employees=employees)

@app.route('/delete-customer/<int:customer_id>', methods=['POST'])
@login_required
def delete_customer(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    customer.is_deleted = True
    db.session.commit()
    flash('Customer deleted successfully.', 'customersuccess')
    return redirect(url_for('customers'))
@app.route('/bulk-upload', methods=['GET', 'POST'])
@login_required
def bulk_upload():
    if request.method == 'POST':
        file = request.files.get('customer_file')
        if not file:
            flash('No file uploaded.', 'customererror')
            return redirect(url_for('bulk_upload'))
        try:
            df = pd.read_csv(file)
            required_columns = {'Name', 'Email', 'Phone', 'Address', 'City', 'Company', 'Source', 'Status'}
            if not required_columns.issubset(df.columns):
                flash(f'Missing required columns: {", ".join(required_columns)}', 'customererror')
                return redirect(url_for('bulk_upload'))
            for _, row in df.iterrows():
                name = str(row.get('Name', '')).strip()
                email = str(row.get('Email', '')).strip()
                phone_number = re.sub(r'\D', '', str(row.get('Phone', '')))  # strip non-digits
                address = str(row.get('Address', '')).strip()
                city = str(row.get('City', '')).strip()
                company = str(row.get('Company', '')).strip()
                source = str(row.get('Source', '')).strip()
                status = str(row.get('Status', '')).strip()
                
                #if assigned is mentioned in the csv, assign to that user, else assign to uploader
                assigned_to_email = str(row.get('Assigned_To', '')).strip()
                if assigned_to_email:
                    assigned_user = User.query.filter_by(email=assigned_to_email).first()
                    if assigned_user:
                        assigned_to_id = assigned_user.id
                    else:
                        assigned_to_id = current_user.id

                if not all([name, email, phone_number]):
                    continue  # skip invalid rows
                if len(phone_number) != 10:
                    continue  # skip invalid phone numbers
                if Customer.query.filter_by(email=email, is_deleted=False).first():
                    continue  # skip duplicates
                new_customer = Customer(
                    name=name,
                    email=email,
                    phone_number=phone_number,
                    address=address,
                    city=city,
                    company=company,
                    source=source,
                    status=status,
                    created_by=current_user.employee.id,
                    assigned_to=Employee.query.filter_by(email=assigned_to_email).first().id if assigned_to_email and Employee.query.filter_by(email=assigned_to_email).first() else current_user.employee.id
                )
                db.session.add(new_customer)
            db.session.commit()
            flash('Bulk upload successful!', 'customersuccess')
            return redirect(url_for('customers'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error processing file: {str(e)}', 'customererror')
            return redirect(url_for('bulk_upload'))
    return render_template('customer/bulkuploadcustomer.html')
@app.route('/download-template')
@login_required
def download_template():
    template_path = 'static/templates/bulk_upload_template.csv'
    return send_file(template_path, as_attachment=True, download_name='bulk_upload_template.csv')
@app.route('/employee')
@login_required
def employee():
    all_employees = Employee.query.filter_by(is_deleted=False).all()
    return render_template('employee/employee.html', employees=all_employees)
@app.route('/add-employee', methods=['GET', 'POST'])
@login_required
def add_employee():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        phone_number = request.form.get('phone_number')
        position = request.form.get('position')

        if not all([name, email, phone_number, position]):
            flash('All fields are required.', 'employeeerror')
            return redirect(url_for('add_employee'))

        if Employee.query.filter_by(email=email).first():
            flash('Employee with this email already exists.', 'employeeerror')
            return redirect(url_for('add_employee'))

        new_employee = Employee(
            name=name,
            email=email,
            phone_number=phone_number,
            position=position
        )
        db.session.add(new_employee)
        db.session.commit()
        flash('Employee added successfully!', 'employeesuccess')
        return redirect(url_for('employee'))

    return render_template('employee/add_employee.html')
@app.route('/edit-employee/<int:employee_id>', methods=['GET', 'POST'])
@login_required
def edit_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        phone_number = re.sub(r'\D', '', request.form.get('phone_number', ''))
        position = request.form.get('position', '').strip()

        if not all([name, email, phone_number, position]):
            flash('All required fields must be filled.', 'employeeerror')
            return redirect(url_for('edit_employee', employee_id=employee_id))

        if len(phone_number) != 10:
            flash('Phone number must be 10 digits.', 'employeeerror')
            return redirect(url_for('edit_employee', employee_id=employee_id))

        # Check duplicate email excluding current employee
        existing = Employee.query.filter(Employee.email == email, Employee.id != employee_id, Employee.is_deleted == False).first()
        if existing:
            flash('Another employee with this email already exists.', 'employeeerror')
            return redirect(url_for('edit_employee', employee_id=employee_id))

        employee.name = name
        employee.email = email
        employee.phone_number = phone_number
        employee.position = position
        
        try:
            db.session.commit()
            flash('Employee profile updated successfully!', 'employeesuccess')
            return redirect(url_for('employee'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error updating profile: {str(e)}', 'employeeerror')
            return redirect(url_for('edit_employee', employee_id=employee_id))

    return render_template('employee/editemployee.html', employee=employee)
@app.route('/delete-employee/<int:employee_id>', methods=['POST'])
@login_required
def delete_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    employee.is_deleted=True
    db.session.commit()
    flash('Employee deleted successfully!', 'employeesuccess')
    return redirect(url_for('employee'))
if __name__ == '__main__':
    app.run(debug=True)
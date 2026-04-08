import os
import re

html_shell_top = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quotations | GlassEntials Accounts</title>
    <!-- Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@300;400;500;600;700&family=Playfair+Display:ital,wght@0,400;0,600;0,700;1,600&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/home.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/dashboard.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/quotation.css') }}">
"""

html_shell_nav = """
</head>
<body class="dashboard-page">
    <!-- Top Navbar -->
    <header class="dash-nav">
        <div class="nav-left">
            <div class="logo">
                <a href="/home" class="logo-link">
                    <img src="{{ url_for('static', filename='img/logo.png') }}" alt="GlassEntials Logo" class="site-logo">
                </a>
            </div>

            <nav class="dash-menu">
                <a href="{{ url_for('home_page') }}">Dashboard</a>
                <a href="{{ url_for('customers.customers_list') }}">Customers</a>
                <a href="{{ url_for('leads.leads_list') }}">Leads</a>
                <a href="{{ url_for('projects.projects_list') }}">Projects</a>
                <div class="dropdown">
                    <a href="#" {% if request.endpoint and 'tasks' in request.endpoint %}class="active"{% endif %}>Tasks ▾</a>
                    <div class="dropdown-content">
                        <a href="{{ url_for('tasks.tasks_list') }}">All Tasks</a>
                        <a href="{{ url_for('tasks.daily_tasks_list') }}">Daily Tasks</a>
                    </div>
                </div>
                <div class="dropdown">
                    <a href="#" class="active">Accounts ▾</a>
                    <div class="dropdown-content">
                        <a href="{{ url_for('accounts.invoice_list') }}">Invoice</a>
                        <a href="{{ url_for('accounts.quotation_list') }}">Quotations</a>
                        <a href="{{ url_for('expenses.expenses_list') }}">Expenses</a>
                    </div>
                </div>
                <div class="dropdown">
                    <a href="#">Settings ▾</a>
                    <div class="dropdown-content">
                        <a href="{{ url_for('employees.employee_list') }}">Employee Portal</a>
                    </div>
                </div>
            </nav>
        </div>

        <div class="user-profile">
            <span class="welcome-text">Welcome, {{ current_user.username }}</span>
            <div class="profile-dropdown">
                <div class="profile-trigger">
                    <img src="{{ get_profile_pic(current_user.employee) }}" alt="User" class="avatar">
                </div>
                <div class="profile-dropdown-content">
                    <a href="{{ url_for('auth.user_profile') }}">
                        <span class="icon">👤</span> Manage Profile
                    </a>
                    <a href="{{ url_for('auth.logout') }}" class="logout-link">
                        <span class="icon">🚪</span> Logout
                    </a>
                </div>
            </div>
        </div>
    </header>

    <main class="dash-container">
"""

files_to_fix = [
    'templates/accounts/quotation_list.html',
    'templates/accounts/quotation_form.html',
    'templates/accounts/quotation_view.html',
    'templates/accounts/quotation_settings.html',
    'templates/accounts/quotation_terms.html'
]

for fp in files_to_fix:
    with open(fp, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "{% extends 'base.html' %}" not in content:
        continue

    # Remove extends
    content = content.replace("{% extends 'base.html' %}", html_shell_top)
    
    # Remove old blocks 
    content = re.sub(r'\{%\s*block\s+title\s*%\}.*?\{%\s*endblock\s*%\}', '', content, flags=re.DOTALL)
    content = re.sub(r'\{%\s*block\s+head\s*%\}.*?\{%\s*endblock\s*%\}', '', content, flags=re.DOTALL)
    
    # Replace content open
    content = re.sub(r'\{%\s*block\s+content\s*%\}', html_shell_nav, content)
    
    # Replace scripts open
    content = re.sub(r'\{%\s*block\s+scripts\s*%\}', '</main>', content)
    
    # Eliminate all endblocks
    content = content.replace("{% endblock %}", '')
    
    content += "\n</body>\n</html>\n"
    
    with open(fp, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('Fixed', fp)

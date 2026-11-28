"""
tests/test_daily_tasks_views.py
Comprehensive role-based, functional, security, and regression tests for
Daily Tasks: Monthly View (Employees) & Dual Views (Admin / Manager / Super Admin).
"""
import unittest
import sys
from pathlib import Path
from datetime import date, datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from flask_login import LoginManager
from sqlalchemy import text

from model import (
    db,
    User,
    UserRole,
    Organization,
    OrganizationMember,
    OrgMemberRole,
    Employee,
    DailyTask,
)
from routes.tasks import tasks_bp
from utils.timezone import ist_filter, utcnow, to_ist


class DailyTasksViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root_dir = Path(__file__).resolve().parents[1]
        cls.app = Flask(
            "daily_tasks_views_test",
            template_folder=str(root_dir / "templates"),
            static_folder=str(root_dir / "static"),
        )
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
            WTF_CSRF_ENABLED=False,
            SECRET_KEY="daily-tasks-views-test-secret",
        )
        db.init_app(cls.app)

        cls.login_manager = LoginManager()
        cls.login_manager.init_app(cls.app)

        @cls.login_manager.user_loader
        def load_user(user_id):
            return db.session.get(User, int(user_id))

        cls.app.register_blueprint(tasks_bp)
        cls.app.add_template_filter(ist_filter, "ist")
        cls.app.add_template_filter(lambda s: str(s or ""), "format_tasks")

        # Dummy routes referenced by navbar.html & daily_tasks.html
        for ep in [
            "home_page", "customers.customers_list", "org_customers.org_customers_list",
            "leads.leads_list", "leads.all_follow_ups", "orders.list_orders",
            "projects.projects_list", "quotations.quotation_list", "accounts.invoice_list",
            "payments.due_payments", "payments.received_payments", "expenses.expenses_list",
            "gst_reports.gst_report_page", "products.products_list",
            "employees.employee_list", "contacts.contacts_list", "vendors.vendor_list",
            "trash.trash_list", "call_logger.dashboard", "org.organization_settings",
            "auth.user_profile", "auth.settings", "auth.logout", "auth.notifications",
            "messages.messages_page", "messages.total_unread_count", "api.recent_notifications",
        ]:
            cls.app.add_url_rule(
                f"/dummy/{ep.replace('.', '/')}",
                endpoint=ep,
                view_func=lambda ep=ep: f"dummy_{ep}",
            )

        cls.app.add_url_rule(
            "/dummy/projects/<int:project_id>",
            endpoint="projects.view_project",
            view_func=lambda project_id: f"proj_{project_id}",
        )
        cls.app.add_url_rule(
            "/dummy/documents/<int:doc_id>",
            endpoint="documents.download_document",
            view_func=lambda doc_id: f"doc_{doc_id}",
        )

        @cls.app.context_processor
        def utility_processor():
            return {
                "get_profile_pic": lambda emp: "/static/img/default-avatar.png",
                "unread_notifications_count": lambda: 0,
                "org_modules": {},
                "active_org": None,
                "user_orgs": [],
                "csrf_token": lambda: "test-csrf-token",
            }

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        self.client = self.app.test_client()
        db.session.execute(text("PRAGMA foreign_keys = ON;"))
        db.create_all()

        self.org1 = Organization(id=1, name="GlassEntials Main", unique_code="ORG001")
        self.org2 = Organization(id=2, name="Other Org", unique_code="ORG002")
        db.session.add_all([self.org1, self.org2])
        db.session.flush()

        # Org 1 Users: Admin, Manager, Owner (Super Admin equivalent in org), Emp1, Emp2, AdminNoEmp
        self.u_admin = User(id=1, username="admin1", password="pw", email="admin1@test.com", role=UserRole.ADMIN, organization_id=1)
        self.u_mgr = User(id=2, username="mgr1", password="pw", email="mgr1@test.com", role=UserRole.MANAGER, organization_id=1)
        self.u_owner = User(id=3, username="owner1", password="pw", email="owner1@test.com", role=UserRole.ADMIN, organization_id=1)
        self.u_emp1 = User(id=4, username="emp1", password="pw", email="emp1@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_emp2 = User(id=5, username="emp2", password="pw", email="emp2@test.com", role=UserRole.EMPLOYEE, organization_id=1)
        self.u_admin_no_emp = User(id=6, username="admin_no_emp", password="pw", email="noemp@test.com", role=UserRole.ADMIN, organization_id=1)
        # Org 2 User
        self.u_org2 = User(id=7, username="org2_admin", password="pw", email="org2@test.com", role=UserRole.ADMIN, organization_id=2)

        db.session.add_all([
            self.u_admin, self.u_mgr, self.u_owner,
            self.u_emp1, self.u_emp2, self.u_admin_no_emp, self.u_org2,
        ])
        db.session.flush()

        self.emp_admin = Employee(id=1, user_id=1, name="Alice Admin", organization_id=1)
        self.emp_mgr = Employee(id=2, user_id=2, name="Bob Manager", organization_id=1)
        self.emp_owner = Employee(id=3, user_id=3, name="Super Owner", organization_id=1)
        self.emp1 = Employee(id=4, user_id=4, name="Charlie Emp1", organization_id=1)
        self.emp2 = Employee(id=5, user_id=5, name="Diana Emp2", organization_id=1)
        self.emp_org2 = Employee(id=6, user_id=7, name="Zenith Admin", organization_id=2)
        db.session.add_all([
            self.emp_admin, self.emp_mgr, self.emp_owner,
            self.emp1, self.emp2, self.emp_org2,
        ])

        # Org membership for Owner
        db.session.add(OrganizationMember(
            organization_id=1, user_id=3, role=OrgMemberRole.OWNER, status="active"
        ))
        db.session.flush()

        # Dates anchored to current IST date for default month/today assertions
        self.today_ist = to_ist(utcnow()).date()
        self.curr_first = date(self.today_ist.year, self.today_ist.month, 1)
        self.curr_mid = date(self.today_ist.year, self.today_ist.month, min(15, self.today_ist.day if self.today_ist.day > 1 else 15))
        if self.curr_mid == self.today_ist:
            # Pick a different day in the same month from today_ist
            self.curr_other_day = date(
                self.today_ist.year,
                self.today_ist.month,
                2 if self.today_ist.day != 2 else 3,
            )
        else:
            self.curr_other_day = self.curr_mid

        prev_y, prev_m = (
            (self.today_ist.year - 1, 12)
            if self.today_ist.month == 1
            else (self.today_ist.year, self.today_ist.month - 1)
        )
        self.prev_month_date = date(prev_y, prev_m, 10)

        # Seed Daily Tasks across employees and months
        self.t_emp1_today = DailyTask(
            employee_id=self.emp1.id, date=self.today_ist,
            task_description="EMP1_TODAY_LOG", hours_spent=4.0, work_category="Site Visit", organization_id=1,
        )
        self.t_emp1_other = DailyTask(
            employee_id=self.emp1.id, date=self.curr_other_day,
            task_description="EMP1_OTHER_DAY_THIS_MONTH", hours_spent=6.0, work_category="Design", organization_id=1,
        )
        self.t_emp1_first = DailyTask(
            employee_id=self.emp1.id, date=self.curr_first,
            task_description="EMP1_FIRST_DAY_THIS_MONTH", hours_spent=2.5, work_category="General", organization_id=1,
        )
        self.t_emp1_prev_month = DailyTask(
            employee_id=self.emp1.id, date=self.prev_month_date,
            task_description="EMP1_PREV_MONTH_LOG", hours_spent=8.0, work_category="Fabrication", organization_id=1,
        )
        self.t_emp1_dec = DailyTask(
            employee_id=self.emp1.id, date=date(2026, 12, 31),
            task_description="EMP1_DEC_2026_END", hours_spent=5.0, work_category="General", organization_id=1,
        )
        self.t_emp1_jan = DailyTask(
            employee_id=self.emp1.id, date=date(2027, 1, 1),
            task_description="EMP1_JAN_2027_START", hours_spent=3.0, work_category="General", organization_id=1,
        )

        self.t_emp2_today = DailyTask(
            employee_id=self.emp2.id, date=self.today_ist,
            task_description="EMP2_SECRET_TODAY_LOG", hours_spent=7.0, work_category="Installation", organization_id=1,
        )
        self.t_admin_today = DailyTask(
            employee_id=self.emp_admin.id, date=self.today_ist,
            task_description="ADMIN_OWN_TODAY_LOG", hours_spent=3.5, work_category="Client Meeting", organization_id=1,
        )
        self.t_admin_other = DailyTask(
            employee_id=self.emp_admin.id, date=self.curr_other_day,
            task_description="ADMIN_OWN_OTHER_DAY_LOG", hours_spent=5.5, work_category="Quotation", organization_id=1,
        )
        self.t_owner_today = DailyTask(
            employee_id=self.emp_owner.id, date=self.today_ist,
            task_description="OWNER_OWN_TODAY_LOG", hours_spent=4.5, work_category="General", organization_id=1,
        )
        self.t_org2_today = DailyTask(
            employee_id=self.emp_org2.id, date=self.today_ist,
            task_description="ORG2_ISOLATED_LOG", hours_spent=8.0, work_category="General", organization_id=2,
        )

        db.session.add_all([
            self.t_emp1_today, self.t_emp1_other, self.t_emp1_first,
            self.t_emp1_prev_month, self.t_emp1_dec, self.t_emp1_jan,
            self.t_emp2_today, self.t_admin_today, self.t_admin_other,
            self.t_owner_today, self.t_org2_today,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        db.session.remove()
        try:
            db.session.execute(text("PRAGMA foreign_keys = OFF;"))
            db.drop_all()
        except Exception:
            pass
        self.context.pop()

    def _login(self, user_id: int):
        from flask import g
        g.pop("_login_user", None)
        self.client = self.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    # ── EMPLOYEE TESTS ────────────────────────────────────────────────────────

    def test_01_employee_default_shows_entire_current_month_own_tasks_only(self):
        """Employee default view shows full current month's own tasks, never other employees'."""
        self._login(self.u_emp1.id)
        resp = self.client.get("/daily-tasks")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Own current month tasks appear (both today AND other days of current month)
        self.assertIn("EMP1_TODAY_LOG", html)
        self.assertIn("EMP1_OTHER_DAY_THIS_MONTH", html)
        self.assertIn("EMP1_FIRST_DAY_THIS_MONTH", html)

        # Previous month & other employees & other orgs must NOT appear
        self.assertNotIn("EMP1_PREV_MONTH_LOG", html)
        self.assertNotIn("EMP2_SECRET_TODAY_LOG", html)
        self.assertNotIn("ADMIN_OWN_TODAY_LOG", html)
        self.assertNotIn("ORG2_ISOLATED_LOG", html)

        # Admin dual-mode switcher must NOT be rendered for employees
        self.assertNotIn("All Employees – Date Wise", html)
        self.assertNotIn("Select Employee", html)

    def test_02_employee_month_navigation_and_dec_jan_boundary(self):
        """Employee can navigate to previous months and Dec <-> Jan transitions accurately."""
        self._login(self.u_emp1.id)

        # Previous month
        resp_prev = self.client.get(
            f"/daily-tasks?month_num={self.prev_month_date.month}&year={self.prev_month_date.year}"
        )
        self.assertEqual(resp_prev.status_code, 200)
        html_prev = resp_prev.get_data(as_text=True)
        self.assertIn("EMP1_PREV_MONTH_LOG", html_prev)
        self.assertNotIn("EMP1_TODAY_LOG", html_prev)

        # December 2026 boundary
        resp_dec = self.client.get("/daily-tasks?month_num=12&year=2026")
        html_dec = resp_dec.get_data(as_text=True)
        self.assertIn("EMP1_DEC_2026_END", html_dec)
        self.assertNotIn("EMP1_JAN_2027_START", html_dec)
        # Next month link should point to Jan 2027
        self.assertIn("month_num=1&amp;year=2027", html_dec)

        # January 2027 boundary
        resp_jan = self.client.get("/daily-tasks?month=2027-01")
        html_jan = resp_jan.get_data(as_text=True)
        self.assertIn("EMP1_JAN_2027_START", html_jan)
        self.assertNotIn("EMP1_DEC_2026_END", html_jan)
        # Prev month link should point to Dec 2026
        self.assertIn("month_num=12&amp;year=2026", html_jan)

    def test_03_employee_custom_date_range_within_month(self):
        """Inclusive from_date / to_date filtering within selected month, no conflict."""
        self._login(self.u_emp1.id)

        # Filter specifically to curr_first (inclusive)
        first_str = self.curr_first.strftime("%Y-%m-%d")
        resp = self.client.get(
            f"/daily-tasks?month_num={self.today_ist.month}&year={self.today_ist.year}"
            f"&from_date={first_str}&to_date={first_str}"
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("EMP1_FIRST_DAY_THIS_MONTH", html)
        if self.curr_other_day != self.curr_first:
            self.assertNotIn("EMP1_OTHER_DAY_THIS_MONTH", html)

        # Out-of-month range does NOT leak previous month's tasks into Dec 2026
        prev_str = self.prev_month_date.strftime("%Y-%m-%d")
        resp_oob = self.client.get(
            f"/daily-tasks?month_num=12&year=2026&from_date={prev_str}&to_date={prev_str}"
        )
        html_oob = resp_oob.get_data(as_text=True)
        self.assertNotIn("EMP1_PREV_MONTH_LOG", html_oob)
        self.assertNotIn("EMP1_DEC_2026_END", html_oob)

        # Reversed date range returns error flash and empty task list
        later = date(2026, 12, 20).strftime("%Y-%m-%d")
        earlier = date(2026, 12, 5).strftime("%Y-%m-%d")
        resp_rev = self.client.get(
            f"/daily-tasks?month_num=12&year=2026&from_date={later}&to_date={earlier}"
        )
        html_rev = resp_rev.get_data(as_text=True)
        self.assertIn("From Date cannot be later than To Date", html_rev)
        self.assertNotIn("EMP1_DEC_2026_END", html_rev)

    def test_04_employee_cannot_bypass_via_url_or_api_manipulation(self):
        """Employee tampering with view_mode, employee_id, or direct task endpoints is blocked."""
        self._login(self.u_emp1.id)

        # URL manipulation attempt on list endpoint
        resp = self.client.get(
            f"/daily-tasks?view_mode=all_daily&employee_id={self.emp2.id}&date={self.today_ist.strftime('%Y-%m-%d')}"
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertNotIn("EMP2_SECRET_TODAY_LOG", html)
        self.assertNotIn("ADMIN_OWN_TODAY_LOG", html)
        self.assertIn("EMP1_TODAY_LOG", html)

        # Direct API view attempt on Emp2's task -> 403
        resp_view = self.client.get(f"/daily-task/{self.t_emp2_today.id}/view")
        self.assertEqual(resp_view.status_code, 403)

        # Direct edit/delete attempt on Emp2's task -> blocked & unchanged
        resp_edit = self.client.post(
            f"/daily-task/{self.t_emp2_today.id}/edit",
            data={"description": "HACKED", "date": self.today_ist.strftime("%Y-%m-%d")},
            follow_redirects=True,
        )
        self.assertEqual(resp_edit.status_code, 200)
        self.assertEqual(db.session.get(DailyTask, self.t_emp2_today.id).task_description, "EMP2_SECRET_TODAY_LOG")

        resp_del = self.client.post(f"/daily-task/{self.t_emp2_today.id}/delete")
        self.assertEqual(resp_del.status_code, 302)
        self.assertIsNotNone(db.session.get(DailyTask, self.t_emp2_today.id))

    # ── ADMIN & SUPER ADMIN DUAL-MODE TESTS ───────────────────────────────────

    def test_05_admin_default_all_employees_date_wise(self):
        """Admin default view is All Employees - Date Wise showing all org tasks for the date."""
        self._login(self.u_admin.id)
        resp = self.client.get("/daily-tasks")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # View selector present
        self.assertIn("All Employees – Date Wise", html)
        self.assertIn("My Tasks – Monthly", html)

        # Today's tasks for all Org 1 employees appear
        self.assertIn("ADMIN_OWN_TODAY_LOG", html)
        self.assertIn("EMP1_TODAY_LOG", html)
        self.assertIn("EMP2_SECRET_TODAY_LOG", html)
        self.assertIn("OWNER_OWN_TODAY_LOG", html)

        # Other dates & Org 2 excluded
        self.assertNotIn("EMP1_PREV_MONTH_LOG", html)
        self.assertNotIn("ORG2_ISOLATED_LOG", html)

        # Filter by specific employee in Mode A
        resp_emp = self.client.get(
            f"/daily-tasks?view_mode=all_daily&date={self.today_ist.strftime('%Y-%m-%d')}&employee_id={self.emp2.id}"
        )
        html_emp = resp_emp.get_data(as_text=True)
        self.assertIn("EMP2_SECRET_TODAY_LOG", html_emp)
        self.assertNotIn("EMP1_TODAY_LOG", html_emp)
        self.assertNotIn("ADMIN_OWN_TODAY_LOG", html_emp)

    def test_06_admin_my_tasks_monthly_mode_strictly_own_tasks(self):
        """Admin Mode B (my_monthly) shows ONLY admin's own tasks for month, ignoring employee_id."""
        self._login(self.u_admin.id)

        # Even if employee_id=emp2 is supplied in query, my_monthly must ignore it!
        resp = self.client.get(
            f"/daily-tasks?view_mode=my_monthly&employee_id={self.emp2.id}"
        )
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        # Only Admin's own current-month tasks appear
        self.assertIn("ADMIN_OWN_TODAY_LOG", html)
        self.assertIn("ADMIN_OWN_OTHER_DAY_LOG", html)

        # Other employees' tasks MUST NOT appear
        self.assertNotIn("EMP1_TODAY_LOG", html)
        self.assertNotIn("EMP2_SECRET_TODAY_LOG", html)
        self.assertNotIn("OWNER_OWN_TODAY_LOG", html)
        self.assertNotIn("ORG2_ISOLATED_LOG", html)

        # Date range filter inside Mode B
        other_str = self.curr_other_day.strftime("%Y-%m-%d")
        resp_range = self.client.get(
            f"/daily-tasks?view_mode=my_monthly&month_num={self.today_ist.month}&year={self.today_ist.year}"
            f"&from_date={other_str}&to_date={other_str}"
        )
        html_range = resp_range.get_data(as_text=True)
        self.assertIn("ADMIN_OWN_OTHER_DAY_LOG", html_range)
        if self.curr_other_day != self.today_ist:
            self.assertNotIn("ADMIN_OWN_TODAY_LOG", html_range)

    def test_07_admin_without_employee_profile_my_monthly_shows_no_other_tasks(self):
        """Admin lacking an Employee record sees empty list in my_monthly, never everyone's tasks."""
        self._login(self.u_admin_no_emp.id)
        resp = self.client.get("/daily-tasks?view_mode=my_monthly")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)

        self.assertNotIn("ADMIN_OWN_TODAY_LOG", html)
        self.assertNotIn("EMP1_TODAY_LOG", html)
        self.assertNotIn("EMP2_SECRET_TODAY_LOG", html)

    def test_08_super_admin_owner_dual_modes(self):
        """Org Owner / Super Admin gets both All Employees (default) and My Tasks - Monthly."""
        self._login(self.u_owner.id)

        # Default Mode A
        resp_a = self.client.get("/daily-tasks")
        html_a = resp_a.get_data(as_text=True)
        self.assertIn("All Employees – Date Wise", html_a)
        self.assertIn("OWNER_OWN_TODAY_LOG", html_a)
        self.assertIn("EMP1_TODAY_LOG", html_a)

        # Mode B
        resp_b = self.client.get("/daily-tasks?view_mode=my_monthly")
        html_b = resp_b.get_data(as_text=True)
        self.assertIn("OWNER_OWN_TODAY_LOG", html_b)
        self.assertNotIn("EMP1_TODAY_LOG", html_b)
        self.assertNotIn("ADMIN_OWN_TODAY_LOG", html_b)

    # ── REGRESSION & CROSS-TENANT ISOLATION ───────────────────────────────────

    def test_09_crud_regression_and_cross_tenant_isolation(self):
        """Create, view, edit, delete daily task works; cross-org access returns 404."""
        self._login(self.u_emp1.id)

        # Create
        resp_add = self.client.post(
            "/add-daily-task",
            data={
                "date": self.today_ist.strftime("%Y-%m-%d"),
                "description": "EMP1_NEWLY_CREATED_TASK",
                "hours_spent": "2.0",
                "work_category": "Design",
                "project_id": "none",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp_add.status_code, 200)
        self.assertIn("EMP1_NEWLY_CREATED_TASK", resp_add.get_data(as_text=True))

        created = DailyTask.query.filter_by(task_description="EMP1_NEWLY_CREATED_TASK").first()
        self.assertIsNotNone(created)

        # View JSON
        resp_json = self.client.get(f"/daily-task/{created.id}/view")
        self.assertEqual(resp_json.status_code, 200)
        self.assertEqual(resp_json.get_json()["task_description"], "EMP1_NEWLY_CREATED_TASK")

        # Edit
        resp_edit = self.client.post(
            f"/daily-task/{created.id}/edit",
            data={
                "date": self.today_ist.strftime("%Y-%m-%d"),
                "description": "EMP1_UPDATED_TASK",
                "hours_spent": "3.5",
                "work_category": "Fabrication",
                "project_id": "none",
            },
            follow_redirects=True,
        )
        self.assertEqual(resp_edit.status_code, 200)
        self.assertIn("EMP1_UPDATED_TASK", resp_edit.get_data(as_text=True))

        # Delete
        resp_del = self.client.post(
            f"/daily-task/{created.id}/delete",
            data={"view_mode": "my_monthly"},
            follow_redirects=False,
        )
        self.assertEqual(resp_del.status_code, 302)
        self.assertIsNone(db.session.get(DailyTask, created.id))

        # Cross-tenant isolation: Org 2 accessing Org 1's task -> 404
        self._login(self.u_org2.id)
        resp_cross = self.client.get(f"/daily-task/{self.t_emp1_today.id}/view")
        self.assertEqual(resp_cross.status_code, 404)


if __name__ == "__main__":
    unittest.main()

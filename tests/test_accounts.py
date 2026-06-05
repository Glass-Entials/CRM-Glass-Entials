import pytest
from datetime import datetime
from model import (
    db, Expense, ExpenseCategory, ExpenseStatus, Invoice, InvoiceStatus,
    Quotation, QuotationStatus, Customer, Employee, Project
)

def test_expenses_list(logged_in_client):
    resp = logged_in_client.get("/expenses/")
    assert resp.status_code == 200

def test_add_expense(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    resp = logged_in_client.post("/expenses/add", data={
        "title": "Office Supplies",
        "amount": "150.50",
        "category": ExpenseCategory.OFFICE_SUPPLIES.value,
        "date": "2026-06-05",
        "description": "Bought pens and papers"
    }, follow_redirects=True)
    assert resp.status_code == 200

    exp = Expense.query.filter_by(title="Office Supplies").first()
    assert exp is not None
    assert exp.amount == 150.50

def test_edit_expense(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    exp = Expense(title="Travel", amount=50.0, category=ExpenseCategory.TRAVEL, organization_id=org.id, employee_id=emp.id)
    db.session.add(exp)
    db.session.commit()

    resp = logged_in_client.post(f"/expenses/edit/{exp.id}", data={
        "title": "Business Travel",
        "amount": "60.0",
        "description": "Bus ticket"
    }, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(exp)
    assert exp.title == "Business Travel"
    assert exp.amount == 60.0

def test_delete_expense(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    exp = Expense(title="Dinner", amount=35.0, category=ExpenseCategory.OTHER, organization_id=org.id, employee_id=emp.id)
    db.session.add(exp)
    db.session.commit()

    resp = logged_in_client.post(f"/expenses/delete/{exp.id}", follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(exp)
    assert exp.is_deleted is True

def test_update_expense_status(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    exp = Expense(title="Gas", amount=20.0, category=ExpenseCategory.TRAVEL, organization_id=org.id, employee_id=emp.id)
    db.session.add(exp)
    db.session.commit()

    resp = logged_in_client.post(f"/expenses/status/{exp.id}", data={
        "status": ExpenseStatus.APPROVED.value
    }, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(exp)
    assert exp.status == ExpenseStatus.APPROVED

# ─── INVOICE TESTS ────────────────────────────────────────────────────────────

def test_invoices_list(logged_in_client):
    resp = logged_in_client.get("/invoices")
    assert resp.status_code == 200

def test_add_invoice(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    cust = Customer(name="Invoice Cust", email="inv.cust@test.com", phone_number="1231231234", organization_id=org.id, created_by=emp.id)
    db.session.add(cust)
    db.session.commit()

    resp = logged_in_client.post("/add-invoice", data={
        "invoice_title": "Tax Invoice",
        "issue_date": "2026-06-05",
        "due_date": "2026-07-05",
        "customer_id": str(cust.id),
        "status": InvoiceStatus.UNPAID.value,
        "total_discount": "0",
        "total_discount_type": "flat",
        "additional_charges": "0",
        "item_name[]": ["Glass Panel"],
        "description[]": ["Tempered Glass"],
        "quantity[]": ["2"],
        "rate[]": ["100"],
        "gst_rate[]": ["18"]
    }, follow_redirects=True)
    assert resp.status_code == 200

    inv = Invoice.query.filter_by(customer_id=cust.id).first()
    assert inv is not None
    assert inv.total_amount == 236.0 # (2 * 100) * 1.18

def test_edit_invoice(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    cust = Customer(name="Invoice Cust 2", email="inv.cust2@test.com", phone_number="1231231235", organization_id=org.id, created_by=emp.id)
    db.session.add(cust)
    db.session.flush()

    inv = Invoice(invoice_number="INV/26/0001", organization_id=org.id, created_by=emp.id, customer_id=cust.id)
    db.session.add(inv)
    db.session.commit()

    resp = logged_in_client.post(f"/edit-invoice/{inv.id}", data={
        "invoice_title": "Updated Invoice",
        "issue_date": "2026-06-05",
        "due_date": "2026-07-05",
        "customer_id": str(cust.id),
        "status": InvoiceStatus.PAID.value,
        "total_discount": "0",
        "total_discount_type": "flat",
        "additional_charges": "0",
        "item_name[]": ["Glass Panel Updated"],
        "quantity[]": ["1"],
        "rate[]": ["150"],
        "gst_rate[]": ["18"]
    }, follow_redirects=True)
    assert resp.status_code == 200

    db.session.refresh(inv)
    assert inv.invoice_title == "Updated Invoice"
    assert inv.status == InvoiceStatus.PAID
    assert inv.total_amount == 177.0

# ─── QUOTATION TESTS ──────────────────────────────────────────────────────────

def test_quotations_list(logged_in_client):
    resp = logged_in_client.get("/quotations/")
    assert resp.status_code == 200

def test_add_quotation(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    cust = Customer(name="Quotation Cust", email="q.cust@test.com", phone_number="1231231236", organization_id=org.id, created_by=emp.id)
    db.session.add(cust)
    db.session.commit()

    resp = logged_in_client.post("/quotations/new", data={
        "quotation_title": "Glass Quote",
        "doc_type": "Quotation",
        "issue_date": "2026-06-05",
        "customer_id": str(cust.id),
        "status": QuotationStatus.DRAFT.value,
        "item_name[]": ["Quote Glass Item"],
        "quantity[]": ["10"],
        "rate[]": ["50"],
        "gst_rate[]": ["18"]
    }, follow_redirects=True)
    assert resp.status_code == 200

    q = Quotation.query.filter_by(customer_id=cust.id).first()
    assert q is not None

import pytest
from model import db, Customer

def test_customers_list(logged_in_client):
    resp = logged_in_client.get("/customers")
    assert resp.status_code == 200

def test_add_customer(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    resp = logged_in_client.post("/add-customer", data={
        "name": "Jane Doe",
        "email": "jane@example.com",
        "phone_number": "5551234567",
        "address": "123 Main St",
        "company": "Jane Co"
    }, follow_redirects=True)
    assert resp.status_code == 200
    cust = Customer.query.filter_by(email="jane@example.com").first()
    assert cust is not None
    assert cust.organization_id == org.id

def test_edit_customer(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    cust = Customer(name="Old Name", email="old@example.com", phone_number="9998887776", organization_id=org.id, created_by=emp.id)
    db.session.add(cust)
    db.session.commit()

    resp = logged_in_client.post(f"/edit-customer/{cust.id}", data={
        "name": "New Name",
        "email": "old@example.com",
        "phone_number": "9998887776",
        "company": "New Co"
    }, follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(cust)
    assert cust.name == "New Name"

def test_delete_customer(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    cust = Customer(name="To Delete", email="delete@example.com", phone_number="1111111111", organization_id=org.id, created_by=emp.id)
    db.session.add(cust)
    db.session.commit()

    resp = logged_in_client.post(f"/delete-customer/{cust.id}", follow_redirects=True)
    assert resp.status_code == 200
    assert Customer.query.get(cust.id).is_deleted is True

import pytest
from model import db, Lead, LeadSource, LeadStatus

def test_leads_list(logged_in_client):
    resp = logged_in_client.get("/leads")
    assert resp.status_code == 200

def test_add_lead(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    resp = logged_in_client.post("/add-lead", data={
        "name": "John Doe Lead",
        "email": "john.lead@example.com",
        "phone": "5559876543",
        "company": "Lead Co",
        "source": LeadSource.WEBSITE.value,
        "status": LeadStatus.NEW.value,
    }, follow_redirects=True)
    assert resp.status_code == 200
    lead = Lead.query.filter_by(email="john.lead@example.com").first()
    assert lead is not None
    assert lead.organization_id == org.id

def test_edit_lead(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    lead = Lead(name="Old Lead", email="old.lead@example.com", phone_number="9998887775", source=LeadSource.OTHER, status=LeadStatus.NEW, organization_id=org.id, created_by=emp.id)
    db.session.add(lead)
    db.session.commit()

    resp = logged_in_client.post(f"/edit-lead/{lead.id}", data={
        "name": "New Lead",
        "email": "old.lead@example.com",
        "phone": "9998887775",
        "source": LeadSource.REFERRAL.value,
        "status": LeadStatus.ACTIVE.value
    }, follow_redirects=True)
    assert resp.status_code == 200
    db.session.refresh(lead)
    assert lead.name == "New Lead"
    assert lead.status == LeadStatus.ACTIVE

def test_delete_lead(logged_in_client, seed_org_user):
    org, user, emp = seed_org_user
    lead = Lead(name="To Delete Lead", email="delete.lead@example.com", phone_number="1111111112", source=LeadSource.OTHER, status=LeadStatus.NEW, organization_id=org.id, created_by=emp.id)
    db.session.add(lead)
    db.session.commit()

    resp = logged_in_client.post(f"/delete-lead/{lead.id}", follow_redirects=True)
    assert resp.status_code == 200
    assert Lead.query.get(lead.id).is_deleted is True

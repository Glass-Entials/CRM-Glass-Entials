import os
from app import app
from model import db, Plan
import json

def seed_plans():
    with app.app_context():
        plans_data = [
            {
                "name": "Starter",
                "display_name": "Starter",
                "description": "Perfect for small teams getting started",
                "default_member_limit": 5,
                "default_storage_limit_gb": 10.0,
                "monthly_price_paise": 299900,
                "yearly_price_paise": 2999900,
                "sort_order": 1,
                "features_json": json.dumps([
                    "Customer Management",
                    "Lead Management",
                    "Task Management",
                    "Sales Pipeline",
                    "Dashboard",
                    "Email Support"
                ])
            },
            {
                "name": "Professional",
                "display_name": "Professional",
                "description": "For growing teams that need more power",
                "default_member_limit": 20,
                "default_storage_limit_gb": 50.0,
                "monthly_price_paise": 599900,
                "yearly_price_paise": 5999900,
                "sort_order": 2,
                "features_json": json.dumps([
                    "Everything in Starter",
                    "Team Management",
                    "Reports & Analytics",
                    "Notifications",
                    "Role Based Access",
                    "Priority Support"
                ])
            },
            {
                "name": "Business",
                "display_name": "Business",
                "description": "For established businesses scaling up",
                "default_member_limit": 50,
                "default_storage_limit_gb": 200.0,
                "monthly_price_paise": 999900,
                "yearly_price_paise": 9999900,
                "sort_order": 3,
                "features_json": json.dumps([
                    "Everything in Professional",
                    "Advanced Reports",
                    "API Access",
                    "Custom Workflows",
                    "Integrations",
                    "Priority Support"
                ])
            },
            {
                "name": "Enterprise",
                "display_name": "Enterprise",
                "description": "Custom solutions for large organizations",
                "default_member_limit": 9999,
                "default_storage_limit_gb": 1000.0,
                "monthly_price_paise": 0,
                "yearly_price_paise": 0,
                "sort_order": 4,
                "features_json": json.dumps([
                    "Everything in Business",
                    "Dedicated Account Manager",
                    "Custom Integrations",
                    "SLA Support",
                    "Enterprise Security",
                    "Unlimited Users"
                ])
            }
        ]

        for p_data in plans_data:
            existing = Plan.query.filter_by(name=p_data['name']).first()
            if existing:
                for key, value in p_data.items():
                    setattr(existing, key, value)
            else:
                new_plan = Plan(**p_data)
                db.session.add(new_plan)
        
        db.session.commit()
        print("SaaS plans seeded successfully!")

if __name__ == "__main__":
    seed_plans()

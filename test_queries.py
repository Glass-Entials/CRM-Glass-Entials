import os
import json
from app import app
from model import db, User, Organization
from ai.ai_service import process_question
from flask_login import login_user

def test_queries():
    with app.app_context():
        # Get an active org and user
        user = User.query.filter_by(username="ratandeep.purohit@gmail.com").first()
        if not user:
            user = User.query.first()
        org_id = user.organization_id
        
        queries = [
            ("A", "How many customers do we have?"),
            ("B", "How many customers were created by Ratandeep?"),
            ("C", "Show customers created by Ratandeep"),
            ("D", "How many leads were created by Ratandeep?"),
            ("E", "Show recent activities"),
            ("F", "What are the GST rules for B2B?"),
            ("G", "How many customers do we have?")
        ]
        
        for label, q in queries:
            print(f"\n{'='*50}\nQuery {label}: {q}")
            res = process_question(q, org_id, current_user_id=user.id)
            print(json.dumps({
                "type": res.get("query_type"),
                "answer": res.get("answer", "")[:100] + "...",
                "sources": res.get("sources"),
                "structured_data": res.get("structured_data")
            }, indent=2, default=str))
            
            # Print document data separately to avoid spam
            docs = res.get("document_data", [])
            if docs:
                print(f"Found {len(docs)} documents.")

if __name__ == "__main__":
    test_queries()

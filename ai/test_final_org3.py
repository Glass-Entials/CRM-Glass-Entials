"""Final test for org 3 Ratandeep scenario."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app
from ai.ai_service import process_question
from ai.crm_query import resolve_creator_in_org, count_leads

with app.app_context():
    print("=== Ratandeep is in org 3 ===")
    uid = resolve_creator_in_org("Ratandeep", 3)
    print(f"Ratandeep resolved in org 3: user_id={uid}")

    r1 = process_question("How many leads do we have", org_id=3, current_user_id=uid or 1)
    print(f"Total leads in org 3   : {r1['answer']}")

    r2 = process_question("How many leads were created by Ratandeep", org_id=3, current_user_id=uid or 1)
    print(f"By Ratandeep (count)   : {r2['answer']}")

    r3 = process_question("Show my leads", org_id=3, current_user_id=uid or 1)
    print(f"My leads (uid={uid})   : {r3['answer']}")

    r4 = process_question("Show leads created by Ratandeep", org_id=3, current_user_id=uid or 1)
    recs = r4.get("structured_data", {}).get("data", [])
    print(f"Show leads by Ratandeep: {r4['answer']} | records={len(recs)}")
    if recs:
        print(f"  First record: name={recs[0].get('name')}  status={recs[0].get('status')}")

    print()
    print("=== Cross-org isolation: Ratandeep NOT resolvable in org 1 ===")
    uid_org1 = resolve_creator_in_org("Ratandeep", 1)
    print(f"Ratandeep in org 1: {uid_org1} (must be None)")
    r5 = process_question("How many leads were created by Ratandeep", org_id=1, current_user_id=999)
    print(f"Query result for org 1: {r5['answer']}")
    assert "couldn't find" in r5["answer"].lower(), "FAIL: should return not-found message"
    print("ISOLATION: CONFIRMED - not-found message returned correctly")

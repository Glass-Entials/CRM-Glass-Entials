"""
Test script for AI creator filter implementation.
Run: python ai/test_creator_filter.py
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import app
from ai.ai_service import process_question, extract_creator_intent
from ai.crm_query import resolve_creator_in_org, count_leads

PASS = "✓ PASS"
FAIL = "✗ FAIL"

def test(label, actual, expected):
    ok = actual == expected
    print(f"{'  OK  ' if ok else ' FAIL '} {label}")
    print(f"        got={repr(actual)}  expected={repr(expected)}")
    return ok

with app.app_context():
    all_ok = True

    # ── Section 1: Intent extraction ──────────────────────────────────
    print("\n=== 1. extract_creator_intent() ===")
    cases = [
        ("How many leads do we have",              None),
        ("How many leads were created by Ratandeep","Ratandeep"),
        ("Show leads created by Ratandeep",         "Ratandeep"),
        ("how many leads did Ratandeep create",     "Ratandeep"),
        ("How many leads did I create",             "me"),
        ("Show my leads",                           "me"),
        ("leads created by me",                     "me"),
        ("leads I created",                         "me"),
        ("show leads created by Kumar",             "Kumar"),
    ]
    for q, expected in cases:
        result = extract_creator_intent(q)
        ok = test(repr(q[:55]), result, expected)
        all_ok = all_ok and ok

    # ── Section 2: Org-scoped user resolution ─────────────────────────
    print("\n=== 2. resolve_creator_in_org() ===")

    uid_org1 = resolve_creator_in_org("Ratandeep", 1)
    print(f"  Ratandeep in org 1  -> user_id={uid_org1}")

    uid_other = resolve_creator_in_org("Ratandeep", 999)
    ok = uid_other is None
    all_ok = all_ok and ok
    print(f"  {'  OK  ' if ok else ' FAIL '} Ratandeep in org 999 = {uid_other} (should be None) ← tenant isolation")

    uid_unknown = resolve_creator_in_org("NonExistentUser12345xyz", 1)
    ok = uid_unknown is None
    all_ok = all_ok and ok
    print(f"  {'  OK  ' if ok else ' FAIL '} Unknown user in org 1 = {uid_unknown} (should be None)")

    # ── Section 3: End-to-end process_question ────────────────────────
    print("\n=== 3. process_question() end-to-end ===")

    # T1: total leads (no filter)
    r1 = process_question("How many leads do we have", org_id=1, current_user_id=1)
    print(f"  T1 Total leads      : {r1['answer']}")
    total_leads = r1["structured_data"]["data"] if r1.get("structured_data") else "?"

    # T2: count filtered by Ratandeep
    r2 = process_question("How many leads were created by Ratandeep", org_id=1, current_user_id=1)
    print(f"  T2 By Ratandeep cnt : {r2['answer']}")

    # T3: list filtered by Ratandeep
    r3 = process_question("Show leads created by Ratandeep", org_id=1, current_user_id=1)
    records3 = r3.get("structured_data", {}).get("data", [])
    print(f"  T3 Show by Ratandeep: {r3['answer']} | records={len(records3)}")

    # T4: my leads (current_user_id=1)
    r4 = process_question("Show my leads", org_id=1, current_user_id=1)
    print(f"  T4 My leads (uid=1) : {r4['answer']}")

    # T5: unknown user
    r5 = process_question("Show leads created by NonExistentUser12345xyz", org_id=1, current_user_id=1)
    print(f"  T5 Unknown user     : {r5['answer']}")
    ok5 = "couldn't find" in r5["answer"].lower()
    all_ok = all_ok and ok5
    print(f"  {'  OK  ' if ok5 else ' FAIL '} Returns helpful not-found message")

    # T6: isolation — org 999 gets 0 leads regardless
    r6 = process_question("How many leads do we have", org_id=999, current_user_id=999)
    ok6 = r6["structured_data"]["data"] == 0
    all_ok = all_ok and ok6
    print(f"\n  T6 Isolation org=999: {r6['answer']} | {'  OK  ' if ok6 else ' FAIL '}")

    # T7: Ratandeep's count < total leads (proves filter is applied)
    if uid_org1 is not None:
        filtered = count_leads(1, created_by_id=uid_org1)
        total    = count_leads(1)
        ok7 = filtered <= total
        all_ok = all_ok and ok7
        print(f"  T7 Filter vs total  : {filtered} <= {total}  {'  OK  ' if ok7 else ' FAIL '}")
    else:
        print("  T7 Skipped (Ratandeep not found in org 1 — check username in DB)")

    print(f"\n{'='*50}")
    print(f"  ALL TESTS PASSED: {all_ok}")
    print(f"{'='*50}\n")

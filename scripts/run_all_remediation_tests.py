import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

suites = [
    ("OMS Status State Machine", "tests.test_oms_status_architecture"),
    ("OMS Order 360 Profile", "tests.test_oms_order_360_profile"),
    ("OMS Order Dashboard", "tests.test_oms_order_tracking_dashboard"),
    ("Master Order Creation", "tests.test_oms_master_order_creation"),
    ("OMS Core Models & Relationships", "tests.test_oms_phase1_models"),
    ("OMS Phase 2 Lifecycle Services", "tests.test_oms_phase2_services"),
    ("OMS Parallel & Partial Workflows", "tests.test_oms_parallel_and_partial_workflows"),
    ("Billing-Disabled Standalone Mode", "tests.test_billing_disabled_mode"),
    ("PhonePe Amount Mismatch Protection", "tests.test_phonepe_amount_mismatch"),
    ("PhonePe AutoPay V2 Reconciliation", "tests.test_phonepe_v2_renewal_reconciliation"),
    ("SaaS CSRF Webhook & Endpoint Security", "tests.test_saas_csrf_security"),
    ("Private Upload Storage & Tenant Scoping", "tests.test_secure_document_storage"),
]

results = []
grand_total = 0
grand_passed = 0
grand_failed = 0
grand_errors = 0

for label, mod_name in suites:
    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    suite = unittest.TestLoader().loadTestsFromName(mod_name)
    res = runner.run(suite)
    passed = res.testsRun - len(res.failures) - len(res.errors)
    failed = len(res.failures)
    errors = len(res.errors)
    grand_total += res.testsRun
    grand_passed += passed
    grand_failed += failed
    grand_errors += errors
    results.append((label, mod_name, res.testsRun, passed, failed, errors))

header = f"{'Suite / Feature Area':<42} {'Tests':>6} {'Pass':>6} {'Fail':>6} {'Error':>6} {'Rate':>8}"
divider = "-" * len(header)
banner = "=" * len(header)

print("\n" + banner)
print(header)
print(divider)
for label, mod, total, passed, failed, errors in results:
    rate = f"{(passed/total)*100:.0f}%" if total > 0 else "N/A"
    print(f"{label:<42} {total:>6} {passed:>6} {failed:>6} {errors:>6} {rate:>8}")
print(divider)
pct = (grand_passed / grand_total) * 100 if grand_total > 0 else 0
print(f"{'GRAND TOTAL':<42} {grand_total:>6} {grand_passed:>6} {grand_failed:>6} {grand_errors:>6} {pct:>7.1f}%")
print(banner)

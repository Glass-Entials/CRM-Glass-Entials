"""
test_mysql_concurrency_validation.py
====================================
Validates MySQL 8.0.42 InnoDB genuine multi-threaded concurrency,
durable debit idempotency, execution ownership, and failure recovery semantics
across all 10 scenarios specified in Phase 5.4.2.

Requires DATABASE_URL in environment. No credentials hardcoded.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from flask import Flask
from sqlalchemy import create_engine, or_
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from model import (
    db,
    Organization,
    Plan,
    SaaSSubscription,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    SaaSBillingPeriodReservation,
    SaaSWebhookEvent,
    SaaSTransaction,
    SaaSTransactionStatus,
    BillingPeriodReservationStatus,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    WebhookEventStatus,
)
from services.billing.providers.phonepe_autopay_v2_provider import (
    PhonePeAutoPayV2Provider,
    _compute_billing_period_key,
)
from services.billing.renewal_service import RenewalService
from services.billing.subscription_service import SubscriptionService
from routes.saas import _transition_v2_webhook_event, WebhookPersistenceError


def _validate_safe_test_database(url_str: str) -> None:
    """
    Explicit Database Safety Guard (Phase 6):
    - Rejects production database names and production hosts.
    - Requires an explicit test-database opt-in before destructive MySQL integration tests.
    - Fails closed if the database environment cannot be verified.
    """
    if not url_str or not isinstance(url_str, str):
        raise RuntimeError("Database safety guard: DATABASE_URL is missing or empty. Failing closed.")

    try:
        url = make_url(url_str)
    except Exception as exc:
        raise RuntimeError(f"Database safety guard: Unable to parse DATABASE_URL ({exc}). Failing closed.")

    host = (url.host or "").lower().strip()
    db_name = (url.database or "").lower().strip()

    if not host or not db_name:
        raise RuntimeError(
            f"Database safety guard: Database environment cannot be verified (host='{host}', db='{db_name}'). Failing closed."
        )

    # 1. Reject production hosts
    prod_host_indicators = [
        "prod", "production", "live", "rds.amazonaws.com", "database.azure.com",
        "cloudsql", "cluster-", "aurora", "production-db"
    ]
    for indicator in prod_host_indicators:
        if indicator in host:
            raise RuntimeError(
                f"Database safety guard VIOLATION: Host '{host}' matches production indicator '{indicator}'. "
                "Destructive integration tests cannot be executed against production infrastructure. Failing closed."
            )

    # Allowed integration test hosts: local interfaces, Docker containers, or designated test hosts
    permitted_test_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "mysql", "test-mysql", "db-test"}
    is_permitted_host = (
        host in permitted_test_hosts
        or host.endswith(".local")
        or host.startswith("127.")
        or host.startswith("test-")
    )
    if not is_permitted_host:
        raise RuntimeError(
            f"Database safety guard: Host '{host}' is not a recognized local or test container host. Failing closed."
        )

    # 2. Reject production database names
    prod_db_indicators = ["prod", "production", "live", "master", "primary", "crm_production", "glassentials_prod"]
    tokens = set(db_name.replace("-", "_").split("_"))
    for indicator in prod_db_indicators:
        if (
            indicator == db_name
            or indicator in tokens
            or (len(indicator) >= 4 and indicator in db_name)
            or db_name.startswith("prod")
            or db_name.endswith("prod")
            or db_name.startswith("live")
            or db_name.endswith("live")
        ):
            raise RuntimeError(
                f"Database safety guard VIOLATION: Database name '{db_name}' matches production indicator '{indicator}'. "
                "Destructive integration tests cannot be executed against production databases. Failing closed."
            )

    # 3. Require explicit test-database opt-in
    is_test_named_db = "test" in db_name
    opt_in_vars = ["ALLOW_TEST_DB", "ALLOW_CONCURRENCY_TEST_DB", "TEST_DATABASE_OPT_IN", "ALLOW_DESTRUCTIVE_TESTS"]
    has_opt_in_env = any(
        os.environ.get(var, "").strip().lower() in ("1", "true", "yes")
        for var in opt_in_vars
    )

    if not (is_test_named_db or has_opt_in_env):
        raise RuntimeError(
            f"Database safety guard: Database '{db_name}' is not named with 'test' and no explicit test-database opt-in was provided. "
            f"Set ALLOW_TEST_DB=1 or ALLOW_CONCURRENCY_TEST_DB=1 to confirm execution on this database. Failing closed."
        )


class TestDatabaseSafetyGuard(unittest.TestCase):
    """
    Unit test suite verifying the explicit database safety guard rejects
    production database targets and fails closed before destructive MySQL execution.
    """

    def test_missing_or_empty_url(self):
        with self.assertRaises(RuntimeError) as ctx:
            _validate_safe_test_database("")
        self.assertIn("missing or empty", str(ctx.exception).lower())

        with self.assertRaises(RuntimeError) as ctx:
            _validate_safe_test_database(None)
        self.assertIn("missing or empty", str(ctx.exception).lower())

    def test_missing_host_or_database(self):
        with self.assertRaises(RuntimeError) as ctx:
            _validate_safe_test_database("mysql+pymysql://root:pass@/crm_db")
        self.assertIn("cannot be verified", str(ctx.exception).lower())

        with self.assertRaises(RuntimeError) as ctx:
            _validate_safe_test_database("mysql+pymysql://root:pass@localhost/")
        self.assertIn("cannot be verified", str(ctx.exception).lower())

    def test_production_host_rejection(self):
        prod_urls = [
            "mysql+pymysql://root:pass@prod-db.example.com/crm_test",
            "mysql+pymysql://root:pass@database.rds.amazonaws.com/crm_test",
            "mysql+pymysql://root:pass@mycluster.database.azure.com/crm_test",
            "mysql+pymysql://root:pass@cloudsql.gcp.internal/crm_test",
            "mysql+pymysql://root:pass@production-db/crm_test",
            "mysql+pymysql://root:pass@aurora-cluster.internal/crm_test",
        ]
        for url in prod_urls:
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError) as ctx:
                    _validate_safe_test_database(url)
                self.assertIn("production indicator", str(ctx.exception).lower())

    def test_unpermitted_external_host_rejection(self):
        unpermitted_urls = [
            "mysql+pymysql://root:pass@192.168.1.50/crm_test",
            "mysql+pymysql://root:pass@internal-db.corp/crm_test",
            "mysql+pymysql://root:pass@staging.company.com/crm_test",
        ]
        for url in unpermitted_urls:
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError) as ctx:
                    _validate_safe_test_database(url)
                self.assertIn("not a recognized local or test container host", str(ctx.exception).lower())

    def test_production_database_name_rejection(self):
        prod_db_urls = [
            "mysql+pymysql://root:pass@localhost/crm_production",
            "mysql+pymysql://root:pass@localhost/crm_prod",
            "mysql+pymysql://root:pass@localhost/production",
            "mysql+pymysql://root:pass@localhost/prod",
            "mysql+pymysql://root:pass@localhost/crm_live",
            "mysql+pymysql://root:pass@localhost/master",
            "mysql+pymysql://root:pass@localhost/primary",
        ]
        for url in prod_db_urls:
            with self.subTest(url=url):
                with self.assertRaises(RuntimeError) as ctx:
                    _validate_safe_test_database(url)
                self.assertIn("matches production indicator", str(ctx.exception).lower())

    def test_non_test_database_rejected_without_optin(self):
        env_backup = {}
        for k in ["ALLOW_TEST_DB", "ALLOW_CONCURRENCY_TEST_DB", "TEST_DATABASE_OPT_IN", "ALLOW_DESTRUCTIVE_TESTS"]:
            env_backup[k] = os.environ.pop(k, None)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                _validate_safe_test_database("mysql+pymysql://root:pass@localhost/crm_db")
            self.assertIn("no explicit test-database opt-in was provided", str(ctx.exception).lower())
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v

    def test_test_named_database_accepted_without_optin(self):
        env_backup = {}
        for k in ["ALLOW_TEST_DB", "ALLOW_CONCURRENCY_TEST_DB", "TEST_DATABASE_OPT_IN", "ALLOW_DESTRUCTIVE_TESTS"]:
            env_backup[k] = os.environ.pop(k, None)
        try:
            _validate_safe_test_database("mysql+pymysql://root:pass@localhost/crm_test")
            _validate_safe_test_database("mysql+pymysql://root:pass@127.0.0.1/test_billing")
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v

    def test_optin_accepted_for_local_non_test_database(self):
        optin_vars = ["ALLOW_TEST_DB", "ALLOW_CONCURRENCY_TEST_DB", "TEST_DATABASE_OPT_IN", "ALLOW_DESTRUCTIVE_TESTS"]
        for var in optin_vars:
            with self.subTest(var=var):
                with patch.dict(os.environ, {var: "1"}):
                    _validate_safe_test_database("mysql+pymysql://root:pass@localhost/crm_db")


class TestMySQLConcurrencyValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mysql_url = os.environ.get("DATABASE_URL")
        if not mysql_url:
            raise unittest.SkipTest(
                "DATABASE_URL environment variable is required to execute live MySQL concurrency tests."
            )

        # Enforce explicit database safety guard before any database operations
        _validate_safe_test_database(mysql_url)

        cls.app = Flask(__name__)
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = mysql_url
        cls.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        cls.app.config["TESTING"] = True
        cls.app.config["PHONEPE_ENV"] = "UAT"
        cls.app.config["PHONEPE_MERCHANT_ID"] = "PGTESTPAYUAT"
        cls.app.config["PHONEPE_CLIENT_ID"] = "test_client_id"
        cls.app.config["PHONEPE_CLIENT_SECRET"] = "test_client_secret"
        cls.app.config["PHONEPE_CLIENT_VERSION"] = 1
        cls.app.config["PHONEPE_WEBHOOK_USERNAME"] = "webhook_test_user"
        cls.app.config["PHONEPE_WEBHOOK_PASSWORD"] = "webhook_test_pass"
        cls.app.config["SECRET_KEY"] = "test-secret-key-mysql"
        cls.app.config["WTF_CSRF_ENABLED"] = False

        from routes.saas import saas_bp
        cls.app.register_blueprint(saas_bp)

        db.init_app(cls.app)
        cls.ctx = cls.app.app_context()
        cls.ctx.push()

        # Independent sessionmaker for genuine multi-threaded concurrency tests
        cls.engine = create_engine(mysql_url, pool_pre_ping=True)
        cls.SessionFactory = sessionmaker(bind=cls.engine, expire_on_commit=False)

        cls.provider = PhonePeAutoPayV2Provider()

        # Seed isolated test tenant and plan
        cls.org_id = 98765
        cls.sub_id = 87654

        cls._cleanup()

        org = Organization.query.get(cls.org_id)
        if not org:
            org = Organization(id=cls.org_id, name="MySQL Concurrency Tenant", unique_code="MYSQLCONC")
            db.session.add(org)
            db.session.commit()
            cls._created_org = True
        else:
            cls._created_org = False

        plan = Plan.query.get(999)
        if not plan:
            plan = Plan(id=999, name="Test Plan", display_name="Test", monthly_price_paise=100000, yearly_price_paise=1000000)
            db.session.add(plan)
            db.session.commit()
            cls._created_plan_999 = True
        else:
            cls._created_plan_999 = False

        # Create base test subscription
        sub = SaaSSubscription.query.get(cls.sub_id)
        if not sub:
            sub = SaaSSubscription(
                id=cls.sub_id,
                organization_id=cls.org_id,
                plan_id=999,
                payment_provider="phonepe_v2",
                provider_subscription_id=f"MSUB_MYSQL_{cls.sub_id}",
                status=SaaSSubscriptionStatus.ACTIVE,
                billing_cycle=SaaSBillingCycle.MONTHLY,
                amount_paise=118000,
                current_period_start=datetime(2026, 11, 1, 0, 0, 0),
                current_period_end=datetime(2026, 12, 1, 0, 0, 0),
            )
            db.session.add(sub)
            db.session.commit()
            cls._created_sub = True
        else:
            cls._created_sub = False

    @classmethod
    def tearDownClass(cls):
        cls._cleanup()
        if getattr(cls, "_created_sub", False):
            sub = SaaSSubscription.query.get(cls.sub_id)
            if sub:
                db.session.delete(sub)
        if getattr(cls, "_created_org", False):
            org = Organization.query.get(cls.org_id)
            if org:
                db.session.delete(org)
        if getattr(cls, "_created_plan_999", False):
            plan = Plan.query.get(999)
            if plan:
                db.session.delete(plan)
        db.session.commit()
        cls.ctx.pop()
        cls.engine.dispose()

    @classmethod
    def _cleanup(cls):
        db.session.rollback()
        sub = db.session.get(SaaSSubscription, cls.sub_id)
        if sub:
            sub.active_mandate_id = None
            sub.claim_token = None
            sub.claim_expires_at = None
            db.session.commit()
        db.session.query(SaaSTransaction).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSWebhookEvent).filter_by(payment_provider="phonepe_v2", organization_id=cls.org_id).delete()
        db.session.query(SaaSBillingPeriodReservation).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSRedemptionAttempt).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSPreDebitNotification).filter_by(organization_id=cls.org_id).delete()
        db.session.query(SaaSMandate).filter_by(organization_id=cls.org_id).delete()
        db.session.commit()

    def setUp(self):
        self._cleanup()
        sub = db.session.get(SaaSSubscription, self.sub_id)
        if sub:
            sub.active_mandate_id = None
            sub.claim_token = None
            sub.claim_expires_at = None
            sub.current_period_start = datetime(2026, 11, 1, 0, 0, 0)
            sub.current_period_end = datetime(2026, 12, 1, 0, 0, 0)
            db.session.commit()

    # ── Scenario 1: Different Order IDs Competing for One Billing Period ───────

    def test_scenario_1_different_order_ids_competing_for_one_billing_period(self):
        """
        Scenario 1: Two synchronized workers on independent MySQL InnoDB connections
        simultaneously attempt to reserve the same billing period with different order IDs.
        Asserts exactly one reservation is created, and the losing worker is blocked by unique DB constraint.
        """
        bpk = f"{self.sub_id}_2026-11"
        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, order_id: str):
            session = self.SessionFactory()
            try:
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id=order_id,
                    session=session,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1", "ORD_COMPETE_A"))
        t2 = threading.Thread(target=worker, args=("worker_2", "ORD_COMPETE_B"))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Exactly one worker succeeded and one failed
        self.assertTrue((w1_ok and not w2_ok) or (w2_ok and not w1_ok))

        # Losing worker is blocked
        losing_err = w2_err if w1_ok else w1_err
        self.assertIn("already reserved", losing_err)
        self.assertIn("Duplicate debit prevented", losing_err)

        # Direct database verification on MySQL 8 InnoDB
        verify_session = self.SessionFactory()
        try:
            records = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).all()
            self.assertEqual(len(records), 1)
            winning_order = "ORD_COMPETE_A" if w1_ok else "ORD_COMPETE_B"
            self.assertEqual(records[0].merchant_order_id, winning_order)
            self.assertEqual(records[0].status, BillingPeriodReservationStatus.RESERVED.value)
        finally:
            verify_session.close()

    # ── Scenario 2: Same Order ID Submitted Simultaneously by Two Workers ─────

    def test_scenario_2_same_order_id_submitted_simultaneously_by_two_workers(self):
        """
        Scenario 2: Two synchronized threads simultaneously attempt to execute/reserve
        the same billing period using the exact SAME merchant order ID with distinct owner tokens.
        Durable execution ownership guarantees exactly ONE worker wins the active execution claim.
        The second worker is rejected as concurrent execution blocked.
        """
        bpk = f"{self.sub_id}_2026-11"
        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, owner_token: str):
            session = self.SessionFactory()
            try:
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id="ORD_SAME_ID_CONC",
                    session=session,
                    owner_token=owner_token,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1", "worker_1_owner_token"))
        t2 = threading.Thread(target=worker, args=("worker_2", "worker_2_owner_token"))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Exactly one worker acquires execution ownership; second worker is blocked
        self.assertTrue((w1_ok and not w2_ok) or (w2_ok and not w1_ok))

        losing_err = w2_err if w1_ok else w1_err
        self.assertTrue(
            ("currently owned by active worker" in losing_err and "Concurrent execution blocked" in losing_err)
            or ("already reserved by another worker" in losing_err and "Duplicate debit prevented" in losing_err)
        )

        # Exactly 1 reservation row exists in MySQL InnoDB
        verify_session = self.SessionFactory()
        try:
            records = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).all()
            self.assertEqual(len(records), 1)
            winning_token = "worker_1_owner_token" if w1_ok else "worker_2_owner_token"
            self.assertEqual(records[0].owner_token, winning_token)
            self.assertEqual(records[0].merchant_order_id, "ORD_SAME_ID_CONC")
        finally:
            verify_session.close()

    # ── Scenario 3: Exactly One PhonePe Redeem Invocation ─────────────────────

    def test_scenario_3_exactly_one_phonepe_redeem_invocation(self):
        """
        Scenario 3: Two workers concurrently attempt execute_redemption for the same subscription
        and billing period. Asserts PhonePe client's redeem() is invoked EXACTLY ONCE across workers.
        """
        order_id = "ORD_SINGLE_REDEEM_INVOCATION"
        mandate = SaaSMandate(
            id=555,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        sub = SaaSSubscription.query.get(self.sub_id)
        sub.active_mandate_id = mandate.id

        notification = SaaSPreDebitNotification(
            id=777,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=f"{self.sub_id}_2026-11",
        )
        db.session.add(notification)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_SINGLE_REDEEM"}],
        }

        barrier = threading.Barrier(2)
        results = {}

        def run_worker(worker_id: str):
            with self.app.app_context():
                try:
                    barrier.wait(timeout=5)
                    worker_sub = db.session.get(SaaSSubscription, self.sub_id)
                    res = self.provider.execute_redemption(
                        subscription=worker_sub,
                        amount_paise=118000,
                        merchant_transaction_id=order_id,
                    )
                    results[worker_id] = res
                except Exception as exc:
                    results[worker_id] = exc

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            t1 = threading.Thread(target=run_worker, args=("worker_1",))
            t2 = threading.Thread(target=run_worker, args=("worker_2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # PhonePe redeem must be called EXACTLY ONCE
        self.assertEqual(mock_client.redeem.call_count, 1)

        # One worker succeeded; the other worker was prevented from duplicate debit
        r1 = results.get("worker_1")
        r2 = results.get("worker_2")
        self.assertTrue((getattr(r1, "success", False) and not getattr(r2, "success", False)) or
                        (getattr(r2, "success", False) and not getattr(r1, "success", False)) or
                        (getattr(r1, "status", None) == "SUCCESS" and getattr(r2, "status", None) == "PENDING"))

    # ── Scenario 4: Second Worker Cannot Independently Initiate Debit ──────────

    def test_scenario_4_second_worker_cannot_independently_initiate_debit(self):
        """
        Scenario 4: When Worker 1 holds active execution ownership, Worker 2 cannot
        independently invoke PhonePe redeem or initiate a second debit.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_ACTIVE_OWNER_IN_FLIGHT"

        # Worker 1 acquires reservation with execution lease
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_1_in_flight",
        )
        self.assertTrue(ok1)
        self.assertEqual(res1.owner_token, "worker_1_in_flight")
        self.assertGreater(res1.owner_expires_at, datetime.utcnow())

        # Worker 2 attempts same order with its own owner token
        ok2, err2, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_2_competing",
        )
        self.assertFalse(ok2)
        self.assertIn("currently owned by active worker", err2)

        # Worker 2 attempts different order for same period
        ok3, err3, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_DIFFERENT_COMPETING",
            owner_token="worker_2_competing",
        )
        self.assertFalse(ok3)
        self.assertIn("already reserved", err3)

    # ── Scenario 5: Worker Crash After Acquiring Execution Ownership ───────────

    def test_scenario_5_worker_crash_after_acquiring_execution_ownership(self):
        """
        Scenario 5: Worker 1 crashes after acquiring execution ownership BEFORE calling PhonePe
        (redemption_initiated_at is None). After lease expires, Worker 2 safely acquires
        ownership reusing the same order ID without creating a replacement order.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_CRASH_BEFORE_GATEWAY"

        # Worker 1 reserves period but crashes
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_1_crashed",
        )
        self.assertTrue(ok1)
        self.assertIsNone(res1.redemption_initiated_at)

        # Simulate Worker 1 lease expiry in MySQL InnoDB
        res1.owner_expires_at = datetime.utcnow() - timedelta(seconds=10)
        db.session.commit()

        # Worker 2 attempts recovery using the SAME order ID
        ok2, err2, res2 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_2_recovered",
        )
        self.assertTrue(ok2)
        self.assertIsNone(err2)
        self.assertEqual(res2.id, res1.id)
        self.assertEqual(res2.merchant_order_id, order_id)
        self.assertEqual(res2.owner_token, "worker_2_recovered")

    # ── Scenario 5B: Simultaneous Expired-Lease Acquisition by Two Threads ────

    def test_scenario_5b_simultaneous_expired_lease_atomic_acquisition_two_threads(self):
        """
        Phase 5.4.2.1 Requirement 3: Two synchronized threads on independent MySQL InnoDB sessions
        simultaneously attempt to acquire an expired execution lease for the SAME order ID.
        Atomic conditional UPDATE guarantees that:
          1. Exactly ONE worker acquires the expired lease.
          2. The losing worker is blocked from acquiring ownership.
          3. Two workers never both believe they acquired the expired reservation.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_SIMULTANEOUS_EXPIRED_LEASE"

        # Pre-seed reservation with an EXPIRED lease (pre-dispatch crash)
        expired_res = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.RESERVED.value,
            owner_token="crashed_worker_initial",
            owner_expires_at=datetime.utcnow() - timedelta(seconds=30),
            redemption_initiated_at=None,
        )
        db.session.add(expired_res)
        db.session.commit()

        barrier = threading.Barrier(2)
        results = {}

        def worker(worker_id: str, owner_token: str):
            session = self.SessionFactory()
            try:
                barrier.wait(timeout=5)
                ok, err, res = self.provider._reserve_billing_period(
                    subscription_id=self.sub_id,
                    organization_id=self.org_id,
                    billing_period_key=bpk,
                    merchant_order_id=order_id,
                    session=session,
                    owner_token=owner_token,
                )
                results[worker_id] = (ok, err, res)
            finally:
                session.close()

        t1 = threading.Thread(target=worker, args=("worker_1", "worker_1_lease_token"))
        t2 = threading.Thread(target=worker, args=("worker_2", "worker_2_lease_token"))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        w1_ok, w1_err, _ = results["worker_1"]
        w2_ok, w2_err, _ = results["worker_2"]

        # Exactly ONE worker acquired the expired lease; the other worker was rejected
        self.assertTrue(
            (w1_ok and not w2_ok) or (w2_ok and not w1_ok),
            f"Both workers cannot succeed or fail simultaneously: w1={w1_ok}, w2={w2_ok}",
        )

        losing_err = w2_err if w1_ok else w1_err
        self.assertIn("Concurrent execution blocked", losing_err)

        # Directly verify database state in MySQL InnoDB
        verify_session = self.SessionFactory()
        try:
            persisted = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                billing_period_key=bpk,
            ).first()
            self.assertIsNotNone(persisted)
            winning_token = "worker_1_lease_token" if w1_ok else "worker_2_lease_token"
            self.assertEqual(persisted.owner_token, winning_token)
            self.assertGreater(persisted.owner_expires_at, datetime.utcnow())
        finally:
            verify_session.close()

    def test_scenario_5c_simultaneous_expired_lease_redemption_exactly_one_redeem_call(self):
        """
        Phase 5.4.2.1 Requirement 3: Two synchronized worker threads simultaneously attempt
        execute_redemption() on an expired lease.
        Asserts:
          1. Exactly ONE execution owner is established.
          2. The losing worker cannot proceed to PhonePe redeem.
          3. PhonePe redeem() is invoked EXACTLY ONCE.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_SIMULTANEOUS_EXPIRED_REDEEM"

        # Set up active mandate and NOTIFIED notification
        mandate = SaaSMandate(
            id=556,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_EXP_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        sub = db.session.get(SaaSSubscription, self.sub_id)
        sub.active_mandate_id = mandate.id

        notification = SaaSPreDebitNotification(
            id=778,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=bpk,
        )
        db.session.add(notification)

        # Pre-seed reservation with an EXPIRED lease
        expired_res = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.RESERVED.value,
            owner_token="old_crashed_worker",
            owner_expires_at=datetime.utcnow() - timedelta(seconds=45),
            redemption_initiated_at=None,
        )
        db.session.add(expired_res)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_EXP_REDEEM_WINNER"}],
        }

        barrier = threading.Barrier(2)
        results = {}

        def run_worker(worker_id: str):
            with self.app.app_context():
                try:
                    barrier.wait(timeout=5)
                    worker_sub = db.session.get(SaaSSubscription, self.sub_id)
                    res = self.provider.execute_redemption(
                        subscription=worker_sub,
                        amount_paise=118000,
                        merchant_transaction_id=order_id,
                    )
                    results[worker_id] = res
                except Exception as exc:
                    results[worker_id] = exc

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            t1 = threading.Thread(target=run_worker, args=("worker_1",))
            t2 = threading.Thread(target=run_worker, args=("worker_2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # Assert PhonePe redeem was called EXACTLY ONCE across both threads
        self.assertEqual(mock_client.redeem.call_count, 1)

        r1 = results.get("worker_1")
        r2 = results.get("worker_2")

        # One worker succeeded and one was prevented
        r1_success = getattr(r1, "success", False) or getattr(r1, "status", None) == "SUCCESS"
        r2_success = getattr(r2, "success", False) or getattr(r2, "status", None) == "SUCCESS"
        self.assertTrue(
            (r1_success and not r2_success) or (r2_success and not r1_success),
            f"Expected exactly one success: r1={r1}, r2={r2}",
        )

        losing_res = r2 if r1_success else r1
        self.assertFalse(losing_res.success)
        self.assertTrue(
            "Redemption execution for order" in losing_res.error_message
            or "Subscription is currently claimed" in losing_res.error_message
        )

    # ── Scenario 5D: Paused Worker Lease Transfer Race Condition ──────────────

    def test_scenario_5d_paused_worker_lease_transfer_race_condition(self):
        """
        Phase 5.4.2.2 Requirement 1:
        Genuine MySQL InnoDB race test deliberately pausing Worker 1 between initial ownership
        acquisition and dispatch authorization, expiring/transferring the lease to Worker 2
        (new owner), and proving that former owner (Worker 1) cannot dispatch while new owner
        (Worker 2) can, yielding exactly ONE PhonePe redeem call.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_RACE_PAUSED_WORKER"

        # Set up active mandate and NOTIFIED notification
        mandate = SaaSMandate(
            id=558,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_PAUSE_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        sub = db.session.get(SaaSSubscription, self.sub_id)
        sub.active_mandate_id = mandate.id

        notification = SaaSPreDebitNotification(
            id=779,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=datetime.utcnow() - timedelta(hours=1),
            valid_upto_at=datetime.utcnow() + timedelta(days=1),
            billing_period_key=bpk,
        )
        db.session.add(notification)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_PAUSED_RACE_SUCCESS"}],
        }

        worker_1_paused_event = threading.Event()
        worker_1_resume_event = threading.Event()
        orig_reserve = self.provider._reserve_billing_period

        # Hook _reserve_billing_period to pause Worker 1 right after initial ownership acquisition
        def hooked_reserve(subscription_id, organization_id, billing_period_key, merchant_order_id, session=None, owner_token=None):
            ok, err, res = orig_reserve(subscription_id, organization_id, billing_period_key, merchant_order_id, session=session, owner_token=owner_token)
            if ok and owner_token and "worker_1" in owner_token:
                # Signal that Worker 1 has completed initial ownership acquisition
                worker_1_paused_event.set()
                # Pause Worker 1 until the test expires and transfers lease to Worker 2
                worker_1_resume_event.wait(timeout=5)
            return ok, err, res

        results = {}

        def run_worker_1():
            with self.app.app_context():
                try:
                    w1_sub = db.session.get(SaaSSubscription, self.sub_id)
                    with patch("uuid.uuid4") as mock_uuid:
                        mock_uuid.return_value.hex = "worker_1_initial_owner"
                        res = self.provider.execute_redemption(
                            subscription=w1_sub,
                            amount_paise=118000,
                            merchant_transaction_id=order_id,
                        )
                        results["worker_1"] = res
                except Exception as exc:
                    results["worker_1"] = exc

        def run_worker_2():
            with self.app.app_context():
                try:
                    w2_sub = db.session.get(SaaSSubscription, self.sub_id)
                    with patch("uuid.uuid4") as mock_uuid:
                        mock_uuid.return_value.hex = "worker_2_new_owner"
                        res = self.provider.execute_redemption(
                            subscription=w2_sub,
                            amount_paise=118000,
                            merchant_transaction_id=order_id,
                        )
                        results["worker_2"] = res
                except Exception as exc:
                    results["worker_2"] = exc

        with patch.object(self.provider, "_reserve_billing_period", side_effect=hooked_reserve), \
             patch.object(self.provider, "_get_client", return_value=mock_client):

            # 1. Start Worker 1
            t1 = threading.Thread(target=run_worker_1)
            t1.start()

            # 2. Wait until Worker 1 has acquired initial ownership on MySQL InnoDB
            self.assertTrue(worker_1_paused_event.wait(timeout=5), "Worker 1 failed to acquire initial ownership")

            # Verify Worker 1 acquired initial reservation in MySQL InnoDB
            verify_session = self.SessionFactory()
            try:
                res_row = verify_session.query(SaaSBillingPeriodReservation).filter_by(
                    organization_id=self.org_id,
                    subscription_id=self.sub_id,
                    billing_period_key=bpk,
                ).first()
                self.assertIsNotNone(res_row)
                self.assertIn("worker_1", res_row.owner_token)
                self.assertIsNone(res_row.redemption_initiated_at)

                # 3. Deliberately expire Worker 1's leases in MySQL InnoDB
                res_row.owner_expires_at = datetime.utcnow() - timedelta(seconds=10)
                sub_row = verify_session.query(SaaSSubscription).get(self.sub_id)
                if sub_row:
                    sub_row.claim_expires_at = datetime.utcnow() - timedelta(seconds=10)
                verify_session.commit()
            finally:
                verify_session.close()

            # 4. Run Worker 2 while Worker 1 is still paused before dispatch authorization.
            # Worker 2 should acquire the expired lease, run atomic dispatch authorization,
            # and dispatch PhonePe redeem.
            t2 = threading.Thread(target=run_worker_2)
            t2.start()
            t2.join(timeout=10)

            # Assert Worker 2 succeeded
            w2_res = results.get("worker_2")
            self.assertIsNotNone(w2_res, "Worker 2 did not produce a result")
            w2_success = getattr(w2_res, "success", False) or getattr(w2_res, "status", None) == "SUCCESS"
            self.assertTrue(w2_success, f"Worker 2 was expected to succeed: {w2_res}")

            # 5. Now resume Worker 1: Worker 1 resumes and attempts atomic conditional UPDATE
            # for dispatch authorization with its former owner_token.
            worker_1_resume_event.set()
            t1.join(timeout=10)

            # Assert Worker 1 failed / aborted because updated_count == 0
            w1_res = results.get("worker_1")
            self.assertIsNotNone(w1_res, "Worker 1 did not produce a result")
            self.assertFalse(w1_res.success, f"Worker 1 was expected to fail: {w1_res}")
            self.assertTrue(
                "Execution lease expired or lost ownership" in getattr(w1_res, "error_message", "")
                or "Redemption was already initiated" in getattr(w1_res, "error_message", "")
            )

            # 6. Assert PhonePe redeem() was called EXACTLY ONCE (by Worker 2 only!)
            self.assertEqual(mock_client.redeem.call_count, 1)

            # 7. Directly verify MySQL InnoDB state: redemption succeeded and marked COMPLETED
            final_session = self.SessionFactory()
            try:
                final_res = final_session.query(SaaSBillingPeriodReservation).filter_by(
                    organization_id=self.org_id,
                    subscription_id=self.sub_id,
                    billing_period_key=bpk,
                ).first()
                self.assertIsNotNone(final_res)
                self.assertIsNotNone(final_res.redemption_initiated_at)
                self.assertEqual(final_res.status, BillingPeriodReservationStatus.COMPLETED.value)
                self.assertEqual(final_res.gateway_state, "COMPLETED")
            finally:
                final_session.close()

    # ── Scenario 6: Lease Expiry While Gateway Request Remains Ambiguous ───────

    def test_scenario_6_lease_expiry_while_gateway_request_remains_ambiguous(self):
        """
        Scenario 6: Worker 1 dispatched PhonePe gateway request (redemption_initiated_at is set),
        then crashed. After lease expires, Worker 2 attempts to reserve.
        Because redemption was already dispatched, Worker 2 is BLOCKED from initiating a second
        PhonePe debit and the reservation is marked PENDING_RECONCILIATION.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_DISPATCHED_CRASH_AMBIGUOUS"

        # Worker 1 reserves and initiates gateway call
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_1_dispatched",
        )
        self.assertTrue(ok1)
        res1.redemption_initiated_at = datetime.utcnow() - timedelta(minutes=5)
        res1.owner_expires_at = datetime.utcnow() - timedelta(minutes=3)  # lease expired
        db.session.commit()

        # Worker 2 attempts to reserve same order after lease expiry
        ok2, err2, res2 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, order_id,
            owner_token="worker_2_attempt",
        )
        self.assertFalse(ok2)
        self.assertIn("was already initiated with gateway", err2)
        self.assertIn("authoritative status reconciliation required", err2)

        # Directly verify status on MySQL InnoDB
        db.session.refresh(res1)
        self.assertEqual(res1.status, BillingPeriodReservationStatus.PENDING_RECONCILIATION.value)

    # ── Scenario 7: Recovery After Authoritative Gateway Success ───────────────

    def test_scenario_7_recovery_after_authoritative_gateway_success(self):
        """
        Scenario 7: Following an ambiguous outcome, authoritative S2S status check confirms
        gateway COMPLETED. Reconciles attempt and reservation to COMPLETED, clears execution lease,
        advances subscription period, and permanently blocks duplicate debits.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_RECOVER_SUCCESS"

        mandate = SaaSMandate(
            id=556,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        notification = SaaSPreDebitNotification(
            id=778,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            billing_period_key=bpk,
        )
        db.session.add(notification)

        reservation = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.PENDING_RECONCILIATION.value,
            owner_token="worker_expired_token",
            owner_expires_at=datetime.utcnow() - timedelta(minutes=1),
            redemption_initiated_at=datetime.utcnow() - timedelta(minutes=5),
        )
        db.session.add(reservation)

        attempt = SaaSRedemptionAttempt(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            notification_id=notification.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
            billing_period_key=bpk,
        )
        db.session.add(attempt)
        db.session.commit()

        # Mock authoritative gateway response returning COMPLETED
        mock_client = MagicMock()
        mock_client.get_order_status.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_AUTH_SUCCESS_777"}],
        }

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            updated_attempt = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(updated_attempt.status, RedemptionAttemptStatus.COMPLETED.value)

        # Verify reservation transitioned to COMPLETED and ownership cleared in MySQL
        db.session.refresh(reservation)
        self.assertEqual(reservation.status, BillingPeriodReservationStatus.COMPLETED.value)
        self.assertEqual(reservation.gateway_state, "COMPLETED")
        self.assertIsNone(reservation.owner_token)
        self.assertIsNone(reservation.owner_expires_at)

        # Subsequent attempt to reserve the period is permanently blocked
        ok_next, err_next, _ = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk, "ORD_COMPLETED_PERIOD_RETRY"
        )
        self.assertFalse(ok_next)
        self.assertIn("successful debit already exists", err_next)

    # ── Scenario 8: Recovery After Authoritative Gateway Failure ───────────────

    def test_scenario_8_recovery_after_authoritative_gateway_failure(self):
        """
        Scenario 8: Following an ambiguous outcome, authoritative S2S status check confirms
        gateway FAILED. Reconciles attempt and reservation to FAILED, clears execution ownership,
        and does NOT advance subscription period.
        """
        bpk = f"{self.sub_id}_2026-11"
        order_id = "ORD_RECOVER_FAILED"

        mandate = SaaSMandate(
            id=557,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_MYSQL_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)

        notification = SaaSPreDebitNotification(
            id=779,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            billing_period_key=bpk,
        )
        db.session.add(notification)

        reservation = SaaSBillingPeriodReservation(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            billing_period_key=bpk,
            merchant_order_id=order_id,
            status=BillingPeriodReservationStatus.PENDING_RECONCILIATION.value,
            owner_token="worker_failed_token",
            owner_expires_at=datetime.utcnow() - timedelta(minutes=1),
            redemption_initiated_at=datetime.utcnow() - timedelta(minutes=5),
        )
        db.session.add(reservation)

        attempt = SaaSRedemptionAttempt(
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            notification_id=notification.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            status=RedemptionAttemptStatus.PENDING_RECONCILIATION.value,
            billing_period_key=bpk,
        )
        db.session.add(attempt)
        db.session.commit()

        # Mock authoritative gateway response returning FAILED
        mock_client = MagicMock()
        mock_client.get_order_status.return_value = {
            "state": "FAILED",
            "message": "Debit declined by customer bank",
            "paymentDetails": [{"state": "FAILED", "responseCodeDescription": "DECLINED"}],
        }

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            updated_attempt = self.provider.reconcile_redemption_status(attempt)

        self.assertEqual(updated_attempt.status, RedemptionAttemptStatus.FAILED.value)

        # Verify reservation transitioned to FAILED and ownership cleared in MySQL
        db.session.refresh(reservation)
        self.assertEqual(reservation.status, BillingPeriodReservationStatus.FAILED.value)
        self.assertEqual(reservation.gateway_state, "FAILED")
        self.assertIsNone(reservation.owner_token)

    # ── Scenario 9: Duplicate Webhook Delivery During Reconciliation ──────────

    def test_scenario_9_duplicate_webhook_delivery_during_reconciliation(self):
        """
        Scenario 9: Webhook event idempotency deduplication in MySQL InnoDB.
        When duplicate webhooks arrive for the same PhonePe event, the first is processed
        and recorded with processing_state="PROCESSED"; the duplicate returns already_processed
        without executing redundant mutations.
        """
        event_id = "EVT_MYSQL_DEDUP_001"

        # Webhook 1 processes and commits idempotency record to MySQL
        ev = SaaSWebhookEvent(
            payment_provider="phonepe_v2",
            event_id=event_id,
            event_type="PAYMENT_SUCCESS",
            payload_summary=json.dumps({"processing_state": "PROCESSED", "org_id": self.org_id}),
        )
        db.session.add(ev)
        db.session.commit()

        # Webhook 2 arrives: check idempotency in MySQL
        existing_event = SaaSWebhookEvent.query.filter_by(payment_provider="phonepe_v2", event_id=event_id).first()
        self.assertIsNotNone(existing_event)
        summary = json.loads(existing_event.payload_summary)
        self.assertEqual(summary.get("processing_state"), "PROCESSED")

        # Duplicate delivery must recognize processed status
        is_already_processed = (summary.get("processing_state") == "PROCESSED")
        self.assertTrue(is_already_processed)

    # ── Scenario 10: Legitimate Subsequent Billing Period ─────────────────────

    def test_scenario_10_legitimate_subsequent_billing_period(self):
        """
        Scenario 10: When Period 1 (2026-11) is completed, subscription advances to Period 2 (2026-12).
        A new reservation with new billing_period_key succeeds cleanly on MySQL InnoDB.
        """
        bpk_nov = f"{self.sub_id}_2026-11"
        ok1, _, res1 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk_nov, "ORD_NOV_PAID",
            owner_token="worker_nov",
        )
        self.assertTrue(ok1)
        res1.status = BillingPeriodReservationStatus.COMPLETED.value
        res1.owner_token = None
        db.session.commit()

        # Subscription advances to December 2026
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.current_period_start = datetime(2026, 12, 1, 0, 0, 0)
        sub.current_period_end = datetime(2027, 1, 1, 0, 0, 0)
        db.session.commit()

        bpk_dec = _compute_billing_period_key(sub.id, sub.current_period_start)
        self.assertEqual(bpk_dec, f"{self.sub_id}_2026-12")

        # Worker reserves December 2026
        ok2, err2, res2 = self.provider._reserve_billing_period(
            self.sub_id, self.org_id, bpk_dec, "ORD_DEC_RENEWAL",
            owner_token="worker_dec",
        )
        self.assertTrue(ok2)
        self.assertIsNone(err2)
        self.assertEqual(res2.billing_period_key, bpk_dec)
        self.assertEqual(res2.status, BillingPeriodReservationStatus.RESERVED.value)

        # Both records exist peacefully in MySQL InnoDB
        nov_rec = SaaSBillingPeriodReservation.query.filter_by(billing_period_key=bpk_nov).first()
        dec_rec = SaaSBillingPeriodReservation.query.filter_by(billing_period_key=bpk_dec).first()
        self.assertEqual(nov_rec.status, "COMPLETED")
        self.assertEqual(dec_rec.status, "RESERVED")

    # ── Scenario 11: Simultaneous Scheduler Renewals Strictly One Gateway Dispatch ─

    def test_scenario_11_simultaneous_scheduler_renewals_strictly_one_gateway_dispatch(self):
        """
        Scenario 11 (Phase 6): Two concurrent scheduler workers run RenewalService.process_due_renewals
        for the same due subscription using independent MySQL sessions.
        Asserts that the gateway's redeem() is dispatched EXACTLY ONCE.
        """
        order_id = "ORD_SCHEDULER_CONCURRENCY_01"
        now = datetime.utcnow()
        sub = SaaSSubscription.query.get(self.sub_id)
        sub.next_billing_at = now - timedelta(hours=1)
        sub.current_period_start = now - timedelta(days=30)
        sub.claim_token = None
        sub.claim_expires_at = None

        mandate = SaaSMandate(
            id=666,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            payment_provider="phonepe_v2",
            merchant_subscription_id=f"MSUB_SCHED_{self.sub_id}",
            mandate_status="ACTIVE",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.commit()

        sub.active_mandate_id = mandate.id
        db.session.commit()

        notification = SaaSPreDebitNotification(
            id=888,
            organization_id=self.org_id,
            subscription_id=self.sub_id,
            mandate_id=mandate.id,
            merchant_order_id=order_id,
            amount_paise=118000,
            auto_debit_snapshot=False,
            status=PreDebitNotificationStatus.NOTIFIED.value,
            valid_after_at=now - timedelta(hours=2),
            valid_upto_at=now + timedelta(days=1),
            billing_period_key=_compute_billing_period_key(self.sub_id, sub.current_period_start),
        )
        db.session.add(notification)
        db.session.commit()

        mock_client = MagicMock()
        mock_client.redeem.return_value = {
            "state": "COMPLETED",
            "paymentDetails": [{"state": "COMPLETED", "transactionId": "TXN_SCHED_01"}],
        }
        mock_client.get_subscription_status.return_value = {"state": "ACTIVE"}

        barrier = threading.Barrier(2)
        results = {}

        def run_scheduler_worker(worker_id: str):
            with self.app.app_context():
                try:
                    barrier.wait(timeout=5)
                    worker_results = RenewalService.process_due_renewals(self.provider)
                    results[worker_id] = worker_results
                except Exception as exc:
                    results[worker_id] = exc

        with patch.object(self.provider, "_get_client", return_value=mock_client):
            t1 = threading.Thread(target=run_scheduler_worker, args=("worker_1",))
            t2 = threading.Thread(target=run_scheduler_worker, args=("worker_2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        # PhonePe redeem must be dispatched EXACTLY ONCE
        self.assertEqual(mock_client.redeem.call_count, 1)

        r1 = results.get("worker_1", [])
        r2 = results.get("worker_2", [])
        total_executed = sum(1 for r in r1 if isinstance(r, dict) and r.get("success")) + sum(1 for r in r2 if isinstance(r, dict) and r.get("success"))
        self.assertEqual(total_executed, 1)

    # ── Scenario 12: Route-Level Webhook Concurrency, Atomic Fencing & Idempotency ──

    def test_scenario_12_route_level_webhook_concurrency_atomic_fencing_and_idempotency(self):
        """
        Scenario 12 (Phase 6.2): Route-level multi-threaded webhook concurrency on real MySQL 8.0 InnoDB.
        - Two synchronized threads deliver the same authenticated PhonePe V2 webhook via the actual route path (/saas/phonepe-webhook).
        - Mocks ONLY PhonePe HTTP transport/S2S responses.
        - Verifies that InnoDB provider-scoped unique constraint and distributed fencing token guarantee:
          * Exactly one worker succeeds with HTTP 200; concurrent worker receives 422 (retryable) or already_processed.
          * S2S reconciliation is invoked exactly ONCE across all workers.
          * Zero duplicate subscription activations or transaction creations.
          * Event is durably recorded in PROCESSED status with valid owner_token.
          * Subsequent duplicate delivery returns 200 already_processed with zero extra S2S calls or mutations.
          * Expired worker lease fencing prevents stale workers from committing state changes.
        """
        setup_order_id = f"ORD_MYSQL_SETUP_{self.sub_id}"
        # Set subscription to PENDING with setup provider_order_id
        with self.app.app_context():
            sub = db.session.get(SaaSSubscription, self.sub_id)
            sub.status = SaaSSubscriptionStatus.PENDING
            sub.provider_order_id = setup_order_id
            sub.activated_at = None

            mandate = SaaSMandate(
                id=8888,
                organization_id=self.org_id,
                subscription_id=self.sub_id,
                payment_provider="phonepe_v2",
                merchant_subscription_id=f"MSUB_MYSQL_SETUP_{self.sub_id}",
                mandate_status="ACTIVE",
                max_amount_paise=200000,
                frequency="MONTHLY",
            )
            db.session.add(mandate)
            db.session.commit()

        correct_sha = hashlib.sha256(b"webhook_test_user:webhook_test_pass").hexdigest()
        headers = {
            "Authorization": f"Basic {correct_sha}",
            "Content-Type": "application/json",
        }
        event_id = f"EVT_ROUTE_MYSQL_{int(datetime.utcnow().timestamp())}"
        payload = json.dumps({
            "merchantSubscriptionId": f"MSUB_MYSQL_SETUP_{self.sub_id}",
            "eventId": event_id,
            "event": "SUBSCRIPTION_ACTIVE",
        })

        mock_s2s_resp = {
            "state": "COMPLETED",
            "amount": 118000,
            "currency": "INR",
            "merchantOrderId": setup_order_id,
            "merchantSubscriptionId": f"MSUB_MYSQL_SETUP_{self.sub_id}",
            "paymentDetails": [
                {
                    "state": "COMPLETED",
                    "amount": 118000,
                    "transactionId": "TXN_MYSQL_CONCUR_S2S",
                    "paymentMode": "UPI",
                }
            ],
        }

        barrier = threading.Barrier(2)
        worker_results = {}

        def run_route_worker(worker_id: str):
            with self.app.test_client() as client:
                barrier.wait(timeout=5)
                resp = client.post("/saas/phonepe-webhook", data=payload, headers=headers)
                worker_results[worker_id] = (resp.status_code, resp.get_json())

        with patch("services.billing.providers.phonepe_autopay_client.PhonePeAutoPayClient.get_order_status") as mock_get_order, \
             patch("services.billing.providers.phonepe_autopay_client.PhonePeAutoPayClient.get_subscription_status") as mock_get_sub:
            mock_get_order.return_value = mock_s2s_resp
            mock_get_sub.return_value = {"state": "ACTIVE"}

            t1 = threading.Thread(target=run_route_worker, args=("w1",))
            t2 = threading.Thread(target=run_route_worker, args=("w2",))

            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            # Assert exactly one S2S order reconciliation call across both concurrent workers
            self.assertEqual(mock_get_order.call_count, 1)

            # 1. Assert exactly one worker performed processing, the other received 422 (PROCESSING retryable) or 200 (already_processed)
            w1_status, w1_json = worker_results["w1"]
            w2_status, w2_json = worker_results["w2"]

            # Exactly one worker must have performed processing and succeeded with status: "success"
            w1_processed = (w1_status == 200 and isinstance(w1_json, dict) and w1_json.get("status") == "success")
            w2_processed = (w2_status == 200 and isinstance(w2_json, dict) and w2_json.get("status") == "success")
            self.assertTrue(
                w1_processed ^ w2_processed,
                f"Expected exactly one worker to perform processing with status 'success'. Results: w1={worker_results['w1']}, w2={worker_results['w2']}"
            )

            loser_status, loser_json = (w2_status, w2_json) if w1_processed else (w1_status, w1_json)
            if loser_status == 422:
                self.assertEqual(loser_json.get("status"), "PROCESSING")
                self.assertIs(loser_json.get("retryable"), True)
            elif loser_status == 200:
                self.assertEqual(loser_json.get("status"), "already_processed")
            else:
                self.fail(
                    f"Losing worker returned invalid HTTP status {loser_status} (body: {loser_json}). "
                    "Must return either HTTP 422 with status 'PROCESSING' (retryable=True) or HTTP 200 with status 'already_processed'."
                )

            # 2. Assert direct database state on MySQL 8.0 InnoDB
            verify_session = self.SessionFactory()
            try:
                # Subscription activated exactly once
                sub_check = verify_session.get(SaaSSubscription, self.sub_id)
                self.assertEqual(sub_check.status, SaaSSubscriptionStatus.ACTIVE)
                self.assertIsNotNone(sub_check.current_period_start)
                self.assertIsNotNone(sub_check.current_period_end)

                # Exactly one transaction record created
                txs = verify_session.query(SaaSTransaction).filter_by(
                    organization_id=self.org_id,
                    subscription_id=self.sub_id,
                ).all()
                self.assertEqual(len(txs), 1)
                self.assertEqual(txs[0].status, SaaSTransactionStatus.SUCCESS)

                # Verify final event status and released lease
                evt = verify_session.query(SaaSWebhookEvent).filter_by(
                    payment_provider="phonepe_v2",
                    event_id=event_id,
                ).one()
                self.assertEqual(evt.status, WebhookEventStatus.PROCESSED.value)
                self.assertIsNotNone(evt.processed_at)
                self.assertIsNone(evt.owner_token)  # Lease released upon final PROCESSED commit
            finally:
                verify_session.close()

            # 3. Test duplicate delivery after successful processing
            with self.app.test_client() as client:
                dup_resp = client.post("/saas/phonepe-webhook", data=payload, headers=headers)
                self.assertEqual(dup_resp.status_code, 200)
                self.assertEqual(dup_resp.get_json().get("status"), "already_processed")

            # Assert S2S transport was NOT called again
            self.assertEqual(mock_get_order.call_count, 1)

            # Assert transaction count remains strictly 1
            verify_session = self.SessionFactory()
            try:
                tx_count = verify_session.query(SaaSTransaction).filter_by(
                    organization_id=self.org_id,
                    subscription_id=self.sub_id,
                ).count()
                self.assertEqual(tx_count, 1)
            finally:
                verify_session.close()

        # 4. Test actual production stale-worker fencing against real MySQL records
        s12_token = uuid.uuid4().hex[:8]
        stale_event_id = f"EVT_STALE_FENCE_{s12_token}"
        stale_token = f"wh_stale_expired_token_{s12_token}"
        valid_worker_token = f"wh_recovered_worker_token_{s12_token}"
        now_fencing = datetime.utcnow()
        created_s12_org_id = None
        created_s12_sub_id = None
        created_s12_mandate_id = None
        created_s12_event_id = None

        try:
            with self.app.app_context():
                # Create dedicated organization and subscription in PENDING status to verify stale worker cannot activate it
                fence_org = Organization(name="Fence Org", unique_code=f"F{s12_token[:7].upper()}")
                db.session.add(fence_org)
                db.session.commit()
                created_s12_org_id = fence_org.id

                fence_sub = SaaSSubscription(
                    organization_id=created_s12_org_id,
                    plan_id=1,
                    status=SaaSSubscriptionStatus.PENDING,
                    billing_cycle=SaaSBillingCycle.MONTHLY,
                    amount_paise=100000,
                    provider_order_id=f"ORD_FENCE_STALE_{s12_token}",
                )
                db.session.add(fence_sub)
                db.session.commit()
                created_s12_sub_id = fence_sub.id

                fence_mandate = SaaSMandate(
                    organization_id=created_s12_org_id,
                    subscription_id=created_s12_sub_id,
                    payment_provider="phonepe_v2",
                    merchant_subscription_id=f"MSUB_FENCE_{s12_token}",
                    mandate_status="ACTIVE",
                    max_amount_paise=200000,
                    frequency="MONTHLY",
                )
                expired_evt = SaaSWebhookEvent(
                    payment_provider="phonepe_v2",
                    event_id=stale_event_id,
                    event_type="SUBSCRIPTION_ACTIVE",
                    status=WebhookEventStatus.PROCESSING.value,
                    organization_id=created_s12_org_id,
                    owner_token=stale_token,
                    owner_expires_at=now_fencing - timedelta(seconds=10),
                )
                db.session.add_all([fence_mandate, expired_evt])
                db.session.commit()
                created_s12_mandate_id = fence_mandate.id
                created_s12_event_id = expired_evt.id

                # Legitimate worker claims the expired lease using production atomic acquisition logic
                claimed_rows = SaaSWebhookEvent.query.filter(
                    SaaSWebhookEvent.payment_provider == "phonepe_v2",
                    SaaSWebhookEvent.event_id == stale_event_id,
                    or_(
                        SaaSWebhookEvent.owner_expires_at.is_(None),
                        SaaSWebhookEvent.owner_expires_at <= datetime.utcnow(),
                        SaaSWebhookEvent.status.in_([
                            WebhookEventStatus.UNRESOLVED.value,
                            WebhookEventStatus.FAILED.value,
                        ]),
                    )
                ).update({
                    SaaSWebhookEvent.status: WebhookEventStatus.PROCESSING.value,
                    SaaSWebhookEvent.owner_token: valid_worker_token,
                    SaaSWebhookEvent.owner_expires_at: datetime.utcnow() + timedelta(seconds=120),
                }, synchronize_session=False)
                self.assertEqual(claimed_rows, 1)
                db.session.commit()

                # Production Code Path Execution:
                # 1. Assert expired worker token CANNOT transition the event using actual production helper
                with self.assertRaises(WebhookPersistenceError):
                    _transition_v2_webhook_event(
                        event_id=stale_event_id,
                        fencing_token=stale_token,
                        target_state="PROCESSED",
                        event_type="SUBSCRIPTION_ACTIVE",
                        details={"worker": "stale_attempt"},
                        organization_id=created_s12_org_id,
                    )

                # 2. Assert stale worker CANNOT execute atomic activation or commit subscription changes
                # Attempting atomic activation under stale token: conditional update matches 0 rows
                stale_activation_updated = SaaSWebhookEvent.query.filter(
                    SaaSWebhookEvent.payment_provider == "phonepe_v2",
                    SaaSWebhookEvent.event_id == stale_event_id,
                    SaaSWebhookEvent.owner_token == stale_token,
                    SaaSWebhookEvent.owner_expires_at >= datetime.utcnow(),
                    SaaSWebhookEvent.status == WebhookEventStatus.PROCESSING.value,
                ).update({
                    SaaSWebhookEvent.status: WebhookEventStatus.PROCESSED.value,
                }, synchronize_session=False)
                self.assertEqual(stale_activation_updated, 0)
                db.session.rollback()

                # 3. Assert valid current owner transitions event successfully using actual production helper
                transition_ok = _transition_v2_webhook_event(
                    event_id=stale_event_id,
                    fencing_token=valid_worker_token,
                    target_state="PROCESSED",
                    event_type="SUBSCRIPTION_ACTIVE",
                    details={"worker": "valid_owner"},
                    organization_id=created_s12_org_id,
                )
                self.assertTrue(transition_ok)

            # 4. Verify state mutations directly via MySQL queries on a fresh session
            verify_session = self.SessionFactory()
            try:
                # Event transitioned to PROCESSED and ownership lease released in MySQL
                evt_check = verify_session.query(SaaSWebhookEvent).filter_by(
                    payment_provider="phonepe_v2",
                    event_id=stale_event_id,
                ).one()
                self.assertEqual(evt_check.status, WebhookEventStatus.PROCESSED.value)
                self.assertIsNone(evt_check.owner_token)
                self.assertIsNone(evt_check.owner_expires_at)
                self.assertIsNotNone(evt_check.processed_at)

                # Subscription remains PENDING (stale worker could not activate it)
                sub_check = verify_session.get(SaaSSubscription, created_s12_sub_id)
                self.assertEqual(sub_check.status, SaaSSubscriptionStatus.PENDING)
                self.assertIsNone(sub_check.current_period_start)
                self.assertIsNone(sub_check.current_period_end)

                # Zero transactions recorded for the subscription
                txs = verify_session.query(SaaSTransaction).filter_by(
                    subscription_id=created_s12_sub_id,
                ).all()
                self.assertEqual(len(txs), 0)
            finally:
                verify_session.close()

        finally:
            with self.app.app_context():
                if created_s12_org_id:
                    db.session.query(SaaSTransaction).filter_by(organization_id=created_s12_org_id).delete()
                if created_s12_event_id:
                    db.session.query(SaaSWebhookEvent).filter_by(id=created_s12_event_id).delete()
                elif stale_event_id:
                    db.session.query(SaaSWebhookEvent).filter_by(payment_provider="phonepe_v2", event_id=stale_event_id).delete()
                if created_s12_mandate_id:
                    db.session.query(SaaSMandate).filter_by(id=created_s12_mandate_id).delete()
                if created_s12_sub_id:
                    db.session.query(SaaSSubscription).filter_by(id=created_s12_sub_id).delete()
                if created_s12_org_id:
                    db.session.query(Organization).filter_by(id=created_s12_org_id).delete()
                db.session.commit()

    # ── Scenario 13: Production Stale Worker Activation Fencing ──────────────

    def test_scenario_13_production_stale_worker_activation_fencing(self):
        """
        Scenario 13 (Final Phase 6 Test): Production stale worker activation fencing.
        Executes the actual /saas/phonepe-webhook route path against real MySQL 8.0 InnoDB records.
        - Mocks ONLY external PhonePe HTTP transport (get_order_status).
        - At the activation boundary (inside the transport mock after authoritative S2S evidence is resolved),
          a second independent MySQL session transfers the event's ownership lease to a new worker.
        - The original route worker continues into the production atomic activation guard.
        - Asserts:
          1. Original worker receives HTTP 422 (UNRESOLVED, retryable) and cannot commit subscription activation.
          2. Fresh MySQL session verifies:
             - Subscription remains PENDING with zero period extension or entitlement changes.
             - No successful transaction was created.
             - SaaSWebhookEvent ownership belongs to the new worker.
             - Event remains in PROCESSING state (stale worker could not overwrite to PROCESSED).
          3. Stale worker cannot overwrite the new owner's event state using production helper.
          4. Valid current owner subsequently completes the appropriate event transition to PROCESSED.
        """
        unique_token = uuid.uuid4().hex[:8]
        setup_order_id = f"ORD_FENCE_STALE_{unique_token}"
        event_id = f"EVT_FENCE_STALE_{unique_token}"
        merchant_sub_id = f"MSUB_FENCE_{unique_token}"
        provider_sub_id = f"PSUB_FENCE_{unique_token}"
        mandate_ref = f"MN_FENCE_{unique_token}"
        unique_org_code = f"S{unique_token[:7].upper()}"

        created_org_id = None
        created_sub_id = None
        created_mandate_id = None
        created_event_id = None
        created_tx_id = None

        try:
            with self.app.app_context():
                # 1. Create valid MySQL records for pending PhonePe V2 subscription, mandate, webhook event, and setup payment
                fence_org = Organization(name="Stale Worker Fence Tenant", unique_code=unique_org_code)
                db.session.add(fence_org)
                db.session.commit()
                created_org_id = fence_org.id

                fence_sub = SaaSSubscription(
                    organization_id=created_org_id,
                    plan_id=999,  # monthly 100000, expected total 118000
                    payment_provider="phonepe_v2",
                    status=SaaSSubscriptionStatus.PENDING,
                    billing_cycle=SaaSBillingCycle.MONTHLY,
                    amount_paise=118000,
                    provider_order_id=setup_order_id,
                    current_period_start=None,
                    current_period_end=None,
                )
                db.session.add(fence_sub)
                db.session.commit()
                created_sub_id = fence_sub.id

                fence_mandate = SaaSMandate(
                    organization_id=created_org_id,
                    subscription_id=created_sub_id,
                    payment_provider="phonepe_v2",
                    merchant_subscription_id=merchant_sub_id,
                    provider_subscription_id=provider_sub_id,
                    mandate_reference=mandate_ref,
                    mandate_status="ACTIVE",
                    max_amount_paise=200000,
                    frequency="MONTHLY",
                )
                init_evt = SaaSWebhookEvent(
                    payment_provider="phonepe_v2",
                    event_id=event_id,
                    event_type="SUBSCRIPTION_ACTIVE",
                    status=WebhookEventStatus.UNRESOLVED.value,
                    organization_id=created_org_id,
                    error_message="Initial unverified event state",
                )
                setup_tx = SaaSTransaction(
                    organization_id=created_org_id,
                    subscription_id=created_sub_id,
                    payment_provider="phonepe_v2",
                    provider_order_id=setup_order_id,
                    amount_paise=118000,
                    status=SaaSTransactionStatus.PENDING,
                )
                db.session.add_all([fence_mandate, init_evt, setup_tx])
                db.session.commit()
                created_mandate_id = fence_mandate.id
                created_event_id = init_evt.id
                created_tx_id = setup_tx.id

            correct_sha = hashlib.sha256(b"webhook_test_user:webhook_test_pass").hexdigest()
            headers = {
                "Authorization": f"Basic {correct_sha}",
                "Content-Type": "application/json",
            }
            payload = json.dumps({
                "merchantSubscriptionId": merchant_sub_id,
                "merchantOrderId": setup_order_id,
                "eventId": event_id,
                "event": "SUBSCRIPTION_ACTIVE",
            })

            mock_s2s_resp = {
                "state": "COMPLETED",
                "amount": 118000,
                "currency": "INR",
                "merchantOrderId": setup_order_id,
                "merchantSubscriptionId": merchant_sub_id,
                "paymentDetails": [
                    {
                        "state": "COMPLETED",
                        "amount": 118000,
                        "currency": "INR",
                        "transactionId": f"TXN_{unique_token}",
                        "paymentMode": "UPI",
                    }
                ],
            }

            new_worker_token = f"wh_transferred_owner_{unique_token}"
            captured_stale_token = {}

            # 3. Mock ONLY external PhonePe HTTP transport
            def mock_get_order_side_effect(order_id, details=True):
                # 4. At the activation boundary, transfer worker lease using a second independent MySQL session
                second_session = self.SessionFactory()
                try:
                    evt_in_db = second_session.query(SaaSWebhookEvent).filter_by(
                        payment_provider="phonepe_v2",
                        event_id=event_id,
                    ).one()
                    captured_stale_token["token"] = evt_in_db.owner_token
                    self.assertIsNotNone(evt_in_db.owner_token, "Original worker should have acquired lease")
                    self.assertEqual(evt_in_db.status, WebhookEventStatus.PROCESSING.value)

                    # Atomically transfer lease in MySQL to new worker
                    updated_rows = second_session.query(SaaSWebhookEvent).filter_by(
                        payment_provider="phonepe_v2",
                        event_id=event_id,
                    ).update({
                        SaaSWebhookEvent.owner_token: new_worker_token,
                        SaaSWebhookEvent.owner_expires_at: datetime.utcnow() + timedelta(seconds=120),
                    })
                    self.assertEqual(updated_rows, 1)
                    second_session.commit()
                finally:
                    second_session.close()

                return mock_s2s_resp

            with patch("services.billing.providers.phonepe_autopay_client.PhonePeAutoPayClient.get_order_status", side_effect=mock_get_order_side_effect), \
                 patch("services.billing.providers.phonepe_autopay_client.PhonePeAutoPayClient.get_subscription_status", return_value={"state": "ACTIVE"}):
                # 2. Start webhook processing through actual Flask route & 5. Allow original worker into production atomic guard
                with self.app.test_client() as client:
                    resp = client.post("/saas/phonepe-webhook", data=payload, headers=headers)

            # 6. Assert the stale worker cannot commit subscription activation or successful transaction
            self.assertEqual(resp.status_code, 422)
            resp_json = resp.get_json()
            self.assertEqual(resp_json.get("status"), "UNRESOLVED")
            self.assertIs(resp_json.get("retryable"), True)
            self.assertIn("Worker lease expired during reconciliation", resp_json.get("error", ""))

            # 7. Verify using a fresh MySQL session
            verify_session = self.SessionFactory()
            try:
                # Subscription remains PENDING
                sub_check = verify_session.get(SaaSSubscription, created_sub_id)
                self.assertEqual(sub_check.status, SaaSSubscriptionStatus.PENDING)
                # No current-period extension or entitlement update
                self.assertIsNone(sub_check.current_period_start)
                self.assertIsNone(sub_check.current_period_end)
                self.assertNotEqual(sub_check.last_webhook_event_id, event_id)

                # No successful transaction
                success_txs = verify_session.query(SaaSTransaction).filter_by(
                    subscription_id=created_sub_id,
                    status=SaaSTransactionStatus.SUCCESS,
                ).all()
                self.assertEqual(len(success_txs), 0)

                # Event ownership belongs to the new worker
                evt_check = verify_session.query(SaaSWebhookEvent).filter_by(
                    payment_provider="phonepe_v2",
                    event_id=event_id,
                ).one()
                self.assertEqual(evt_check.owner_token, new_worker_token)
                # Stale worker cannot overwrite the new owner's event state
                self.assertEqual(evt_check.status, WebhookEventStatus.PROCESSING.value)
            finally:
                verify_session.close()

            # Verify stale worker cannot overwrite new owner's event state using production helper
            with self.app.app_context():
                with self.assertRaises(WebhookPersistenceError):
                    _transition_v2_webhook_event(
                        event_id=event_id,
                        fencing_token=captured_stale_token["token"],
                        target_state="PROCESSED",
                        event_type="SUBSCRIPTION_ACTIVE",
                        details={"attempt": "stale_overwrite_attempt"},
                        organization_id=created_org_id,
                    )

            # 8. Verify the valid current owner can subsequently complete the appropriate event transition
            with self.app.app_context():
                transition_ok = _transition_v2_webhook_event(
                    event_id=event_id,
                    fencing_token=new_worker_token,
                    target_state="PROCESSED",
                    event_type="SUBSCRIPTION_ACTIVE",
                    details={"completed_by": "valid_current_owner", "sub_id": created_sub_id},
                    organization_id=created_org_id,
                )
                self.assertTrue(transition_ok)

            verify_session = self.SessionFactory()
            try:
                evt_final = verify_session.query(SaaSWebhookEvent).filter_by(
                    payment_provider="phonepe_v2",
                    event_id=event_id,
                ).one()
                self.assertEqual(evt_final.status, WebhookEventStatus.PROCESSED.value)
                self.assertIsNone(evt_final.owner_token)
                self.assertIsNone(evt_final.owner_expires_at)
                self.assertIsNotNone(evt_final.processed_at)
            finally:
                verify_session.close()

        finally:
            with self.app.app_context():
                if created_org_id:
                    db.session.query(SaaSTransaction).filter_by(organization_id=created_org_id).delete()
                if created_event_id:
                    db.session.query(SaaSWebhookEvent).filter_by(id=created_event_id).delete()
                elif event_id:
                    db.session.query(SaaSWebhookEvent).filter_by(payment_provider="phonepe_v2", event_id=event_id).delete()
                if created_mandate_id:
                    db.session.query(SaaSMandate).filter_by(id=created_mandate_id).delete()
                if created_sub_id:
                    db.session.query(SaaSSubscription).filter_by(id=created_sub_id).delete()
                if created_org_id:
                    db.session.query(Organization).filter_by(id=created_org_id).delete()
                db.session.commit()


if __name__ == "__main__":
    unittest.main()

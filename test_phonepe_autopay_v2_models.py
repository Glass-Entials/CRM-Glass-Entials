"""
test_phonepe_autopay_v2_models.py
=================================
Unit tests for PhonePe AutoPay V2 database models, enums, relationships,
constraints, and backward compatibility.

Covers:
1. SaaSMandate model creation and basic field validation
2. SaaSPreDebitNotification model creation
3. SaaSRedemptionAttempt model creation
4. Verification that auto_debit_snapshot is immutable (documents the contract)
5. MandateStatus, PreDebitNotificationStatus, RedemptionAttemptStatus enum values
6. Composite unique constraint documentation & table args inspection
7. SaaSTransaction.redemption_attempt_id is nullable
8. Terminal mandate states verification
9. SaaSSubscription active_mandate_id, claim_token, claim_expires_at attributes
10. SaaSTransaction mandate_id and redemption_attempt_id attributes
"""

import unittest
from datetime import datetime, timedelta
from flask import Flask
from sqlalchemy import UniqueConstraint, Index

from model import (
    db,
    Organization,
    Plan,
    SaaSSubscription,
    SaaSTransaction,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    MandateStatus,
    PreDebitNotificationStatus,
    RedemptionAttemptStatus,
    SaaSSubscriptionStatus,
    SaaSBillingCycle,
    SaaSTransactionStatus,
)


class TestPhonePeAutoPayV2Models(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
        cls.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        cls.app.config["TESTING"] = True
        db.init_app(cls.app)

        with cls.app.app_context():
            db.create_all()

    def setUp(self):
        self.app_context = self.app.app_context()
        self.app_context.push()

        # Seed minimal test organization and plan
        self.org = Organization(name="GlassEntials Demo Org", unique_code="GLEDEMO")
        db.session.add(self.org)
        db.session.flush()

        self.plan = Plan(
            name="Pro Test Plan",
            display_name="Professional",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )
        db.session.add(self.plan)
        db.session.flush()

        self.sub = SaaSSubscription(
            organization_id=self.org.id,
            plan_id=self.plan.id,
            payment_provider="phonepe_v2",
            status=SaaSSubscriptionStatus.PENDING,
            billing_cycle=SaaSBillingCycle.MONTHLY,
        )
        db.session.add(self.sub)
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        db.session.commit()
        self.app_context.pop()

    # ── Test 1: SaaSMandate Creation & Field Validation ────────────────────────
    def test_saas_mandate_creation_and_fields(self):
        """Test SaaSMandate model creation, default values, and relationships."""
        now = datetime.utcnow()
        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id=f"MSUB_{self.org.id}_001",
            provider_subscription_id="OMSV2_987654321",
            mandate_reference="UMN1234567890@axis",
            mandate_status=MandateStatus.INITIATED,
            auth_workflow_type="TRANSACTION",
            amount_type="VARIABLE",
            max_amount_paise=500000,
            frequency="MONTHLY",
            auto_debit=False,
            start_date=now,
            end_date=now + timedelta(days=3650),
        )
        db.session.add(mandate)
        db.session.commit()

        self.assertIsNotNone(mandate.id)
        self.assertEqual(mandate.payment_provider, "phonepe_v2")
        self.assertEqual(mandate.mandate_status, "INITIATED")
        self.assertEqual(mandate.max_amount_paise, 500000)
        self.assertEqual(mandate.max_amount_inr, 5000.0)
        self.assertFalse(mandate.auto_debit)
        self.assertEqual(mandate.frequency, "MONTHLY")
        self.assertEqual(mandate.auth_workflow_type, "TRANSACTION")
        self.assertEqual(mandate.amount_type, "VARIABLE")
        self.assertIsNotNone(mandate.created_at)
        self.assertIsNotNone(mandate.updated_at)

        # Verify back references and relationships
        self.assertEqual(mandate.organization.id, self.org.id)
        self.assertEqual(mandate.subscription.id, self.sub.id)
        self.assertIn(mandate, self.org.saas_mandates)
        self.assertIn(mandate, self.sub.mandates)

    # ── Test 2: SaaSPreDebitNotification Model Creation ────────────────────────
    def test_saas_pre_debit_notification_creation(self):
        """Test SaaSPreDebitNotification model creation and relationships."""
        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id=f"MSUB_{self.org.id}_002",
            max_amount_paise=200000,
            frequency="MONTHLY",
            auto_debit=False,
        )
        db.session.add(mandate)
        db.session.commit()

        now = datetime.utcnow()
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=mandate.id,
            merchant_order_id=f"NOTIFY_{self.org.id}_{self.sub.id}_01",
            phonepe_order_id="PP_ORD_111",
            amount_paise=100000,
            auto_debit_snapshot=mandate.auto_debit,
            valid_after_at=now + timedelta(hours=24),
            valid_upto_at=now + timedelta(hours=48),
            notified_at=now,
            status=PreDebitNotificationStatus.NOTIFIED,
            raw_response_json={"code": "SUCCESS", "validAfter": 1730000000},
        )
        db.session.add(notification)
        db.session.commit()

        self.assertIsNotNone(notification.id)
        self.assertEqual(notification.status, "NOTIFIED")
        self.assertEqual(notification.amount_paise, 100000)
        self.assertEqual(notification.amount_inr, 1000.0)
        self.assertFalse(notification.auto_debit_snapshot)
        self.assertEqual(notification.phonepe_order_id, "PP_ORD_111")
        self.assertEqual(notification.raw_response_json["code"], "SUCCESS")

        # Verify relationships
        self.assertEqual(notification.mandate.id, mandate.id)
        self.assertEqual(notification.subscription.id, self.sub.id)
        self.assertEqual(notification.organization.id, self.org.id)
        self.assertIn(notification, mandate.pre_debit_notifications)

    # ── Test 3: SaaSRedemptionAttempt Model Creation ───────────────────────────
    def test_saas_redemption_attempt_creation(self):
        """Test SaaSRedemptionAttempt model creation and relationships."""
        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id=f"MSUB_{self.org.id}_003",
            max_amount_paise=200000,
            frequency="MONTHLY",
            auto_debit=False,
        )
        db.session.add(mandate)
        db.session.flush()

        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=mandate.id,
            merchant_order_id=f"NOTIFY_{self.org.id}_003",
            amount_paise=100000,
            auto_debit_snapshot=mandate.auto_debit,
            status=PreDebitNotificationStatus.NOTIFIED,
        )
        db.session.add(notification)
        db.session.flush()

        redemption = SaaSRedemptionAttempt(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            notification_id=notification.id,
            merchant_order_id=notification.merchant_order_id,
            phonepe_transaction_id="TX_PP_RED_123",
            amount_paise=100000,
            status=RedemptionAttemptStatus.INITIATED,
            attempt_number=1,
            failure_reason=None,
            raw_response_json={"status": "INITIATED"},
        )
        db.session.add(redemption)
        db.session.commit()

        self.assertIsNotNone(redemption.id)
        self.assertEqual(redemption.status, "INITIATED")
        self.assertEqual(redemption.amount_paise, 100000)
        self.assertEqual(redemption.amount_inr, 1000.0)
        self.assertEqual(redemption.attempt_number, 1)
        self.assertEqual(redemption.merchant_order_id, notification.merchant_order_id)

        # Verify relationships
        self.assertEqual(redemption.notification.id, notification.id)
        self.assertEqual(redemption.subscription.id, self.sub.id)
        self.assertEqual(redemption.organization.id, self.org.id)
        self.assertIn(redemption, notification.redemption_attempts)

    # ── Test 4: auto_debit_snapshot Immutability Contract ─────────────────────
    def test_auto_debit_snapshot_is_immutable(self):
        """
        Verify that auto_debit_snapshot captures the mandate execution mode at dispatch
        and is decoupled from subsequent changes to mandate.auto_debit.
        """
        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id=f"MSUB_{self.org.id}_004",
            max_amount_paise=300000,
            frequency="MONTHLY",
            auto_debit=False,  # Initially merchant-initiated debit
        )
        db.session.add(mandate)
        db.session.flush()

        # Notification records immutable snapshot of auto_debit at dispatch time
        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=mandate.id,
            merchant_order_id=f"NOTIFY_{self.org.id}_004",
            amount_paise=150000,
            auto_debit_snapshot=mandate.auto_debit,
            status=PreDebitNotificationStatus.NOTIFIED,
        )
        db.session.add(notification)
        db.session.commit()

        self.assertFalse(notification.auto_debit_snapshot)

        # Mandate later gets modified (e.g., auto_debit switched to True by gateway update)
        mandate.auto_debit = True
        db.session.commit()

        # The historical snapshot on the notification must remain False
        refetched_notification = db.session.get(SaaSPreDebitNotification, notification.id)
        self.assertFalse(refetched_notification.auto_debit_snapshot)
        self.assertTrue(refetched_notification.mandate.auto_debit)

        # Verify contract is explicitly documented in model docstring
        doc = SaaSPreDebitNotification.__doc__.lower()
        self.assertIn("immutable", doc)
        self.assertIn("never", doc)

    # ── Test 5: Enum Definitions and Values ───────────────────────────────────
    def test_enum_definitions_and_values(self):
        """Verify MandateStatus, PreDebitNotificationStatus, and RedemptionAttemptStatus enum values."""
        # MandateStatus
        expected_mandate_statuses = {
            "INITIATED": "INITIATED",
            "CREATED": "CREATED",
            "ACTIVE": "ACTIVE",
            "PAUSE_IN_PROGRESS": "PAUSE_IN_PROGRESS",
            "PAUSED": "PAUSED",
            "UNPAUSE_IN_PROGRESS": "UNPAUSE_IN_PROGRESS",
            "REVOKE_IN_PROGRESS": "REVOKE_IN_PROGRESS",
            "REVOKED": "REVOKED",
            "CANCEL_IN_PROGRESS": "CANCEL_IN_PROGRESS",
            "CANCELLED": "CANCELLED",
            "COMPLETED": "COMPLETED",
            "EXPIRED": "EXPIRED",
            "FAILED": "FAILED",
        }
        for name, value in expected_mandate_statuses.items():
            self.assertTrue(hasattr(MandateStatus, name))
            self.assertEqual(getattr(MandateStatus, name).value, value)

        # PreDebitNotificationStatus
        expected_notification_statuses = {
            "INITIATED": "INITIATED",
            "NOTIFICATION_IN_PROGRESS": "NOTIFICATION_IN_PROGRESS",
            "NOTIFIED": "NOTIFIED",
            "FAILED": "FAILED",
            "CONSUMED": "CONSUMED",
            "EXPIRED": "EXPIRED",
        }
        for name, value in expected_notification_statuses.items():
            self.assertTrue(hasattr(PreDebitNotificationStatus, name))
            self.assertEqual(getattr(PreDebitNotificationStatus, name).value, value)

        # RedemptionAttemptStatus
        expected_redemption_statuses = {
            "INITIATED": "INITIATED",
            "PENDING_RECONCILIATION": "PENDING_RECONCILIATION",
            "AMBIGUOUS_NOT_FOUND": "AMBIGUOUS_NOT_FOUND",
            "MANUAL_RECONCILIATION_REQUIRED": "MANUAL_RECONCILIATION_REQUIRED",
            "COMPLETED": "COMPLETED",
            "FAILED": "FAILED",
        }
        for name, value in expected_redemption_statuses.items():
            self.assertTrue(hasattr(RedemptionAttemptStatus, name))
            self.assertEqual(getattr(RedemptionAttemptStatus, name).value, value)

    # ── Test 6: Composite Unique Constraints Metadata ─────────────────────────
    def test_composite_unique_constraints_metadata(self):
        """Verify composite unique constraints on all Phase 4 billing models."""
        def get_unique_constraint_cols(model_cls):
            constraints = []
            for item in getattr(model_cls, "__table_args__", ()):
                if isinstance(item, UniqueConstraint):
                    constraints.append(tuple(item.columns.keys() if hasattr(item.columns, 'keys') else [c.name if hasattr(c, 'name') else str(c) for c in item.columns]))
            return constraints

        # SaaSSubscription: (id, organization_id)
        sub_constraints = get_unique_constraint_cols(SaaSSubscription)
        self.assertTrue(
            any(set(c) == {"id", "organization_id"} for c in sub_constraints),
            "SaaSSubscription must have composite UNIQUE key on (id, organization_id)"
        )

        # SaaSMandate: (id, organization_id) and (organization_id, merchant_subscription_id)
        mandate_constraints = get_unique_constraint_cols(SaaSMandate)
        self.assertTrue(
            any(set(c) == {"id", "organization_id"} for c in mandate_constraints),
            "SaaSMandate must have composite UNIQUE key on (id, organization_id)"
        )
        self.assertTrue(
            any(set(c) == {"organization_id", "merchant_subscription_id"} for c in mandate_constraints),
            "SaaSMandate must have composite UNIQUE key on (organization_id, merchant_subscription_id)"
        )

        # SaaSPreDebitNotification: (id, organization_id) and (organization_id, merchant_order_id)
        pdn_constraints = get_unique_constraint_cols(SaaSPreDebitNotification)
        self.assertTrue(
            any(set(c) == {"id", "organization_id"} for c in pdn_constraints),
            "SaaSPreDebitNotification must have composite UNIQUE key on (id, organization_id)"
        )
        self.assertTrue(
            any(set(c) == {"organization_id", "merchant_order_id"} for c in pdn_constraints),
            "SaaSPreDebitNotification must have composite UNIQUE key on (organization_id, merchant_order_id)"
        )

        # SaaSRedemptionAttempt: (id, organization_id) and (organization_id, merchant_order_id, attempt_number)
        redemption_constraints = get_unique_constraint_cols(SaaSRedemptionAttempt)
        self.assertTrue(
            any(set(c) == {"id", "organization_id"} for c in redemption_constraints),
            "SaaSRedemptionAttempt must have composite UNIQUE key on (id, organization_id)"
        )
        self.assertTrue(
            any(set(c) == {"organization_id", "merchant_order_id", "attempt_number"} for c in redemption_constraints),
            "SaaSRedemptionAttempt must have composite UNIQUE key on (organization_id, merchant_order_id, attempt_number)"
        )

    # ── Test 7: SaaSTransaction.redemption_attempt_id is Nullable ─────────────
    def test_saas_transaction_redemption_attempt_id_nullable(self):
        """Test that initial setup payments can have redemption_attempt_id=None."""
        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id=f"MSUB_{self.org.id}_007",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.commit()

        # Initial payment has no redemption attempt
        tx = SaaSTransaction(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=mandate.id,
            redemption_attempt_id=None,
            amount_paise=100000,
            payment_provider="phonepe_v2",
            provider_payment_id="TXN_INITIAL_SETUP_007",
            status=SaaSTransactionStatus.SUCCESS,
        )
        db.session.add(tx)
        db.session.commit()

        self.assertIsNotNone(tx.id)
        self.assertIsNone(tx.redemption_attempt_id)
        self.assertEqual(tx.mandate_id, mandate.id)
        self.assertIsNone(tx.redemption_attempt)
        self.assertEqual(tx.mandate.id, mandate.id)

        # Column property check
        column = SaaSTransaction.__table__.columns["redemption_attempt_id"]
        self.assertTrue(column.nullable)

    # ── Test 8: Terminal Mandate States ───────────────────────────────────────
    def test_terminal_mandate_states(self):
        """Test that all terminal mandate states are correctly classified."""
        terminal_states = MandateStatus.terminal_states()
        expected_terminals = {
            MandateStatus.REVOKED,
            MandateStatus.CANCELLED,
            MandateStatus.COMPLETED,
            MandateStatus.EXPIRED,
            MandateStatus.FAILED,
        }
        self.assertEqual(terminal_states, expected_terminals)

        for status in expected_terminals:
            self.assertTrue(status.is_terminal)

        # Non-terminal states must return False
        non_terminal_states = {
            MandateStatus.INITIATED,
            MandateStatus.CREATED,
            MandateStatus.ACTIVE,
            MandateStatus.PAUSE_IN_PROGRESS,
            MandateStatus.PAUSED,
            MandateStatus.UNPAUSE_IN_PROGRESS,
            MandateStatus.REVOKE_IN_PROGRESS,
            MandateStatus.CANCEL_IN_PROGRESS,
        }
        for status in non_terminal_states:
            self.assertFalse(status.is_terminal)

    # ── Test 9: SaaSSubscription Extended Attributes & Property ───────────────
    def test_saas_subscription_extended_attributes(self):
        """Test SaaSSubscription active_mandate_id, claim_token, claim_expires_at and subscription_id property."""
        self.assertTrue(hasattr(SaaSSubscription, "active_mandate_id"))
        self.assertTrue(hasattr(SaaSSubscription, "claim_token"))
        self.assertTrue(hasattr(SaaSSubscription, "claim_expires_at"))
        self.assertTrue(hasattr(SaaSSubscription, "active_mandate"))

        # Test claiming
        expires = datetime.utcnow() + timedelta(minutes=15)
        self.sub.claim_token = "worker-uuid-12345"
        self.sub.claim_expires_at = expires
        db.session.commit()

        refetched_sub = db.session.get(SaaSSubscription, self.sub.id)
        self.assertEqual(refetched_sub.claim_token, "worker-uuid-12345")
        self.assertIsNotNone(refetched_sub.claim_expires_at)

        # Test active_mandate link & subscription_id property
        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id="MSUB_ACTIVE_009",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.flush()

        refetched_sub.active_mandate = mandate
        db.session.commit()

        # Property should prioritize active_mandate.merchant_subscription_id
        self.assertEqual(refetched_sub.subscription_id, "MSUB_ACTIVE_009")

        # When active_mandate is None, falls back to provider_subscription_id
        refetched_sub.active_mandate = None
        refetched_sub.provider_subscription_id = "LEGACY_PHONEPE_SUB_01"
        db.session.commit()
        self.assertEqual(refetched_sub.subscription_id, "LEGACY_PHONEPE_SUB_01")

    # ── Test 10: SaaSTransaction Extended Attributes ──────────────────────────
    def test_saas_transaction_extended_attributes(self):
        """Test SaaSTransaction mandate_id and redemption_attempt_id attributes and linkages."""
        self.assertTrue(hasattr(SaaSTransaction, "mandate_id"))
        self.assertTrue(hasattr(SaaSTransaction, "redemption_attempt_id"))
        self.assertTrue(hasattr(SaaSTransaction, "mandate"))
        self.assertTrue(hasattr(SaaSTransaction, "redemption_attempt"))

        mandate = SaaSMandate(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            merchant_subscription_id=f"MSUB_{self.org.id}_010",
            max_amount_paise=200000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.flush()

        notification = SaaSPreDebitNotification(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=mandate.id,
            merchant_order_id=f"NOTIFY_{self.org.id}_010",
            amount_paise=100000,
            auto_debit_snapshot=mandate.auto_debit,
            status=PreDebitNotificationStatus.NOTIFIED,
        )
        db.session.add(notification)
        db.session.flush()

        redemption = SaaSRedemptionAttempt(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            notification_id=notification.id,
            merchant_order_id=notification.merchant_order_id,
            amount_paise=100000,
            status=RedemptionAttemptStatus.COMPLETED,
        )
        db.session.add(redemption)
        db.session.flush()

        tx = SaaSTransaction(
            organization_id=self.org.id,
            subscription_id=self.sub.id,
            mandate_id=mandate.id,
            redemption_attempt_id=redemption.id,
            amount_paise=100000,
            payment_provider="phonepe_v2",
            provider_payment_id="TXN_RENEWAL_010",
            status=SaaSTransactionStatus.SUCCESS,
        )
        db.session.add(tx)
        db.session.commit()

        self.assertEqual(tx.mandate.id, mandate.id)
        self.assertEqual(tx.redemption_attempt.id, redemption.id)
        self.assertIn(tx, mandate.transactions)
        self.assertIn(tx, redemption.transactions)


if __name__ == "__main__":
    unittest.main()

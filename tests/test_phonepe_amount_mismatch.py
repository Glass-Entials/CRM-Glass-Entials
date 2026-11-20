import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta

from flask import Flask

from model import (
    db,
    Plan,
    Organization,
    SaaSSubscription,
    SaaSMandate,
    SaaSPreDebitNotification,
    SaaSRedemptionAttempt,
    PreDebitNotificationStatus,
)
from services.billing.providers.phonepe_autopay_v2_provider import (
    PhonePeAutoPayV2Provider,
)


class FakePhonePeClient:
    def __init__(self):
        self.redeem_calls = []

    def redeem(self, payload):
        self.redeem_calls.append(payload)
        raise AssertionError("Gateway must not be called")


class PhonePeAmountMismatchTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = Flask("phonepe_amount_mismatch_test")
        cls.app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI="sqlite://",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)

    def setUp(self):
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_redemption_is_blocked_when_amount_differs_from_notification(self):
        plan = Plan(
            name="Regression Test",
            monthly_price_paise=100000,
            yearly_price_paise=1000000,
        )
        db.session.add(plan)
        db.session.flush()

        org = Organization(
            name="Regression Test Org",
            unique_code="REGTEST01",
            plan_id=plan.id,
        )
        db.session.add(org)
        db.session.flush()

        subscription = SaaSSubscription(
            organization_id=org.id,
            plan_id=plan.id,
            payment_provider="phonepe",
            amount_paise=100000,
            current_period_start=None,
        )
        db.session.add(subscription)
        db.session.flush()

        mandate = SaaSMandate(
            organization_id=org.id,
            subscription_id=subscription.id,
            merchant_subscription_id="TEST-MANDATE-001",
            max_amount_paise=500000,
            frequency="MONTHLY",
        )
        db.session.add(mandate)
        db.session.flush()

        subscription.active_mandate_id = mandate.id

        now = datetime.utcnow()
        notification = SaaSPreDebitNotification(
            organization_id=org.id,
            subscription_id=subscription.id,
            mandate_id=mandate.id,
            merchant_order_id="TEST-ORDER-MISMATCH-001",
            amount_paise=100000,
            auto_debit_snapshot=False,
            valid_after_at=now - timedelta(minutes=5),
            valid_upto_at=now + timedelta(minutes=30),
            status=PreDebitNotificationStatus.NOTIFIED.value,
        )
        db.session.add(notification)
        db.session.commit()

        fake_client = FakePhonePeClient()
        provider = PhonePeAutoPayV2Provider(
            autopay_client=fake_client
        )

        result = provider._execute_redemption_inner(
            subscription=subscription,
            amount_paise=500000,
            merchant_transaction_id="TEST-ORDER-MISMATCH-001",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "FAILED")
        self.assertIn("does not match", result.error_message.lower())
        self.assertEqual(len(fake_client.redeem_calls), 0)

        attempts = SaaSRedemptionAttempt.query.filter_by(
            notification_id=notification.id
        ).count()

        self.assertEqual(attempts, 0)

        db.session.refresh(notification)
        self.assertEqual(
            notification.status,
            PreDebitNotificationStatus.NOTIFIED.value,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

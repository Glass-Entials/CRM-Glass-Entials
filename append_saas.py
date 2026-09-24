"""Script to append SaaS billing models to model.py"""
SAAS_MODELS = '''

# ===========================================================================
# SAAS BILLING MODELS
# ===========================================================================

class SaaSSubscription(db.Model):
    """Tracks Razorpay subscription for each organization."""
    __tablename__ = "saas_subscription"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id"), nullable=False, unique=True, index=True
    )
    plan_id = db.Column(db.Integer, db.ForeignKey("plan.id"), nullable=False)
    razorpay_subscription_id = db.Column(db.String(100), nullable=True, unique=True, index=True)
    razorpay_plan_id = db.Column(db.String(100), nullable=True)
    status = db.Column(
        db.Enum(SaaSSubscriptionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=SaaSSubscriptionStatus.PENDING,
        server_default=db.text("\'pending\'"), index=True,
    )
    billing_cycle = db.Column(
        db.Enum(SaaSBillingCycle, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=SaaSBillingCycle.MONTHLY,
    )
    current_period_start = db.Column(db.DateTime, nullable=True)
    current_period_end   = db.Column(db.DateTime, nullable=True)
    cancelled_at         = db.Column(db.DateTime, nullable=True)
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)
    grace_period_end     = db.Column(db.DateTime, nullable=True)
    last_webhook_event_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    organization = db.relationship("Organization", backref=db.backref("saas_subscription", uselist=False))
    plan = db.relationship("Plan", backref="saas_subscriptions")
    transactions = db.relationship(
        "SaaSTransaction", back_populates="subscription",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<SaaSSubscription org={self.organization_id} status={self.status.value}>"


class SaaSTransaction(db.Model):
    """Records every Razorpay SaaS payment event."""
    __tablename__ = "saas_transaction"

    id = db.Column(db.Integer, primary_key=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organization.id"), nullable=False, index=True
    )
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("saas_subscription.id"), nullable=True, index=True
    )
    razorpay_payment_id      = db.Column(db.String(100), nullable=True, unique=True, index=True)
    razorpay_invoice_id      = db.Column(db.String(100), nullable=True, index=True)
    razorpay_subscription_id = db.Column(db.String(100), nullable=True, index=True)
    razorpay_event_id        = db.Column(db.String(100), nullable=True, unique=True, index=True)
    amount_paise = db.Column(db.BigInteger, nullable=False, default=0)
    currency     = db.Column(db.String(10), nullable=False, default="INR")
    status = db.Column(
        db.Enum(SaaSTransactionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False, default=SaaSTransactionStatus.PENDING,
        server_default=db.text("\'pending\'"), index=True,
    )
    payment_method  = db.Column(db.String(50), nullable=True)
    failure_reason  = db.Column(db.Text, nullable=True)
    billing_period_start = db.Column(db.DateTime, nullable=True)
    billing_period_end   = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    organization = db.relationship("Organization", backref="saas_transactions")
    subscription = db.relationship("SaaSSubscription", back_populates="transactions")

    @property
    def amount_inr(self):
        return round(self.amount_paise / 100, 2)

    def __repr__(self):
        return f"<SaaSTransaction payment={self.razorpay_payment_id} status={self.status.value}>"


class SaaSWebhookEvent(db.Model):
    """Idempotency log for processed Razorpay webhook events."""
    __tablename__ = "saas_webhook_event"

    id           = db.Column(db.Integer, primary_key=True)
    event_id     = db.Column(db.String(100), nullable=False, unique=True, index=True)
    event_type   = db.Column(db.String(100), nullable=False)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    payload_summary = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f"<SaaSWebhookEvent {self.event_id} type={self.event_type}>"
'''

with open('model.py', 'r', encoding='utf-8') as f:
    content = f.read()

if 'class SaaSSubscription(db.Model)' not in content:
    with open('model.py', 'a', encoding='utf-8') as f:
        f.write(SAAS_MODELS)
    print('SaaS models appended.')
else:
    print('SaaS models already present, skipping.')

"""SaaS billing: Add SaaSSubscription, SaaSTransaction, SaaSWebhookEvent, extend Plan and Organization

Revision ID: cde1a282101c
Revises: e1f2a3b4c5d6
Create Date: 2026-09-24 11:05:15.169429

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'cde1a282101c'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    # ── New SaaS tables ──────────────────────────────────────────────────────
    op.create_table('saas_webhook_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_id', sa.String(length=100), nullable=False),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.Column('payload_summary', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('saas_webhook_event', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_saas_webhook_event_event_id'), ['event_id'], unique=True)

    op.create_table('saas_subscription',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column('razorpay_subscription_id', sa.String(length=100), nullable=True),
        sa.Column('razorpay_plan_id', sa.String(length=100), nullable=True),
        sa.Column('status', sa.Enum('pending', 'active', 'grace', 'suspended', 'cancelled', 'expired',
                                    name='saassubscriptionstatus'), nullable=False, server_default='pending'),
        sa.Column('billing_cycle', sa.Enum('monthly', 'yearly', name='saasbillingcycle'),
                  nullable=False, server_default='monthly'),
        sa.Column('current_period_start', sa.DateTime(), nullable=True),
        sa.Column('current_period_end', sa.DateTime(), nullable=True),
        sa.Column('cancelled_at', sa.DateTime(), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('grace_period_end', sa.DateTime(), nullable=True),
        sa.Column('last_webhook_event_id', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], ),
        sa.ForeignKeyConstraint(['plan_id'], ['plan.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id'),
        sa.UniqueConstraint('razorpay_subscription_id')
    )
    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_saas_subscription_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_razorpay_subscription_id'), ['razorpay_subscription_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_status'), ['status'], unique=False)

    op.create_table('saas_transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=True),
        sa.Column('razorpay_payment_id', sa.String(length=100), nullable=True),
        sa.Column('razorpay_invoice_id', sa.String(length=100), nullable=True),
        sa.Column('razorpay_subscription_id', sa.String(length=100), nullable=True),
        sa.Column('razorpay_event_id', sa.String(length=100), nullable=True),
        sa.Column('amount_paise', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('currency', sa.String(length=10), nullable=False, server_default='INR'),
        sa.Column('status', sa.Enum('success', 'failed', 'pending', 'refunded',
                                    name='saastransactionstatus'), nullable=False, server_default='pending'),
        sa.Column('payment_method', sa.String(length=50), nullable=True),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('billing_period_start', sa.DateTime(), nullable=True),
        sa.Column('billing_period_end', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], ),
        sa.ForeignKeyConstraint(['subscription_id'], ['saas_subscription.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('razorpay_event_id'),
        sa.UniqueConstraint('razorpay_payment_id')
    )
    with op.batch_alter_table('saas_transaction', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_saas_transaction_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_razorpay_event_id'), ['razorpay_event_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_razorpay_invoice_id'), ['razorpay_invoice_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_razorpay_payment_id'), ['razorpay_payment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_razorpay_subscription_id'), ['razorpay_subscription_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_subscription_id'), ['subscription_id'], unique=False)

    # ── Extend Organization table ────────────────────────────────────────────
    with op.batch_alter_table('organization', schema=None) as batch_op:
        batch_op.add_column(sa.Column('razorpay_customer_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('saas_mode', sa.Boolean(), nullable=False, server_default='1'))
        batch_op.create_index(batch_op.f('ix_organization_razorpay_customer_id'), ['razorpay_customer_id'], unique=False)

    # ── Extend Plan table ────────────────────────────────────────────────────
    with op.batch_alter_table('plan', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('monthly_price_paise', sa.BigInteger(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('yearly_price_paise', sa.BigInteger(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('razorpay_monthly_plan_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('razorpay_yearly_plan_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('features_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'))

    # ── OrganizationStatus enum update: add 'pending', 'grace', 'cancelled' ─
    # MySQL Enum ALTER: use batch_alter_table to modify in-place
    with op.batch_alter_table('organization', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum('active', 'suspended', 'archived', name='organizationstatus'),
            type_=sa.Enum('pending', 'active', 'grace', 'suspended', 'archived', 'cancelled', name='organizationstatus'),
            existing_nullable=False,
            existing_server_default='active'
        )


def downgrade():
    with op.batch_alter_table('organization', schema=None) as batch_op:
        batch_op.alter_column(
            'status',
            existing_type=sa.Enum('pending', 'active', 'grace', 'suspended', 'archived', 'cancelled', name='organizationstatus'),
            type_=sa.Enum('active', 'suspended', 'archived', name='organizationstatus'),
            existing_nullable=False,
            existing_server_default='active'
        )
        batch_op.drop_index(batch_op.f('ix_organization_razorpay_customer_id'))
        batch_op.drop_column('saas_mode')
        batch_op.drop_column('razorpay_customer_id')

    with op.batch_alter_table('plan', schema=None) as batch_op:
        batch_op.drop_column('sort_order')
        batch_op.drop_column('features_json')
        batch_op.drop_column('razorpay_yearly_plan_id')
        batch_op.drop_column('razorpay_monthly_plan_id')
        batch_op.drop_column('yearly_price_paise')
        batch_op.drop_column('monthly_price_paise')
        batch_op.drop_column('description')

    with op.batch_alter_table('saas_transaction', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saas_transaction_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_status'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_razorpay_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_razorpay_payment_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_razorpay_invoice_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_razorpay_event_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_organization_id'))
    op.drop_table('saas_transaction')

    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saas_subscription_status'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_razorpay_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_organization_id'))
    op.drop_table('saas_subscription')

    with op.batch_alter_table('saas_webhook_event', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saas_webhook_event_event_id'))
    op.drop_table('saas_webhook_event')

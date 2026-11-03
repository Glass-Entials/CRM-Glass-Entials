"""SaaS billing: PhonePe UPI AutoPay migration and provider-agnostic schema

Revision ID: b7e9a1c2d3f4
Revises: 537570cc8101
Create Date: 2026-11-03 11:15:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7e9a1c2d3f4'
down_revision = '537570cc8101'
branch_labels = None
depends_on = None


def upgrade():
    # ── Extend organization table ─────────────────────────────────────────────
    with op.batch_alter_table('organization', schema=None) as batch_op:
        batch_op.add_column(sa.Column('provider_customer_id', sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f('ix_organization_provider_customer_id'), ['provider_customer_id'], unique=False)

    # ── Extend saas_subscription table ───────────────────────────────────────
    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_provider', sa.String(length=50), nullable=False, server_default='phonepe'))
        batch_op.add_column(sa.Column('provider_subscription_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('provider_order_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('provider_transaction_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('provider_customer_reference', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('mandate_reference', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('amount_paise', sa.BigInteger(), nullable=False, server_default='0'))
        batch_op.add_column(sa.Column('currency', sa.String(length=10), nullable=False, server_default='INR'))
        batch_op.add_column(sa.Column('next_billing_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('cancellation_reason', sa.String(length=255), nullable=True))

        batch_op.create_unique_constraint('uq_saas_sub_provider_sub_id', ['provider_subscription_id'])
        batch_op.create_index(batch_op.f('ix_saas_subscription_payment_provider'), ['payment_provider'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_provider_subscription_id'), ['provider_subscription_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_provider_order_id'), ['provider_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_provider_transaction_id'), ['provider_transaction_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_provider_customer_reference'), ['provider_customer_reference'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_mandate_reference'), ['mandate_reference'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_subscription_next_billing_at'), ['next_billing_at'], unique=False)

    # ── Extend saas_transaction table ────────────────────────────────────────
    with op.batch_alter_table('saas_transaction', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_provider', sa.String(length=50), nullable=False, server_default='phonepe'))
        batch_op.add_column(sa.Column('provider_payment_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('provider_order_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('provider_transaction_id', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('provider_subscription_id', sa.String(length=100), nullable=True))

        batch_op.create_unique_constraint('uq_saas_tx_provider_payment_id', ['provider_payment_id'])
        batch_op.create_index(batch_op.f('ix_saas_transaction_payment_provider'), ['payment_provider'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_provider_payment_id'), ['provider_payment_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_provider_order_id'), ['provider_order_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_provider_transaction_id'), ['provider_transaction_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_saas_transaction_provider_subscription_id'), ['provider_subscription_id'], unique=False)

    # ── Extend saas_webhook_event table ──────────────────────────────────────
    with op.batch_alter_table('saas_webhook_event', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_provider', sa.String(length=50), nullable=False, server_default='phonepe'))

    # ── Backfill existing records to preserve legacy Razorpay provider ───────
    # Any record created prior to this migration with razorpay IDs belongs to razorpay
    op.execute(
        "UPDATE saas_subscription SET payment_provider = 'razorpay' "
        "WHERE razorpay_subscription_id IS NOT NULL"
    )
    op.execute(
        "UPDATE saas_transaction SET payment_provider = 'razorpay' "
        "WHERE razorpay_payment_id IS NOT NULL OR razorpay_subscription_id IS NOT NULL"
    )


def downgrade():
    with op.batch_alter_table('saas_webhook_event', schema=None) as batch_op:
        batch_op.drop_column('payment_provider')

    with op.batch_alter_table('saas_transaction', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saas_transaction_provider_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_provider_transaction_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_provider_order_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_provider_payment_id'))
        batch_op.drop_index(batch_op.f('ix_saas_transaction_payment_provider'))
        batch_op.drop_constraint('uq_saas_tx_provider_payment_id', type_='unique')
        batch_op.drop_column('provider_subscription_id')
        batch_op.drop_column('provider_transaction_id')
        batch_op.drop_column('provider_order_id')
        batch_op.drop_column('provider_payment_id')
        batch_op.drop_column('payment_provider')

    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saas_subscription_next_billing_at'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_mandate_reference'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_provider_customer_reference'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_provider_transaction_id'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_provider_order_id'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_provider_subscription_id'))
        batch_op.drop_index(batch_op.f('ix_saas_subscription_payment_provider'))
        batch_op.drop_constraint('uq_saas_sub_provider_sub_id', type_='unique')
        batch_op.drop_column('cancellation_reason')
        batch_op.drop_column('next_billing_at')
        batch_op.drop_column('currency')
        batch_op.drop_column('amount_paise')
        batch_op.drop_column('mandate_reference')
        batch_op.drop_column('provider_customer_reference')
        batch_op.drop_column('provider_transaction_id')
        batch_op.drop_column('provider_order_id')
        batch_op.drop_column('provider_subscription_id')
        batch_op.drop_column('payment_provider')

    with op.batch_alter_table('organization', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_organization_provider_customer_id'))
        batch_op.drop_column('provider_customer_id')


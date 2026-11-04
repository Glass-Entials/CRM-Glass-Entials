"""add_billing_period_key

Revision ID: f2a3b4c5d6e7
Revises: e4f5a6b7c8d9
Create Date: 2026-11-04 10:30:00.000000

Adds billing_period_key column to saas_pre_debit_notification and
saas_redemption_attempt for per-billing-period debit idempotency.

NOTE: A partial unique index on (organization_id, subscription_id, billing_period_key)
WHERE status = 'COMPLETED' cannot be expressed portably in SQLAlchemy/Alembic across
MySQL InnoDB and SQLite. Enforcement is done at the application layer in
execute_redemption() and notify_redemption() inside PhonePeAutoPayV2Provider.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2a3b4c5d6e7'
down_revision = 'e4f5a6b7c8d9'
branch_labels = None
depends_on = None


def upgrade():
    # Add billing_period_key to saas_pre_debit_notification
    with op.batch_alter_table('saas_pre_debit_notification', schema=None) as batch_op:
        batch_op.add_column(sa.Column('billing_period_key', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_saas_pdn_billing_period_key', ['subscription_id', 'billing_period_key'])

    # Add billing_period_key to saas_redemption_attempt
    with op.batch_alter_table('saas_redemption_attempt', schema=None) as batch_op:
        batch_op.add_column(sa.Column('billing_period_key', sa.String(length=64), nullable=True))
        batch_op.create_index('ix_saas_redemption_billing_period_key', ['subscription_id', 'billing_period_key'])


def downgrade():
    with op.batch_alter_table('saas_redemption_attempt', schema=None) as batch_op:
        batch_op.drop_index('ix_saas_redemption_billing_period_key')
        batch_op.drop_column('billing_period_key')
    with op.batch_alter_table('saas_pre_debit_notification', schema=None) as batch_op:
        batch_op.drop_index('ix_saas_pdn_billing_period_key')
        batch_op.drop_column('billing_period_key')

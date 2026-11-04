"""add_billing_period_reservation_table

Revision ID: c3d4e5f6a7b8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-04 11:00:00.000000

Creates saas_billing_period_reservation table with composite unique constraint
(organization_id, subscription_id, billing_period_key) to provide hard,
durable database-level duplicate debit prevention across workers and order IDs.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = 'f2a3b4c5d6e7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'saas_billing_period_reservation',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('billing_period_key', sa.String(length=64), nullable=False),
        sa.Column('merchant_order_id', sa.String(length=100), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='RESERVED'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'subscription_id', 'billing_period_key', name='uq_bpr_org_sub_period'),
        sa.UniqueConstraint('id', 'organization_id', name='uq_bpr_id_org'),
        sa.ForeignKeyConstraint(
            ['subscription_id', 'organization_id'],
            ['saas_subscription.id', 'saas_subscription.organization_id'],
            name='fk_bpr_sub_org',
            ondelete='RESTRICT'
        ),
    )
    op.create_index('ix_bpr_org_id', 'saas_billing_period_reservation', ['organization_id'])
    op.create_index('ix_bpr_subscription_id', 'saas_billing_period_reservation', ['subscription_id'])
    op.create_index('ix_bpr_billing_period_key', 'saas_billing_period_reservation', ['billing_period_key'])
    op.create_index('ix_bpr_merchant_order_id', 'saas_billing_period_reservation', ['merchant_order_id'])
    op.create_index('ix_bpr_status', 'saas_billing_period_reservation', ['status'])


def downgrade():
    op.drop_index('ix_bpr_status', table_name='saas_billing_period_reservation')
    op.drop_index('ix_bpr_merchant_order_id', table_name='saas_billing_period_reservation')
    op.drop_index('ix_bpr_billing_period_key', table_name='saas_billing_period_reservation')
    op.drop_index('ix_bpr_subscription_id', table_name='saas_billing_period_reservation')
    op.drop_index('ix_bpr_org_id', table_name='saas_billing_period_reservation')
    op.drop_table('saas_billing_period_reservation')

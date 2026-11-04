"""add execution ownership to billing period reservation

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-11-04 11:40:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd5e6f7a8b9c0'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'saas_billing_period_reservation',
        sa.Column('owner_token', sa.String(length=64), nullable=True)
    )
    op.add_column(
        'saas_billing_period_reservation',
        sa.Column('owner_expires_at', sa.DateTime(), nullable=True)
    )
    op.add_column(
        'saas_billing_period_reservation',
        sa.Column('redemption_initiated_at', sa.DateTime(), nullable=True)
    )
    op.add_column(
        'saas_billing_period_reservation',
        sa.Column('gateway_state', sa.String(length=50), nullable=True)
    )
    op.create_index('ix_bpr_owner_token', 'saas_billing_period_reservation', ['owner_token'])
    op.create_index('ix_bpr_owner_expires', 'saas_billing_period_reservation', ['owner_expires_at'])


def downgrade():
    op.drop_index('ix_bpr_owner_expires', table_name='saas_billing_period_reservation')
    op.drop_index('ix_bpr_owner_token', table_name='saas_billing_period_reservation')
    op.drop_column('saas_billing_period_reservation', 'gateway_state')
    op.drop_column('saas_billing_period_reservation', 'redemption_initiated_at')
    op.drop_column('saas_billing_period_reservation', 'owner_expires_at')
    op.drop_column('saas_billing_period_reservation', 'owner_token')

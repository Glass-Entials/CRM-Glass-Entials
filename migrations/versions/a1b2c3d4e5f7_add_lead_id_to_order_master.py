"""add lead_id to order_master for lead-based master orders

Revision ID: a1b2c3d4e5f7
Revises: f6a7b8c9d0e1
Create Date: 2026-11-23 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f7'
down_revision = 'f6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    # ── Add lead_id and make customer_id nullable in order_master ──
    op.add_column('order_master', sa.Column('lead_id', sa.Integer(), nullable=True))
    op.create_index('ix_order_master_lead_id', 'order_master', ['lead_id'])
    op.create_foreign_key(
        'fk_order_master_lead',
        'order_master',
        'lead',
        ['lead_id'],
        ['id'],
        ondelete='RESTRICT'
    )
    op.alter_column('order_master', 'customer_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    op.drop_constraint('fk_order_master_lead', 'order_master', type_='foreignkey')
    op.drop_index('ix_order_master_lead_id', table_name='order_master')
    op.drop_column('order_master', 'lead_id')
    op.alter_column('order_master', 'customer_id', existing_type=sa.Integer(), nullable=False)

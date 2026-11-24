"""add order 360 extended fields to order_master and order_consignment

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-11-24 12:45:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6a8'
down_revision = 'a1b2c3d4e5f7'
branch_labels = None
depends_on = None


def upgrade():
    # ── order_master extended fields ──
    op.add_column('order_master', sa.Column('priority', sa.String(length=20), nullable=False, server_default='MEDIUM'))
    op.add_column('order_master', sa.Column('customer_po_number', sa.String(length=100), nullable=True))
    op.add_column('order_master', sa.Column('customer_po_date', sa.DateTime(), nullable=True))

    # ── order_consignment extended transport fields ──
    op.add_column('order_consignment', sa.Column('driver_name', sa.String(length=100), nullable=True))
    op.add_column('order_consignment', sa.Column('driver_contact', sa.String(length=20), nullable=True))
    op.add_column('order_consignment', sa.Column('pickup_location', sa.String(length=200), nullable=True))
    op.add_column('order_consignment', sa.Column('packing_verified', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('order_consignment', sa.Column('site_receiver_name', sa.String(length=100), nullable=True))
    op.add_column('order_consignment', sa.Column('site_receiver_contact', sa.String(length=20), nullable=True))
    op.add_column('order_consignment', sa.Column('pod_signed', sa.Boolean(), nullable=False, server_default='0'))
    op.add_column('order_consignment', sa.Column('pod_signed_at', sa.DateTime(), nullable=True))
    op.add_column('order_consignment', sa.Column('failure_reason', sa.Text(), nullable=True))


def downgrade():
    # ── order_consignment ──
    op.drop_column('order_consignment', 'failure_reason')
    op.drop_column('order_consignment', 'pod_signed_at')
    op.drop_column('order_consignment', 'pod_signed')
    op.drop_column('order_consignment', 'site_receiver_contact')
    op.drop_column('order_consignment', 'site_receiver_name')
    op.drop_column('order_consignment', 'packing_verified')
    op.drop_column('order_consignment', 'pickup_location')
    op.drop_column('order_consignment', 'driver_contact')
    op.drop_column('order_consignment', 'driver_name')

    # ── order_master ──
    op.drop_column('order_master', 'customer_po_date')
    op.drop_column('order_master', 'customer_po_number')
    op.drop_column('order_master', 'priority')

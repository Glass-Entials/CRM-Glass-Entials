"""add hsn_sac to quotation_item

Revision ID: b4a490582b42
Revises: cde1a282101c
Create Date: 2026-10-05 12:25:22.428484

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'b4a490582b42'
down_revision = 'cde1a282101c'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('quotation_item', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hsn_sac', sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table('quotation_item', schema=None) as batch_op:
        batch_op.drop_column('hsn_sac')

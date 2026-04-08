"""ERP billing layout fields

Revision ID: 6bd1a768bb07
Revises: 0ab209c5b492
Create Date: 2026-04-07 12:49:03.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '6bd1a768bb07'
down_revision = '0ab209c5b492'
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table('quotation_item', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_name', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('image', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('formula_type', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('chargeable_quantity', sa.Float(), nullable=True))

def downgrade():
    pass

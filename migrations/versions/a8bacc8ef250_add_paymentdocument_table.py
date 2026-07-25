"""Add PaymentDocument table

Revision ID: a8bacc8ef250
Revises: 24c484dbc88c
Create Date: 2026-07-25 12:13:41.654093

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a8bacc8ef250'
down_revision = '24c484dbc88c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('payment_document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('file_type', sa.String(length=50), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('uploaded_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ),
        sa.ForeignKeyConstraint(['uploaded_by'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('payment_document', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_document_payment_id'), ['payment_id'], unique=False)


def downgrade():
    with op.batch_alter_table('payment_document', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_document_payment_id'))

    op.drop_table('payment_document')

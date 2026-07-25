"""Add Payment and PaymentRemark tables

Revision ID: 24c484dbc88c
Revises: a1b2c3d4e5f6
Create Date: 2026-07-25 12:02:22.537458

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '24c484dbc88c'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('payment',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_number', sa.String(length=20), nullable=False),
        sa.Column('customer_name', sa.String(length=120), nullable=False),
        sa.Column('company_name', sa.String(length=120), nullable=True),
        sa.Column('mobile', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('priority', sa.Enum('Low', 'Medium', 'High', name='paymentpriority'), server_default=sa.text("'Medium'"), nullable=True),
        sa.Column('status', sa.Enum('Pending', 'Received', name='paymentstatus'), server_default=sa.text("'Pending'"), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('payment_mode', sa.Enum('Cash', 'UPI', 'Bank Transfer', 'Cheque', 'Card', name='paymentmode'), nullable=True),
        sa.Column('transaction_reference', sa.String(length=100), nullable=True),
        sa.Column('received_date', sa.Date(), nullable=True),
        sa.Column('received_remarks', sa.Text(), nullable=True),
        sa.Column('invoice_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('received_by', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['assigned_to'], ['employee.id'], ),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], ),
        sa.ForeignKeyConstraint(['received_by'], ['employee.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_assigned_to'), ['assigned_to'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_organization_id'), ['organization_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_payment_payment_number'), ['payment_number'], unique=True)
        batch_op.create_index(batch_op.f('ix_payment_status'), ['status'], unique=False)

    op.create_table('payment_remark',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('payment_id', sa.Integer(), nullable=False),
        sa.Column('remark', sa.Text(), nullable=False),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['user.id'], ),
        sa.ForeignKeyConstraint(['payment_id'], ['payment.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('payment_remark', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_payment_remark_payment_id'), ['payment_id'], unique=False)


def downgrade():
    with op.batch_alter_table('payment_remark', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_remark_payment_id'))
    op.drop_table('payment_remark')

    with op.batch_alter_table('payment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_payment_status'))
        batch_op.drop_index(batch_op.f('ix_payment_payment_number'))
        batch_op.drop_index(batch_op.f('ix_payment_organization_id'))
        batch_op.drop_index(batch_op.f('ix_payment_assigned_to'))
    op.drop_table('payment')

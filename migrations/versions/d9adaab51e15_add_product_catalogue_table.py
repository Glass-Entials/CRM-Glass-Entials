"""add_product_catalogue_table

Revision ID: d9adaab51e15
Revises: 6bd1a768bb07
Create Date: 2026-04-08 17:28:55.903772

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd9adaab51e15'
down_revision = '6bd1a768bb07'
branch_labels = None
depends_on = None


def upgrade():
    # Create Products / Catalogue table
    op.create_table('product',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('sku', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('image', sa.String(length=255), nullable=True),
        sa.Column('category', sa.Enum(
            'Glass', 'Hardware', 'Mirror', 'Aluminium',
            'Accessories', 'Raw Material', 'Other',
            name='productcategory'), nullable=True),
        sa.Column('unit', sa.String(length=30), nullable=True),
        sa.Column('status', sa.Enum(
            'Active', 'Inactive', 'Discontinued',
            name='productstatus'), nullable=True),
        sa.Column('cost_price', sa.Float(), nullable=True),
        sa.Column('selling_price', sa.Float(), nullable=True),
        sa.Column('min_price', sa.Float(), nullable=True),
        sa.Column('gst_rate', sa.Float(), nullable=True),
        sa.Column('hsn_code', sa.String(length=20), nullable=True),
        sa.Column('stock_quantity', sa.Float(), nullable=True),
        sa.Column('min_stock_alert', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('tags', sa.String(length=255), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['created_by'], ['employee.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('product', schema=None) as batch_op:
        batch_op.create_index('ix_product_organization_id', ['organization_id'], unique=False)
        batch_op.create_index('ix_product_sku', ['sku'], unique=False)
        batch_op.create_index('ix_product_status', ['status'], unique=False)


def downgrade():
    with op.batch_alter_table('product', schema=None) as batch_op:
        batch_op.drop_index('ix_product_status')
        batch_op.drop_index('ix_product_sku')
        batch_op.drop_index('ix_product_organization_id')

    op.drop_table('product')

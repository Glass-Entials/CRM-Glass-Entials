"""add_vendor_management_module

Revision ID: 537570cc8101
Revises: f1a2b3c4d5e6
Create Date: 2026-10-22

Vendor Management Module — adds:
  - vendor_category
  - vendor
  - vendor_document
  - vendor_activity
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = '537570cc8101'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_tables = inspector.get_table_names()

    # ── 1. vendor_category ────────────────────────────────────────────────────
    if 'vendor_category' not in existing_tables:
        op.create_table(
            'vendor_category',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(100), nullable=False),
            sa.Column('description', sa.String(255), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('created_by', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vendor_cat_org'),
            sa.ForeignKeyConstraint(['created_by'], ['employee.id'], name='fk_vendor_cat_created_by'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('name', 'organization_id', name='uq_vendor_category_name_org'),
        )
        op.create_index('ix_vendor_category_organization_id', 'vendor_category', ['organization_id'])

    # ── 2. vendor ─────────────────────────────────────────────────────────────
    if 'vendor' not in existing_tables:
        op.create_table(
            'vendor',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('code', sa.String(20), nullable=False),
            sa.Column('company_name', sa.String(150), nullable=False),
            sa.Column('legal_name', sa.String(150), nullable=True),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('products_services', sa.Text(), nullable=True),
            sa.Column('contact_person', sa.String(100), nullable=True),
            sa.Column('designation', sa.String(100), nullable=True),
            sa.Column('phone', sa.String(20), nullable=True),
            sa.Column('alt_phone', sa.String(20), nullable=True),
            sa.Column('email', sa.String(120), nullable=True),
            sa.Column('website', sa.String(255), nullable=True),
            sa.Column('gstin', sa.String(15), nullable=True),
            sa.Column('pan', sa.String(10), nullable=True),
            sa.Column(
                'business_type',
                sa.Enum(
                    'Manufacturer', 'Distributor', 'Trader', 'Service Provider',
                    'Contractor', 'Importer', 'Other',
                    name='vendorbusinesstype'
                ),
                nullable=True
            ),
            sa.Column('address', sa.Text(), nullable=True),
            sa.Column('city', sa.String(100), nullable=True),
            sa.Column('state', sa.String(100), nullable=True),
            sa.Column('pincode', sa.String(10), nullable=True),
            sa.Column('country', sa.String(100), nullable=True, server_default='India'),
            sa.Column('warehouse_address', sa.Text(), nullable=True),
            sa.Column(
                'payment_terms',
                sa.Enum(
                    'Advance', 'Immediate', 'Net 7', 'Net 15', 'Net 30',
                    'Net 45', 'Net 60', 'Net 90', 'COD', 'LC', 'PDC', 'Other',
                    name='vendorpaymentterms'
                ),
                nullable=True
            ),
            sa.Column('credit_period_days', sa.Integer(), nullable=True),
            sa.Column('bank_name', sa.String(100), nullable=True),
            sa.Column('bank_account_no', sa.String(30), nullable=True),
            sa.Column('bank_ifsc', sa.String(15), nullable=True),
            sa.Column(
                'status',
                sa.Enum('Active', 'Inactive', 'Blocked', 'Pending Approval', name='vendorstatus'),
                nullable=False,
                server_default='Active'
            ),
            sa.Column(
                'verification_status',
                sa.Enum('Unverified', 'Verified', 'Rejected', 'Pending Review', name='vendorverificationstatus'),
                nullable=False,
                server_default='Unverified'
            ),
            sa.Column('notes', sa.Text(), nullable=True),
            sa.Column('assigned_to', sa.Integer(), nullable=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('created_by', sa.Integer(), nullable=True),
            sa.Column('updated_by', sa.Integer(), nullable=True),
            sa.Column('is_deleted', sa.Boolean(), nullable=False, server_default='0'),
            sa.Column('deleted_at', sa.DateTime(), nullable=True),
            sa.Column('deleted_by', sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(['category_id'], ['vendor_category.id'], name='fk_vendor_category'),
            sa.ForeignKeyConstraint(['assigned_to'], ['employee.id'], name='fk_vendor_assigned_to'),
            sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vendor_org'),
            sa.ForeignKeyConstraint(['created_by'], ['employee.id'], name='fk_vendor_created_by'),
            sa.ForeignKeyConstraint(['updated_by'], ['employee.id'], name='fk_vendor_updated_by'),
            sa.ForeignKeyConstraint(['deleted_by'], ['employee.id'], name='fk_vendor_deleted_by'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('code', 'organization_id', name='uq_vendor_code_org'),
        )
        op.create_index('ix_vendor_company_name', 'vendor', ['company_name'])
        op.create_index('ix_vendor_category_id', 'vendor', ['category_id'])
        op.create_index('ix_vendor_phone', 'vendor', ['phone'])
        op.create_index('ix_vendor_email', 'vendor', ['email'])
        op.create_index('ix_vendor_gstin', 'vendor', ['gstin'])
        op.create_index('ix_vendor_pan', 'vendor', ['pan'])
        op.create_index('ix_vendor_city', 'vendor', ['city'])
        op.create_index('ix_vendor_status', 'vendor', ['status'])
        op.create_index('ix_vendor_assigned_to', 'vendor', ['assigned_to'])
        op.create_index('ix_vendor_organization_id', 'vendor', ['organization_id'])
        op.create_index('ix_vendor_company_org', 'vendor', ['organization_id', 'company_name'])

    # ── 3. vendor_document ────────────────────────────────────────────────────
    if 'vendor_document' not in existing_tables:
        op.create_table(
            'vendor_document',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('vendor_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(255), nullable=False),
            sa.Column('original_name', sa.String(255), nullable=False),
            sa.Column('file_type', sa.String(50), nullable=True),
            sa.Column('file_size', sa.Integer(), nullable=True),
            sa.Column('document_type', sa.String(50), nullable=True),
            sa.Column('description', sa.String(255), nullable=True),
            sa.Column('uploaded_at', sa.DateTime(), nullable=True),
            sa.Column('uploaded_by', sa.Integer(), nullable=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name='fk_vendor_doc_vendor', ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['uploaded_by'], ['employee.id'], name='fk_vendor_doc_uploaded_by'),
            sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vendor_doc_org'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_vendor_document_vendor_id', 'vendor_document', ['vendor_id'])
        op.create_index('ix_vendor_document_organization_id', 'vendor_document', ['organization_id'])

    # ── 4. vendor_activity ────────────────────────────────────────────────────
    if 'vendor_activity' not in existing_tables:
        op.create_table(
            'vendor_activity',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('vendor_id', sa.Integer(), nullable=False),
            sa.Column('action', sa.String(50), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('changes_json', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('actor_id', sa.Integer(), nullable=True),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(['vendor_id'], ['vendor.id'], name='fk_vendor_activity_vendor', ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['actor_id'], ['employee.id'], name='fk_vendor_activity_actor'),
            sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_vendor_activity_org'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_vendor_activity_vendor_id', 'vendor_activity', ['vendor_id'])
        op.create_index('ix_vendor_activity_organization_id', 'vendor_activity', ['organization_id'])


def downgrade():
    op.drop_table('vendor_activity')
    op.drop_table('vendor_document')
    op.drop_table('vendor')
    op.drop_table('vendor_category')

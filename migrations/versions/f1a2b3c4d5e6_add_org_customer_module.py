"""add_org_customer_module

Revision ID: f1a2b3c4d5e6
Revises: 2a0680deca16
Create Date: 2026-10-14

Organisation Customer Module — adds:
  - org_customer table
  - org_customer_city table
  - org_customer_member table
  - org_customer_id, org_customer_member_id columns on project table
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision = 'f1a2b3c4d5e6'
down_revision = '2a0680deca16'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ── 1. org_customer ────────────────────────────────────────────────────
    op.create_table(
        'org_customer',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(20), nullable=True),
        sa.Column('name', sa.String(150), nullable=False),
        sa.Column('legal_name', sa.String(200), nullable=True),
        sa.Column('org_type', sa.String(50), nullable=True),
        sa.Column('website', sa.String(255), nullable=True),
        sa.Column('industry', sa.String(100), nullable=True),
        sa.Column('gstin', sa.String(15), nullable=True),
        sa.Column('pan', sa.String(10), nullable=True),
        sa.Column('address', sa.String(300), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('state', sa.String(100), nullable=True),
        sa.Column('pincode', sa.String(10), nullable=True),
        sa.Column('country', sa.String(100), nullable=True, server_default='India'),
        sa.Column('primary_phone', sa.String(20), nullable=True),
        sa.Column('primary_email', sa.String(120), nullable=True),
        sa.Column('coverage_type', sa.String(30), nullable=False, server_default='Pan India'),
        sa.Column('status', sa.String(20), nullable=False, server_default='Active'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_org_customer_org'),
        sa.ForeignKeyConstraint(['created_by'], ['employee.id'], name='fk_org_customer_created_by'),
        sa.ForeignKeyConstraint(['updated_by'], ['employee.id'], name='fk_org_customer_updated_by'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_customer_name', 'org_customer', ['name'])
    op.create_index('ix_org_customer_gstin', 'org_customer', ['gstin'])
    op.create_index('ix_org_customer_status', 'org_customer', ['status'])
    op.create_index('ix_org_customer_coverage', 'org_customer', ['coverage_type'])
    op.create_index('ix_org_customer_org_id', 'org_customer', ['organization_id'])
    op.create_index('ix_org_customer_is_deleted', 'org_customer', ['is_deleted'])
    op.create_index('ix_org_customer_org_name', 'org_customer', ['organization_id', 'name'])

    # ── 2. org_customer_city ───────────────────────────────────────────────
    op.create_table(
        'org_customer_city',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_customer_id', sa.Integer(), nullable=False),
        sa.Column('city', sa.String(100), nullable=False),
        sa.Column('state', sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(['org_customer_id'], ['org_customer.id'], name='fk_org_customer_city_oc'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_customer_id', 'city', name='uq_org_customer_city'),
    )
    op.create_index('ix_org_customer_city_oc_id', 'org_customer_city', ['org_customer_id'])

    # ── 3. org_customer_member ─────────────────────────────────────────────
    op.create_table(
        'org_customer_member',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_customer_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('mobile', sa.String(20), nullable=True),
        sa.Column('email', sa.String(120), nullable=True),
        sa.Column('designation', sa.String(100), nullable=True),
        sa.Column('department', sa.String(100), nullable=True),
        sa.Column('role', sa.String(50), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
        sa.Column('is_deleted', sa.Boolean(), nullable=True, server_default='0'),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['org_customer_id'], ['org_customer.id'], name='fk_org_member_oc'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], name='fk_org_member_org'),
        sa.ForeignKeyConstraint(['created_by'], ['employee.id'], name='fk_org_member_created_by'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_customer_member_oc_id', 'org_customer_member', ['org_customer_id'])
    op.create_index('ix_org_customer_member_org', 'org_customer_member', ['organization_id', 'org_customer_id'])
    op.create_index('ix_org_customer_member_primary', 'org_customer_member', ['is_primary'])
    op.create_index('ix_org_customer_member_active', 'org_customer_member', ['is_active'])

    # ── 4. project — add org_customer_id and org_customer_member_id ────────
    inspector = sa.inspect(conn)
    project_columns = [c['name'] for c in inspector.get_columns('project')]

    if 'org_customer_id' not in project_columns:
        op.add_column('project', sa.Column('org_customer_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_project_org_customer', 'project', 'org_customer',
            ['org_customer_id'], ['id']
        )
        op.create_index('ix_project_org_customer_id', 'project', ['org_customer_id'])

    if 'org_customer_member_id' not in project_columns:
        op.add_column('project', sa.Column('org_customer_member_id', sa.Integer(), nullable=True))
        op.create_foreign_key(
            'fk_project_org_customer_member', 'project', 'org_customer_member',
            ['org_customer_member_id'], ['id']
        )
        op.create_index('ix_project_org_customer_member_id', 'project', ['org_customer_member_id'])


def downgrade():
    # Drop project FK columns first
    try:
        op.drop_constraint('fk_project_org_customer_member', 'project', type_='foreignkey')
        op.drop_index('ix_project_org_customer_member_id', table_name='project')
        op.drop_column('project', 'org_customer_member_id')
    except Exception:
        pass

    try:
        op.drop_constraint('fk_project_org_customer', 'project', type_='foreignkey')
        op.drop_index('ix_project_org_customer_id', table_name='project')
        op.drop_column('project', 'org_customer_id')
    except Exception:
        pass

    op.drop_table('org_customer_member')
    op.drop_table('org_customer_city')
    op.drop_table('org_customer')

"""phase25_plans_limits_audit

Revision ID: d4e5f6a7b8c9
Revises: cf15f225f487
Create Date: 2026-08-08

Phase 2.5 - Advanced Super Admin Organization Management
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = 'd4e5f6a7b8c9'
down_revision = 'cf15f225f487'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Create plan table
    op.create_table(
        'plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=True),
        sa.Column('default_member_limit', sa.Integer(), nullable=False, server_default='5'),
        sa.Column('default_storage_limit_gb', sa.Float(), nullable=False, server_default='10.0'),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )

    # 2. Seed default plans
    conn.execute(text("""
        INSERT INTO plan (name, display_name, default_member_limit, default_storage_limit_gb, is_active, created_at)
        VALUES
            ('Basic', 'Basic Plan', 5, 10.0, 1, NOW()),
            ('Professional', 'Professional Plan', 25, 100.0, 1, NOW()),
            ('Enterprise', 'Enterprise Plan', 100, 1000.0, 1, NOW())
        ON DUPLICATE KEY UPDATE name=name
    """))

    # 3. Add Phase 2.5 columns to organization
    try:
        op.add_column('organization', sa.Column('plan_id', sa.Integer(), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('member_limit_override', sa.Integer(), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('storage_limit_override_gb', sa.Float(), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('storage_used_bytes', sa.BigInteger(), nullable=False, server_default='0'))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('suspended_at', sa.DateTime(), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('suspended_by', sa.String(length=100), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('suspension_reason', sa.String(length=100), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('suspension_note', sa.Text(), nullable=True))
    except Exception:
        pass

    # 4. Add foreign key for plan_id on organization
    try:
        op.create_foreign_key('fk_org_plan_id', 'organization', 'plan', ['plan_id'], ['id'])
    except Exception:
        pass

    # 5. Backfill: assign Basic plan to all existing orgs
    conn.execute(text("""
        UPDATE organization
        SET plan_id = (SELECT id FROM plan WHERE name = 'Basic' LIMIT 1)
        WHERE plan_id IS NULL
    """))

    # 6. Update status enum to include 'archived' value (MySQL ENUM modify)
    try:
        op.execute(text("""
            ALTER TABLE organization
            MODIFY COLUMN status ENUM('active','suspended','archived')
            NOT NULL DEFAULT 'active'
        """))
    except Exception:
        pass

    # 7. Create org_audit_log table
    op.create_table(
        'org_audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=80), nullable=False),
        sa.Column('performed_by', sa.String(length=100), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('meta', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_org_audit_log_org_id', 'org_audit_log', ['organization_id'])
    op.create_index('ix_org_audit_log_created_at', 'org_audit_log', ['created_at'])


def downgrade():
    try:
        op.drop_index('ix_org_audit_log_created_at', table_name='org_audit_log')
        op.drop_index('ix_org_audit_log_org_id', table_name='org_audit_log')
        op.drop_table('org_audit_log')
    except Exception:
        pass
    try:
        op.drop_constraint('fk_org_plan_id', 'organization', type_='foreignkey')
    except Exception:
        pass
    for col in ['plan_id', 'member_limit_override', 'storage_limit_override_gb',
                'storage_used_bytes', 'suspended_at', 'suspended_by',
                'suspension_reason', 'suspension_note']:
        try:
            op.drop_column('organization', col)
        except Exception:
            pass
    try:
        op.drop_table('plan')
    except Exception:
        pass

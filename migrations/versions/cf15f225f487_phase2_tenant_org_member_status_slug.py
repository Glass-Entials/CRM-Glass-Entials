"""phase2_tenant_org_member_status_slug

Revision ID: cf15f225f487
Revises: 23bdb34fb932
Create Date: 2026-08-08

Phase 2 - Multi-tenant Organization System
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = 'cf15f225f487'
down_revision = '23bdb34fb932'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Add new columns to organization table
    try:
        op.add_column('organization', sa.Column('slug', sa.String(length=120), nullable=True))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('status', sa.String(length=20), nullable=False,
                                                 server_default=sa.text("'active'")))
    except Exception:
        pass
    try:
        op.add_column('organization', sa.Column('updated_at', sa.DateTime(), nullable=True))
    except Exception:
        pass

    # 2. Indexes for organization
    try:
        op.create_index('ix_organization_slug', 'organization', ['slug'], unique=True)
    except Exception:
        pass
    try:
        op.create_index('ix_organization_status', 'organization', ['status'])
    except Exception:
        pass

    # 3. Backfill slug and updated_at
    conn.execute(text("""
        UPDATE organization
        SET slug = CONCAT(LOWER(REPLACE(TRIM(name), ' ', '-')), '-', id)
        WHERE slug IS NULL OR slug = ''
    """))
    conn.execute(text("UPDATE organization SET updated_at = created_at WHERE updated_at IS NULL"))
    conn.execute(text("UPDATE organization SET status = 'active' WHERE status IS NULL OR status = ''"))

    # 4. Create organization_member table
    op.create_table(
        'organization_member',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False, server_default='member'),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='active'),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id', 'user_id', name='uq_org_member'),
    )
    op.create_index('ix_org_member_org_user', 'organization_member', ['organization_id', 'user_id'])
    op.create_index('ix_organization_member_organization_id', 'organization_member', ['organization_id'])
    op.create_index('ix_organization_member_user_id', 'organization_member', ['user_id'])

    # 5. Backfill organization_member from existing user.organization_id
    conn.execute(text("""
        INSERT INTO organization_member (organization_id, user_id, role, status, joined_at, created_at)
        SELECT
            u.organization_id,
            u.id,
            CASE WHEN u.role = 'admin' THEN 'owner' ELSE 'member' END,
            'active',
            NOW(),
            NOW()
        FROM user u
        WHERE u.organization_id IS NOT NULL
        ON DUPLICATE KEY UPDATE role = VALUES(role)
    """))


def downgrade():
    try:
        op.drop_index('ix_org_member_org_user', table_name='organization_member')
        op.drop_index('ix_organization_member_organization_id', table_name='organization_member')
        op.drop_index('ix_organization_member_user_id', table_name='organization_member')
        op.drop_table('organization_member')
    except Exception:
        pass
    try:
        op.drop_index('ix_organization_slug', table_name='organization')
    except Exception:
        pass
    try:
        op.drop_index('ix_organization_status', table_name='organization')
    except Exception:
        pass
    try:
        op.drop_column('organization', 'slug')
        op.drop_column('organization', 'status')
        op.drop_column('organization', 'updated_at')
    except Exception:
        pass

"""add_call_logger_module

Revision ID: e1f2a3b4c5d6
Revises: d4e5f6a7b8c9
Create Date: 2026-08-17

Adds call_device and call_log tables for the Call Logger & Call Monitoring feature.
This migration is non-destructive — it only adds new tables and indexes.
"""
from alembic import op
import sqlalchemy as sa

revision = 'e1f2a3b4c5d6'
down_revision = '2c46adc1f857'
branch_labels = None
depends_on = None


def upgrade():
    # ── call_device ──────────────────────────────────────────────────────────
    op.create_table(
        'call_device',
        sa.Column('id',              sa.Integer(),    nullable=False),
        sa.Column('organization_id', sa.Integer(),    nullable=False),
        sa.Column('device_name',     sa.String(120),  nullable=False),
        sa.Column('employee_id',     sa.Integer(),    nullable=False),

        # Credential stored as SHA-256 hash only
        sa.Column('credential_hash', sa.String(64), nullable=False),

        # Optional Android metadata
        sa.Column('device_identifier',       sa.String(200), nullable=True),
        sa.Column('subscription_identifier', sa.String(200), nullable=True),

        sa.Column('status', sa.Enum('active', 'revoked', 'inactive', name='devicestatus'),
                  nullable=False, server_default='active'),

        sa.Column('last_seen',  sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(),  nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('credential_hash', name='uq_call_device_credential'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['employee_id'],     ['employee.id']),
        sa.ForeignKeyConstraint(['created_by'],      ['employee.id']),
    )
    op.create_index('ix_call_device_organization_id', 'call_device', ['organization_id'])
    op.create_index('ix_call_device_employee_id',     'call_device', ['employee_id'])
    op.create_index('ix_call_device_status',          'call_device', ['status'])

    # ── call_log ─────────────────────────────────────────────────────────────
    op.create_table(
        'call_log',
        sa.Column('id',              sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('device_id',       sa.Integer(), nullable=False),
        sa.Column('employee_id',     sa.Integer(), nullable=False),

        sa.Column('caller_number',        sa.String(30),  nullable=False),
        sa.Column('caller_name_snapshot', sa.String(150), nullable=True),

        sa.Column('lead_id',    sa.Integer(), nullable=True),
        sa.Column('contact_id', sa.Integer(), nullable=True),

        sa.Column('call_type',   sa.Enum('received', 'missed', 'outgoing', name='calltype'), nullable=False),
        sa.Column('call_status', sa.String(50), nullable=True),

        sa.Column('started_at',      sa.DateTime(), nullable=False),
        sa.Column('ended_at',        sa.DateTime(), nullable=True),
        sa.Column('duration',        sa.Integer(),  nullable=True),
        sa.Column('subscription_id', sa.String(100), nullable=True),

        # Idempotency key from Android app
        sa.Column('client_event_id', sa.String(200), nullable=True),

        sa.Column('follow_up_status',
                  sa.Enum('Pending', 'Completed', 'Not Required', name='callfollowupstatus'),
                  nullable=False, server_default='Not Required'),
        sa.Column('follow_up_notes', sa.Text(), nullable=True),

        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_event_id', name='uq_call_log_client_event'),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['device_id'],   ['call_device.id']),
        sa.ForeignKeyConstraint(['employee_id'], ['employee.id']),
        sa.ForeignKeyConstraint(['lead_id'],     ['lead.id']),
        sa.ForeignKeyConstraint(['contact_id'],  ['contact.id']),
    )
    op.create_index('ix_call_log_organization_id',  'call_log', ['organization_id'])
    op.create_index('ix_call_log_device_id',        'call_log', ['device_id'])
    op.create_index('ix_call_log_employee_id',      'call_log', ['employee_id'])
    op.create_index('ix_call_log_caller_number',    'call_log', ['caller_number'])
    op.create_index('ix_call_log_call_type',        'call_log', ['call_type'])
    op.create_index('ix_call_log_started_at',       'call_log', ['started_at'])
    op.create_index('ix_call_log_follow_up_status', 'call_log', ['follow_up_status'])
    op.create_index('ix_call_log_client_event_id',  'call_log', ['client_event_id'])
    op.create_index('ix_call_log_lead_id',          'call_log', ['lead_id'])
    op.create_index('ix_call_log_contact_id',       'call_log', ['contact_id'])
    # Composite indexes for common dashboard queries
    op.create_index('ix_call_log_org_started',   'call_log', ['organization_id', 'started_at'])
    op.create_index('ix_call_log_employee_type', 'call_log', ['employee_id', 'call_type'])


def downgrade():
    # Drop indexes and tables in reverse order
    op.drop_index('ix_call_log_employee_type',   table_name='call_log')
    op.drop_index('ix_call_log_org_started',     table_name='call_log')
    op.drop_index('ix_call_log_contact_id',      table_name='call_log')
    op.drop_index('ix_call_log_lead_id',         table_name='call_log')
    op.drop_index('ix_call_log_client_event_id', table_name='call_log')
    op.drop_index('ix_call_log_follow_up_status',table_name='call_log')
    op.drop_index('ix_call_log_started_at',      table_name='call_log')
    op.drop_index('ix_call_log_call_type',       table_name='call_log')
    op.drop_index('ix_call_log_caller_number',   table_name='call_log')
    op.drop_index('ix_call_log_employee_id',     table_name='call_log')
    op.drop_index('ix_call_log_device_id',       table_name='call_log')
    op.drop_index('ix_call_log_organization_id', table_name='call_log')
    op.drop_table('call_log')

    op.drop_index('ix_call_device_status',          table_name='call_device')
    op.drop_index('ix_call_device_employee_id',     table_name='call_device')
    op.drop_index('ix_call_device_organization_id', table_name='call_device')
    op.drop_table('call_device')

    # Drop enums (PostgreSQL only — MySQL/SQLite handle this automatically)
    try:
        sa.Enum(name='calltype').drop(op.get_bind())
        sa.Enum(name='callfollowupstatus').drop(op.get_bind())
        sa.Enum(name='devicestatus').drop(op.get_bind())
    except Exception:
        pass

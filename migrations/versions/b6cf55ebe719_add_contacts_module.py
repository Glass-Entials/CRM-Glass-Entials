"""add contacts module

Revision ID: b6cf55ebe719
Revises: f976e8bd498d
Create Date: 2026-07-10 15:23:33.211909

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'b6cf55ebe719'
down_revision = 'f976e8bd498d'
branch_labels = None
depends_on = None


def upgrade():
    # === Create Contact table ===
    op.create_table(
        'contact',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('first_name', sa.String(length=100), nullable=False),
        sa.Column('last_name', sa.String(length=100), nullable=True),
        sa.Column('company', sa.String(length=100), nullable=True),
        sa.Column('designation', sa.String(length=100), nullable=True),
        sa.Column('email', sa.String(length=120), nullable=True),
        sa.Column('phone_number', sa.String(length=20), nullable=False),
        sa.Column('secondary_phone', sa.String(length=20), nullable=True),
        sa.Column('whatsapp_number', sa.String(length=20), nullable=True),
        sa.Column('website', sa.String(length=255), nullable=True),
        sa.Column('address', sa.String(length=255), nullable=True),
        sa.Column('city', sa.String(length=100), nullable=True),
        sa.Column('state', sa.String(length=100), nullable=True),
        sa.Column('country', sa.String(length=100), nullable=True),
        sa.Column('pincode', sa.String(length=20), nullable=True),
        sa.Column('birthday', sa.Date(), nullable=True),
        sa.Column('source', sa.Enum('Website', 'Google', 'Social Media', 'Referral', 'Walk-in', 'Other', name='leadsource'), server_default='Other', nullable=True),
        sa.Column('status', sa.Enum('Contact', 'Follow Up', 'Not Interested', 'Do Not Call', 'Lead', 'Customer', name='contactstatus'), server_default='Contact', nullable=True),
        sa.Column('tags', sa.String(length=255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('lead_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True),
        sa.Column('assigned_to', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('updated_by', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['assigned_to'], ['employee.id']),
        sa.ForeignKeyConstraint(['created_by'], ['employee.id']),
        sa.ForeignKeyConstraint(['lead_id'], ['lead.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.ForeignKeyConstraint(['updated_by'], ['employee.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone_number', 'organization_id', name='uq_contact_phone_org'),
    )
    op.create_index('ix_contact_email', 'contact', ['email'], unique=False)
    op.create_index('ix_contact_organization_id', 'contact', ['organization_id'], unique=False)
    op.create_index('ix_contact_status', 'contact', ['status'], unique=False)
    op.create_index('ix_contact_assigned_to', 'contact', ['assigned_to'], unique=False)
    op.create_index('ix_contact_created_by', 'contact', ['created_by'], unique=False)
    op.create_index('ix_contact_lead_id', 'contact', ['lead_id'], unique=False)

    # === Create ContactActivity table ===
    op.create_table(
        'contact_activity',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('activity_type', sa.Enum('Call', 'Meeting', 'Email', 'Note', 'Task', name='activitytype'), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contact.id']),
        sa.ForeignKeyConstraint(['created_by'], ['employee.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contact_activity_contact_id', 'contact_activity', ['contact_id'], unique=False)
    op.create_index('ix_contact_activity_created_by', 'contact_activity', ['created_by'], unique=False)
    op.create_index('ix_contact_activity_organization_id', 'contact_activity', ['organization_id'], unique=False)

    # === Create ContactNote table ===
    op.create_table(
        'contact_note',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('note', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('created_by', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contact.id']),
        sa.ForeignKeyConstraint(['created_by'], ['employee.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contact_note_contact_id', 'contact_note', ['contact_id'], unique=False)
    op.create_index('ix_contact_note_created_by', 'contact_note', ['created_by'], unique=False)
    op.create_index('ix_contact_note_organization_id', 'contact_note', ['organization_id'], unique=False)

    # === Create ContactSystemLog table ===
    op.create_table(
        'contact_system_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=80), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('icon', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['actor_id'], ['employee.id']),
        sa.ForeignKeyConstraint(['contact_id'], ['contact.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contact_system_log_contact_id', 'contact_system_log', ['contact_id'], unique=False)
    op.create_index('ix_contact_system_log_created_at', 'contact_system_log', ['created_at'], unique=False)
    op.create_index('ix_contact_system_log_actor_id', 'contact_system_log', ['actor_id'], unique=False)
    op.create_index('ix_contact_system_log_organization_id', 'contact_system_log', ['organization_id'], unique=False)

    # === Create ContactDocument table ===
    op.create_table(
        'contact_document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('contact_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('file_type', sa.String(length=50), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contact.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_contact_document_contact_id', 'contact_document', ['contact_id'], unique=False)
    op.create_index('ix_contact_document_organization_id', 'contact_document', ['organization_id'], unique=False)

    # === Add contact_id FK column to lead table ===
    with op.batch_alter_table('lead', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contact_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_lead_contact_id'), ['contact_id'], unique=False)
        batch_op.create_foreign_key(None, 'contact', ['contact_id'], ['id'])


def downgrade():
    with op.batch_alter_table('lead', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_lead_contact_id'))
        batch_op.drop_column('contact_id')

    op.drop_index('ix_contact_document_organization_id', table_name='contact_document')
    op.drop_index('ix_contact_document_contact_id', table_name='contact_document')
    op.drop_table('contact_document')

    op.drop_index('ix_contact_system_log_organization_id', table_name='contact_system_log')
    op.drop_index('ix_contact_system_log_actor_id', table_name='contact_system_log')
    op.drop_index('ix_contact_system_log_created_at', table_name='contact_system_log')
    op.drop_index('ix_contact_system_log_contact_id', table_name='contact_system_log')
    op.drop_table('contact_system_log')

    op.drop_index('ix_contact_note_organization_id', table_name='contact_note')
    op.drop_index('ix_contact_note_created_by', table_name='contact_note')
    op.drop_index('ix_contact_note_contact_id', table_name='contact_note')
    op.drop_table('contact_note')

    op.drop_index('ix_contact_activity_organization_id', table_name='contact_activity')
    op.drop_index('ix_contact_activity_created_by', table_name='contact_activity')
    op.drop_index('ix_contact_activity_contact_id', table_name='contact_activity')
    op.drop_table('contact_activity')

    op.drop_index('ix_contact_lead_id', table_name='contact')
    op.drop_index('ix_contact_created_by', table_name='contact')
    op.drop_index('ix_contact_assigned_to', table_name='contact')
    op.drop_index('ix_contact_status', table_name='contact')
    op.drop_index('ix_contact_organization_id', table_name='contact')
    op.drop_index('ix_contact_email', table_name='contact')
    op.drop_table('contact')

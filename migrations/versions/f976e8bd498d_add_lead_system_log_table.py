"""add lead_system_log table

Revision ID: f976e8bd498d
Revises: 2c1cb23b3038
Create Date: 2026-07-03 14:14:20.984194

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f976e8bd498d'
down_revision = '2c1cb23b3038'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('lead_system_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('event_type', sa.String(length=80), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('icon', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['actor_id'], ['employee.id'], ),
        sa.ForeignKeyConstraint(['lead_id'], ['lead.id'], ),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('lead_system_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_lead_system_log_actor_id'), ['actor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_lead_system_log_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_lead_system_log_lead_id'), ['lead_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_lead_system_log_organization_id'), ['organization_id'], unique=False)


def downgrade():
    with op.batch_alter_table('lead_system_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lead_system_log_organization_id'))
        batch_op.drop_index(batch_op.f('ix_lead_system_log_lead_id'))
        batch_op.drop_index(batch_op.f('ix_lead_system_log_created_at'))
        batch_op.drop_index(batch_op.f('ix_lead_system_log_actor_id'))

    op.drop_table('lead_system_log')

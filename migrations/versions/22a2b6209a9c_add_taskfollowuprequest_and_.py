"""Add TaskFollowupRequest and TaskFollowupResponse tables

Revision ID: 22a2b6209a9c
Revises: a8bacc8ef250
Create Date: 2026-07-25 12:48:56.144918

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '22a2b6209a9c'
down_revision = 'a8bacc8ef250'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('task_followup_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('requested_by', sa.Integer(), nullable=False),
        sa.Column('requested_to', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('priority', sa.Enum('Normal', 'Urgent', name='taskfollowuppriority'), nullable=True),
        sa.Column('status', sa.Enum('Pending', 'Responded', name='taskfollowupstatus'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['requested_by'], ['employee.id'], ),
        sa.ForeignKeyConstraint(['requested_to'], ['employee.id'], ),
        sa.ForeignKeyConstraint(['task_id'], ['task.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task_followup_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_followup_requests_task_id'), ['task_id'], unique=False)

    op.create_table('task_followup_responses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('request_id', sa.Integer(), nullable=False),
        sa.Column('progress_percentage', sa.Integer(), nullable=False),
        sa.Column('current_status', sa.Enum('Pending', 'In Progress', 'Completed', 'Cancelled', name='taskstatus'), nullable=True),
        sa.Column('remark', sa.Text(), nullable=False),
        sa.Column('attachment_path', sa.String(length=255), nullable=True),
        sa.Column('responded_by', sa.Integer(), nullable=False),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['request_id'], ['task_followup_requests.id'], ),
        sa.ForeignKeyConstraint(['responded_by'], ['employee.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('task_followup_responses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_task_followup_responses_request_id'), ['request_id'], unique=False)


def downgrade():
    with op.batch_alter_table('task_followup_responses', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_followup_responses_request_id'))
    op.drop_table('task_followup_responses')

    with op.batch_alter_table('task_followup_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_task_followup_requests_task_id'))
    op.drop_table('task_followup_requests')

"""Add daily_task documents support

Revision ID: 60c3789f0f3f
Revises: c7a429b6e679
Create Date: 2026-04-15 11:01:35.161698

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '60c3789f0f3f'
down_revision = 'c7a429b6e679'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('crm_document', schema=None) as batch_op:
        batch_op.add_column(sa.Column('daily_task_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_crm_document_daily_task_id'), ['daily_task_id'], unique=False)
        batch_op.create_foreign_key(None, 'daily_task', ['daily_task_id'], ['id'])

def downgrade():
    with op.batch_alter_table('crm_document', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_crm_document_daily_task_id'))
        batch_op.drop_column('daily_task_id')


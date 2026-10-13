"""Phase 2: Internal Employee Messaging — persistent tables

Revision ID: a3f8e1c2d9b0
Revises: b4a490582b42
Create Date: 2026-10-12

Creates three new tables:
  msg_conversation        — private chat sessions scoped to an organisation
  msg_conversation_member — participants + per-member last-read tracking
  msg_message             — individual persisted messages

No existing tables are modified or dropped.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a3f8e1c2d9b0'
down_revision = 'b4a490582b42'
branch_labels = None
depends_on = None


def upgrade():
    # ── 1. msg_conversation ─────────────────────────────────────────────
    op.create_table(
        'msg_conversation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('conversation_type', sa.String(length=20),
                  nullable=False, server_default='direct'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'],
                                name='fk_msg_conv_org'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_msg_conv_org_id', 'msg_conversation', ['organization_id'])
    op.create_index('ix_msg_conv_last_message_at', 'msg_conversation', ['last_message_at'])
    op.create_index('ix_msg_conv_org_last', 'msg_conversation',
                    ['organization_id', 'last_message_at'])

    # ── 2. msg_conversation_member ───────────────────────────────────────
    op.create_table(
        'msg_conversation_member',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('employee_id', sa.Integer(), nullable=False),
        sa.Column('joined_at', sa.DateTime(), nullable=True),
        sa.Column('last_read_at', sa.DateTime(), nullable=True),
        sa.Column('last_delivered_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['msg_conversation.id'],
                                name='fk_msg_member_conv'),
        sa.ForeignKeyConstraint(['employee_id'], ['employee.id'],
                                name='fk_msg_member_emp'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('conversation_id', 'employee_id',
                            name='uq_msg_conv_member'),
    )
    op.create_index('ix_msg_conv_member_conv_id', 'msg_conversation_member',
                    ['conversation_id'])
    op.create_index('ix_msg_conv_member_emp_id', 'msg_conversation_member',
                    ['employee_id'])
    op.create_index('ix_msg_conv_member_emp', 'msg_conversation_member',
                    ['employee_id', 'conversation_id'])

    # ── 3. msg_message ───────────────────────────────────────────────────
    op.create_table(
        'msg_message',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('conversation_id', sa.Integer(), nullable=False),
        sa.Column('sender_employee_id', sa.Integer(), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['conversation_id'], ['msg_conversation.id'],
                                name='fk_msg_message_conv'),
        sa.ForeignKeyConstraint(['sender_employee_id'], ['employee.id'],
                                name='fk_msg_message_sender'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_msg_message_conv_id', 'msg_message', ['conversation_id'])
    op.create_index('ix_msg_message_sender_id', 'msg_message', ['sender_employee_id'])
    # Composite index for efficient paginated history (most important)
    op.create_index('ix_msg_message_conv_created', 'msg_message',
                    ['conversation_id', 'created_at'])


def downgrade():
    # Drop in reverse dependency order
    try:
        op.drop_index('ix_msg_message_conv_created', table_name='msg_message')
        op.drop_index('ix_msg_message_sender_id', table_name='msg_message')
        op.drop_index('ix_msg_message_conv_id', table_name='msg_message')
    except Exception:
        pass
    try:
        op.drop_table('msg_message')
    except Exception:
        pass
    try:
        op.drop_index('ix_msg_conv_member_emp', table_name='msg_conversation_member')
        op.drop_index('ix_msg_conv_member_emp_id', table_name='msg_conversation_member')
        op.drop_index('ix_msg_conv_member_conv_id', table_name='msg_conversation_member')
    except Exception:
        pass
    try:
        op.drop_table('msg_conversation_member')
    except Exception:
        pass
    try:
        op.drop_index('ix_msg_conv_org_last', table_name='msg_conversation')
        op.drop_index('ix_msg_conv_last_message_at', table_name='msg_conversation')
        op.drop_index('ix_msg_conv_org_id', table_name='msg_conversation')
    except Exception:
        pass
    try:
        op.drop_table('msg_conversation')
    except Exception:
        pass

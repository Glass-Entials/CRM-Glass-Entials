"""add_attachment_fields_to_msg_message

Revision ID: 2a0680deca16
Revises: a3f8e1c2d9b0
Create Date: 2026-10-13 11:26:30.764520

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2a0680deca16'
down_revision = 'a3f8e1c2d9b0'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_cols = {c['name'] for c in insp.get_columns('msg_message')}

    # Nullable columns matching Message model in model.py
    if 'attachment_url' not in existing_cols:
        op.add_column('msg_message', sa.Column('attachment_url', sa.String(length=500), nullable=True))
    if 'attachment_name' not in existing_cols:
        op.add_column('msg_message', sa.Column('attachment_name', sa.String(length=255), nullable=True))
    if 'attachment_type' not in existing_cols:
        op.add_column('msg_message', sa.Column('attachment_type', sa.String(length=100), nullable=True))
    if 'attachment_size' not in existing_cols:
        op.add_column('msg_message', sa.Column('attachment_size', sa.Integer(), nullable=True))


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_cols = {c['name'] for c in insp.get_columns('msg_message')}

    if 'attachment_size' in existing_cols:
        op.drop_column('msg_message', 'attachment_size')
    if 'attachment_type' in existing_cols:
        op.drop_column('msg_message', 'attachment_type')
    if 'attachment_name' in existing_cols:
        op.drop_column('msg_message', 'attachment_name')
    if 'attachment_url' in existing_cols:
        op.drop_column('msg_message', 'attachment_url')

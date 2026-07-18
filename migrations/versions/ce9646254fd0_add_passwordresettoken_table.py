"""Add PasswordResetToken table

Revision ID: ce9646254fd0
Revises: b6cf55ebe719
Create Date: 2026-07-17 12:02:10.120338

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ce9646254fd0'
down_revision = 'b6cf55ebe719'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'password_reset_token' not in inspector.get_table_names():
        op.create_table('password_reset_token',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('token', sa.String(length=128), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
            sa.PrimaryKeyConstraint('id')
        )
        with op.batch_alter_table('password_reset_token', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_password_reset_token_token'), ['token'], unique=True)
            batch_op.create_index(batch_op.f('ix_password_reset_token_user_id'), ['user_id'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'password_reset_token' in inspector.get_table_names():
        with op.batch_alter_table('password_reset_token', schema=None) as batch_op:
            batch_op.drop_index(batch_op.f('ix_password_reset_token_user_id'))
            batch_op.drop_index(batch_op.f('ix_password_reset_token_token'))
        op.drop_table('password_reset_token')

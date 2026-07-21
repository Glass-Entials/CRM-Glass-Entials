"""Add oauth_provider fields to user

Revision ID: 0bb9d04638ce
Revises: ce9646254fd0
Create Date: 2026-07-21 10:21:58.008122

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0bb9d04638ce'
down_revision = 'ce9646254fd0'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = [c["name"] for c in inspector.get_columns("user")]

    with op.batch_alter_table("user", schema=None) as batch_op:
        if "oauth_provider" not in existing_cols:
            batch_op.add_column(sa.Column("oauth_provider", sa.String(length=32), nullable=True))
        if "oauth_provider_id" not in existing_cols:
            batch_op.add_column(sa.Column("oauth_provider_id", sa.String(length=256), nullable=True))


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("oauth_provider_id")
        batch_op.drop_column("oauth_provider")

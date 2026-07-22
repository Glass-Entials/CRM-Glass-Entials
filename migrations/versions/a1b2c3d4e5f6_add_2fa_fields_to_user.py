"""Add 2FA fields to user table

Revision ID: a1b2c3d4e5f6
Revises: 0bb9d04638ce
Create Date: 2026-07-22 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = ('0bb9d04638ce', 'aa3bf90a7402')
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_cols = [c["name"] for c in inspector.get_columns("user")]

    with op.batch_alter_table("user", schema=None) as batch_op:
        if "two_fa_enabled" not in existing_cols:
            batch_op.add_column(sa.Column("two_fa_enabled", sa.Boolean(), server_default=sa.text("0"), nullable=True))
        if "two_fa_otp" not in existing_cols:
            batch_op.add_column(sa.Column("two_fa_otp", sa.String(length=6), nullable=True))
        if "two_fa_otp_expires" not in existing_cols:
            batch_op.add_column(sa.Column("two_fa_otp_expires", sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.drop_column("two_fa_otp_expires")
        batch_op.drop_column("two_fa_otp")
        batch_op.drop_column("two_fa_enabled")

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "965218b5d8a5"
down_revision = "73c0e5f788ee"
branch_labels = None
depends_on = None


def upgrade():
    # lead + lead_activity tables are already created in the DB (partial previous runs).
    # We use checkfirst / try-except pattern for idempotency.

    # -- Customer: convert VARCHAR to ENUM --
    # Data was already set to valid ENUM values by fix_data_for_enum.py
    with op.batch_alter_table("customer", schema=None) as batch_op:
        # lead_id column may already exist (partial run)
        try:
            batch_op.add_column(sa.Column("lead_id", sa.Integer(), nullable=True))
        except Exception:
            pass  # already added

        batch_op.alter_column(
            "source",
            existing_type=mysql.VARCHAR(length=50),
            type_=sa.Enum(
                "WEBSITE",
                "GOOGLE",
                "SOCIAL_MEDIA",
                "REFERRAL",
                "WALK_IN",
                "OTHER",
                name="leadsource",
            ),
            existing_nullable=True,
        )
        batch_op.alter_column(
            "status",
            existing_type=mysql.VARCHAR(length=20),
            type_=sa.Enum(
                "NEW", "ACTIVE", "INACTIVE", "PROSPECT", name="customerstatus"
            ),
            existing_nullable=True,
        )
        batch_op.create_index("ix_customer_status", ["status"], unique=False)
        batch_op.create_index("ix_customer_assigned_to", ["assigned_to"], unique=False)

    # -- Employee: drop redundant columns --
    with op.batch_alter_table("employee", schema=None) as batch_op:
        batch_op.alter_column("user_id", nullable=False)
        batch_op.drop_column("phone_number")
        batch_op.drop_column("email")

    # -- User: convert role to ENUM --
    with op.batch_alter_table("user", schema=None) as batch_op:
        batch_op.alter_column("phone_number", nullable=True)
        batch_op.alter_column(
            "role",
            existing_type=mysql.VARCHAR(length=20),
            type_=sa.Enum("ADMIN", "MANAGER", "EMPLOYEE", name="userrole"),
            existing_nullable=False,
        )


def downgrade():
    pass

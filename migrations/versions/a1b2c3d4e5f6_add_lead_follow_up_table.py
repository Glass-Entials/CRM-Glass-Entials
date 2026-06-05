"""Add lead_follow_up table

Revision ID: a1b2c3d4e5f6
Revises: 60c3789f0f3f
Create Date: 2026-04-16 11:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "60c3789f0f3f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lead_follow_up",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column(
            "method",
            sa.Enum(
                "Call",
                "Email",
                "WhatsApp",
                "Meeting",
                "Site Visit",
                "SMS",
                "Other",
                name="followupmethod",
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "Interested",
                "Not Interested",
                "Callback Requested",
                "No Response",
                "Meeting Scheduled",
                "Converted",
                "Other",
                name="followupoutcome",
            ),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("follow_up_date", sa.DateTime(), nullable=False),
        sa.Column("next_follow_up_date", sa.DateTime(), nullable=True),
        sa.Column("is_done", sa.Boolean(), nullable=True),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["employee.id"],
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["lead.id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("lead_follow_up", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_lead_follow_up_lead_id"), ["lead_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_lead_follow_up_organization_id"),
            ["organization_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_lead_follow_up_created_by"), ["created_by"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_lead_follow_up_is_done"), ["is_done"], unique=False
        )


def downgrade():
    with op.batch_alter_table("lead_follow_up", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_lead_follow_up_is_done"))
        batch_op.drop_index(batch_op.f("ix_lead_follow_up_created_by"))
        batch_op.drop_index(batch_op.f("ix_lead_follow_up_organization_id"))
        batch_op.drop_index(batch_op.f("ix_lead_follow_up_lead_id"))
    op.drop_table("lead_follow_up")

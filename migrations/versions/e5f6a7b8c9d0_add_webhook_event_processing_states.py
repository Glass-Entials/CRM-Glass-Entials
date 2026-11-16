"""add webhook event processing states, ownership fencing, and provider-scoped unique constraint

Revision ID: e5f6a7b8c9d0
Revises: d5e6f7a8b9c0
Create Date: 2026-11-04 14:45:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e5f6a7b8c9d0'
down_revision = 'd5e6f7a8b9c0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('saas_webhook_event', schema=None) as batch_op:
        batch_op.add_column(sa.Column('organization_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('status', sa.String(length=30), nullable=False, server_default='RECEIVED'))
        batch_op.add_column(sa.Column('owner_token', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('owner_expires_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('error_message', sa.Text(), nullable=True))

        # Drop old global unique index on event_id and replace with provider-scoped unique constraint
        try:
            batch_op.drop_index('ix_saas_webhook_event_event_id')
        except Exception:
            pass

        batch_op.create_index('ix_saas_webhook_event_event_id', ['event_id'], unique=False)
        batch_op.create_unique_constraint('uq_saas_webhook_provider_event', ['payment_provider', 'event_id'])
        batch_op.create_foreign_key('fk_saas_webhook_event_org', 'organization', ['organization_id'], ['id'], ondelete='RESTRICT')
        batch_op.create_index('ix_saas_webhook_event_status', ['status'], unique=False)
        batch_op.create_index('ix_saas_webhook_event_owner_token', ['owner_token'], unique=False)
        batch_op.create_index('ix_saas_webhook_event_owner_expires', ['owner_expires_at'], unique=False)
        batch_op.create_index('ix_saas_webhook_event_organization_id', ['organization_id'], unique=False)


def downgrade():
    bind = op.get_bind()
    # Check for duplicate event_ids across providers before recreating the global unique index.
    # To prevent silent event history loss and preserve V3 compatibility,
    # disambiguate duplicate event records deterministically by appending
    # payment_provider and row id (with collision-avoidance suffixes) rather than deleting.
    # Data transformation failures are strictly raised (never silently swallowed).
    dup_events_res = bind.execute(sa.text(
        "SELECT event_id FROM saas_webhook_event GROUP BY event_id HAVING COUNT(*) > 1"
    )).fetchall()
    dup_event_ids = [r[0] for r in dup_events_res if r[0]]

    for dup_event_id in dup_event_ids:
        rows = bind.execute(
            sa.text("SELECT id, payment_provider FROM saas_webhook_event WHERE event_id = :eid ORDER BY id ASC"),
            {"eid": dup_event_id}
        ).fetchall()
        # Keep the earliest row (index 0) with original event_id intact.
        # Disambiguate subsequent duplicates (index 1+) with deterministic transformation.
        for row in rows[1:]:
            row_id = row[0]
            provider = row[1] or "provider"
            candidate_id = f"{dup_event_id}_{provider}_{row_id}"
            suffix_counter = 0
            while True:
                test_id = candidate_id if suffix_counter == 0 else f"{candidate_id}_{suffix_counter}"
                collision = bind.execute(
                    sa.text("SELECT COUNT(*) FROM saas_webhook_event WHERE event_id = :tid"),
                    {"tid": test_id}
                ).scalar()
                if collision == 0:
                    break
                suffix_counter += 1

            update_res = bind.execute(
                sa.text("UPDATE saas_webhook_event SET event_id = :new_id WHERE id = :row_id"),
                {"new_id": test_id, "row_id": row_id}
            )
            if update_res.rowcount != 1:
                raise RuntimeError(
                    f"Failed to update duplicate event_id for row {row_id}: expected 1 updated row, got {update_res.rowcount}"
                )

    # Verification: Ensure zero duplicate event_ids remain before recreating the global unique constraint
    remaining_dups = bind.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT event_id FROM saas_webhook_event GROUP BY event_id HAVING COUNT(*) > 1) AS dups"
    )).scalar()
    if remaining_dups and remaining_dups > 0:
        raise RuntimeError(
            f"Downgrade collision check failed: {remaining_dups} duplicate event_id groups remain. "
            "Cannot safely recreate global unique constraint."
        )

    with op.batch_alter_table('saas_webhook_event', schema=None) as batch_op:
        batch_op.drop_constraint('fk_saas_webhook_event_org', type_='foreignkey')
        batch_op.drop_constraint('uq_saas_webhook_provider_event', type_='unique')
        batch_op.drop_index('ix_saas_webhook_event_organization_id')
        batch_op.drop_index('ix_saas_webhook_event_owner_expires')
        batch_op.drop_index('ix_saas_webhook_event_owner_token')
        batch_op.drop_index('ix_saas_webhook_event_status')
        batch_op.drop_index('ix_saas_webhook_event_event_id')
        batch_op.create_index('ix_saas_webhook_event_event_id', ['event_id'], unique=True)
        batch_op.drop_column('error_message')
        batch_op.drop_column('owner_expires_at')
        batch_op.drop_column('owner_token')
        batch_op.drop_column('status')
        batch_op.drop_column('organization_id')

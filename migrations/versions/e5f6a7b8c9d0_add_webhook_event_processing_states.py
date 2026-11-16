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
    # disambiguate duplicate event records by appending payment_provider and id rather than deleting.
    try:
        dup_rows = bind.execute(sa.text(
            "SELECT id, event_id, payment_provider FROM saas_webhook_event "
            "WHERE event_id IN ("
            "    SELECT event_id FROM saas_webhook_event GROUP BY event_id HAVING COUNT(*) > 1"
            ") ORDER BY id ASC"
        )).fetchall()
        seen_events = set()
        for row in dup_rows:
            row_id, evt_id, provider = row[0], row[1], row[2]
            if evt_id in seen_events:
                new_evt_id = f"{evt_id}_{provider}_{row_id}"
                bind.execute(
                    sa.text("UPDATE saas_webhook_event SET event_id = :new_id WHERE id = :row_id"),
                    {"new_id": new_evt_id, "row_id": row_id}
                )
            else:
                seen_events.add(evt_id)
    except Exception:
        pass

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

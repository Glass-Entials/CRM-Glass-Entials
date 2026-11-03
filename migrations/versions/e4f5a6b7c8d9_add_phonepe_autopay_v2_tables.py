"""add_phonepe_autopay_v2_tables

Revision ID: e4f5a6b7c8d9
Revises: b7e9a1c2d3f4
Create Date: 2026-11-03 16:30:00.000000

PhonePe AutoPay V2 Migration:
- Enforces multi-tenant ownership via composite unique constraints and RESTRICT foreign keys.
- Step 1: Add composite UNIQUE constraint on saas_subscription(id, organization_id)
- Step 2: Create saas_mandate table (RESTRICT FK to saas_subscription(id, organization_id))
- Step 3: Add active_mandate_id, claim_token, claim_expires_at to saas_subscription (RESTRICT FK to saas_mandate(id, organization_id))
- Step 4: Create saas_pre_debit_notification table (RESTRICT FKs)
- Step 5: Create saas_redemption_attempt table (composite unique on id, org_id; RESTRICT FKs)
- Step 6: Add mandate_id and redemption_attempt_id to saas_transaction (RESTRICT FKs)
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4f5a6b7c8d9'
down_revision = 'b7e9a1c2d3f4'
branch_labels = None
depends_on = None


def upgrade():
    # ── Step 1: Add composite unique key on existing parent table saas_subscription ──
    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_saas_sub_id_org', ['id', 'organization_id'])

    # ── Step 2: Create child table saas_mandate ──
    op.create_table(
        'saas_mandate',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('payment_provider', sa.String(length=50), nullable=False, server_default='phonepe_v2'),
        sa.Column('merchant_subscription_id', sa.String(length=100), nullable=False),
        sa.Column('provider_subscription_id', sa.String(length=100), nullable=True),
        sa.Column('mandate_reference', sa.String(length=100), nullable=True),
        sa.Column('mandate_status', sa.String(length=50), nullable=False, server_default='INITIATED'),
        sa.Column('auth_workflow_type', sa.String(length=50), nullable=False, server_default='TRANSACTION'),
        sa.Column('amount_type', sa.String(length=20), nullable=False, server_default='VARIABLE'),
        sa.Column('max_amount_paise', sa.BigInteger(), nullable=False),
        sa.Column('frequency', sa.String(length=20), nullable=False),
        sa.Column('auto_debit', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('start_date', sa.DateTime(), nullable=True),
        sa.Column('end_date', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'organization_id', name='uq_saas_mandate_id_org'),
        sa.UniqueConstraint('organization_id', 'merchant_subscription_id', name='uq_saas_mandate_merchant_sub'),
        sa.ForeignKeyConstraint(
            ['subscription_id', 'organization_id'],
            ['saas_subscription.id', 'saas_subscription.organization_id'],
            name='fk_saas_mandate_sub_org',
            ondelete='RESTRICT'
        ),
    )
    op.create_index('ix_saas_mandate_status', 'saas_mandate', ['mandate_status'])
    op.create_index('ix_saas_mandate_org', 'saas_mandate', ['organization_id'])

    # ── Step 3: Extend saas_subscription with active_mandate_id and worker claim columns ──
    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.add_column(sa.Column('active_mandate_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('claim_token', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('claim_expires_at', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key(
            'fk_saas_sub_active_mandate_org',
            'saas_mandate',
            ['active_mandate_id', 'organization_id'],
            ['id', 'organization_id'],
            ondelete='RESTRICT'
        )
        batch_op.create_index('ix_saas_sub_claim', ['claim_token', 'claim_expires_at'])

    # ── Step 4: Create child table saas_pre_debit_notification ──
    op.create_table(
        'saas_pre_debit_notification',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('mandate_id', sa.Integer(), nullable=False),
        sa.Column('merchant_order_id', sa.String(length=100), nullable=False),
        sa.Column('phonepe_order_id', sa.String(length=100), nullable=True),
        sa.Column('amount_paise', sa.BigInteger(), nullable=False),
        sa.Column('auto_debit_snapshot', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('valid_after_at', sa.DateTime(), nullable=True),
        sa.Column('valid_upto_at', sa.DateTime(), nullable=True),
        sa.Column('notified_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='INITIATED'),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('raw_response_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'organization_id', name='uq_saas_pdn_id_org'),
        sa.UniqueConstraint('organization_id', 'merchant_order_id', name='uq_saas_pdn_order'),
        sa.ForeignKeyConstraint(
            ['mandate_id', 'organization_id'],
            ['saas_mandate.id', 'saas_mandate.organization_id'],
            name='fk_saas_pdn_mandate_org',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['subscription_id', 'organization_id'],
            ['saas_subscription.id', 'saas_subscription.organization_id'],
            name='fk_saas_pdn_sub_org',
            ondelete='RESTRICT'
        ),
    )
    op.create_index('ix_saas_pdn_status', 'saas_pre_debit_notification', ['status'])
    op.create_index('ix_saas_pdn_org', 'saas_pre_debit_notification', ['organization_id'])

    # ── Step 5: Create child table saas_redemption_attempt ──
    op.create_table(
        'saas_redemption_attempt',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('subscription_id', sa.Integer(), nullable=False),
        sa.Column('notification_id', sa.Integer(), nullable=False),
        sa.Column('merchant_order_id', sa.String(length=100), nullable=False),
        sa.Column('phonepe_transaction_id', sa.String(length=100), nullable=True),
        sa.Column('amount_paise', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default='INITIATED'),
        sa.Column('attempt_number', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('failure_reason', sa.Text(), nullable=True),
        sa.Column('raw_response_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'organization_id', name='uq_saas_redemption_id_org'),
        sa.UniqueConstraint('organization_id', 'merchant_order_id', 'attempt_number', name='uq_saas_redemption_attempt'),
        sa.ForeignKeyConstraint(
            ['notification_id', 'organization_id'],
            ['saas_pre_debit_notification.id', 'saas_pre_debit_notification.organization_id'],
            name='fk_saas_redemption_pdn_org',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['subscription_id', 'organization_id'],
            ['saas_subscription.id', 'saas_subscription.organization_id'],
            name='fk_saas_redemption_sub_org',
            ondelete='RESTRICT'
        ),
    )
    op.create_index('ix_saas_redemption_status', 'saas_redemption_attempt', ['status'])
    op.create_index('ix_saas_redemption_org', 'saas_redemption_attempt', ['organization_id'])

    # ── Step 6: Extend saas_transaction with mandate and redemption linkages ──
    with op.batch_alter_table('saas_transaction', schema=None) as batch_op:
        batch_op.add_column(sa.Column('mandate_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('redemption_attempt_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_saas_tx_mandate_org',
            'saas_mandate',
            ['mandate_id', 'organization_id'],
            ['id', 'organization_id'],
            ondelete='RESTRICT'
        )
        batch_op.create_foreign_key(
            'fk_saas_tx_sub_org',
            'saas_subscription',
            ['subscription_id', 'organization_id'],
            ['id', 'organization_id'],
            ondelete='RESTRICT'
        )
        batch_op.create_foreign_key(
            'fk_saas_tx_redemption_org',
            'saas_redemption_attempt',
            ['redemption_attempt_id', 'organization_id'],
            ['id', 'organization_id'],
            ondelete='RESTRICT'
        )
        batch_op.create_index('ix_saas_transaction_mandate_id', ['mandate_id'])
        batch_op.create_index('ix_saas_transaction_redemption_attempt_id', ['redemption_attempt_id'])


def downgrade():
    # ── Reverse Step 6: Drop FKs and columns from saas_transaction ──
    with op.batch_alter_table('saas_transaction', schema=None) as batch_op:
        batch_op.drop_constraint('fk_saas_tx_redemption_org', type_='foreignkey')
        batch_op.drop_constraint('fk_saas_tx_sub_org', type_='foreignkey')
        batch_op.drop_constraint('fk_saas_tx_mandate_org', type_='foreignkey')
        batch_op.drop_index('ix_saas_transaction_redemption_attempt_id')
        batch_op.drop_index('ix_saas_transaction_mandate_id')
        batch_op.drop_column('redemption_attempt_id')
        batch_op.drop_column('mandate_id')

    # ── Reverse Step 5: Drop saas_redemption_attempt table ──
    op.drop_index('ix_saas_redemption_org', table_name='saas_redemption_attempt')
    op.drop_index('ix_saas_redemption_status', table_name='saas_redemption_attempt')
    op.drop_table('saas_redemption_attempt')

    # ── Reverse Step 4: Drop saas_pre_debit_notification table ──
    op.drop_index('ix_saas_pdn_org', table_name='saas_pre_debit_notification')
    op.drop_index('ix_saas_pdn_status', table_name='saas_pre_debit_notification')
    op.drop_table('saas_pre_debit_notification')

    # ── Reverse Step 3: Drop active_mandate_id, claim columns and FK from saas_subscription ──
    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.drop_constraint('fk_saas_sub_active_mandate_org', type_='foreignkey')
        batch_op.drop_index('ix_saas_sub_claim')
        batch_op.drop_column('claim_expires_at')
        batch_op.drop_column('claim_token')
        batch_op.drop_column('active_mandate_id')

    # ── Reverse Step 2: Drop saas_mandate table ──
    op.drop_index('ix_saas_mandate_org', table_name='saas_mandate')
    op.drop_index('ix_saas_mandate_status', table_name='saas_mandate')
    op.drop_table('saas_mandate')

    # ── Reverse Step 1: Drop composite UNIQUE key from saas_subscription(id, organization_id) ──
    with op.batch_alter_table('saas_subscription', schema=None) as batch_op:
        batch_op.drop_constraint('uq_saas_sub_id_org', type_='unique')

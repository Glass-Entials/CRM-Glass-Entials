"""per_org_unique_constraints, invoice_counter, must_change_password

Revision ID: e2a3c4d5b6f7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-05

Changes:
- Add user.must_change_password (Boolean, default False) — skipped if already exists
- Add quotation_settings.invoice_counter (Integer, default 1) — skipped if already exists
- Remove global UNIQUE on lead.email / phone_number; add per-org composite uniques
- Remove global UNIQUE on customer.email / phone_number; add per-org composite uniques
- Make lead.organization_id and customer.organization_id NOT NULL
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# revision identifiers
revision = 'e2a3c4d5b6f7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


# ── Helpers (SQLAlchemy 2.x compatible) ──────────────────────────────────────

def get_inspector():
    """Return an Inspector bound to the active migration connection."""
    return sa_inspect(op.get_bind())


def column_exists(table, column):
    return column in [c['name'] for c in get_inspector().get_columns(table)]


def index_exists(table, index_name):
    return index_name in [i['name'] for i in get_inspector().get_indexes(table)]


def unique_constraint_exists(table, constraint_name):
    return constraint_name in [
        uc['name'] for uc in get_inspector().get_unique_constraints(table)
    ]


# ─────────────────────────────────────────────────────────────────────────────

def upgrade():

    # ── 1. user.must_change_password ─────────────────────────────────────────
    if not column_exists('user', 'must_change_password'):
        op.add_column(
            'user',
            sa.Column('must_change_password', sa.Boolean(), nullable=True)
        )
        op.execute("UPDATE `user` SET must_change_password = 0 "
                   "WHERE must_change_password IS NULL")
        op.alter_column(
            'user', 'must_change_password',
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.false()
        )
    else:
        # Column already exists — just ensure no NULLs remain
        op.execute("UPDATE `user` SET must_change_password = 0 "
                   "WHERE must_change_password IS NULL")

    # ── 2. quotation_settings.invoice_counter ────────────────────────────────
    if not column_exists('quotation_settings', 'invoice_counter'):
        op.add_column(
            'quotation_settings',
            sa.Column('invoice_counter', sa.Integer(), nullable=True,
                      server_default='1')
        )
        op.execute("UPDATE quotation_settings SET invoice_counter = 1 "
                   "WHERE invoice_counter IS NULL")

    # ── 3. lead — per-org unique constraints ─────────────────────────────────
    #    Collect current index / constraint names once
    lead_indexes     = {i['name'] for i in get_inspector().get_indexes('lead')}
    lead_ucs         = {uc['name'] for uc in get_inspector().get_unique_constraints('lead')}
    lead_cols        = {c['name']: c for c in get_inspector().get_columns('lead')}

    # Drop old global unique index on email (if it exists and is unique)
    if 'ix_lead_email' in lead_indexes:
        op.drop_index('ix_lead_email', table_name='lead')

    # Drop old named unique constraints
    for uc in ('uq_lead_email', 'uq_lead_phone_number'):
        if uc in lead_ucs:
            op.drop_constraint(uc, table_name='lead', type_='unique')

    # Make organization_id NOT NULL if currently nullable
    org_col = lead_cols.get('organization_id', {})
    if org_col.get('nullable', True):
        op.execute("UPDATE lead SET organization_id = 1 WHERE organization_id IS NULL")
        op.alter_column(
            'lead', 'organization_id',
            existing_type=sa.Integer(),
            nullable=False
        )

    # Add per-org composite unique constraints
    if 'uq_lead_email_org' not in lead_ucs:
        op.create_unique_constraint(
            'uq_lead_email_org', 'lead', ['email', 'organization_id']
        )
    if 'uq_lead_phone_org' not in lead_ucs:
        op.create_unique_constraint(
            'uq_lead_phone_org', 'lead', ['phone_number', 'organization_id']
        )

    # Re-create non-unique index on email (for fast lookups)
    # Re-read indexes after drops
    lead_indexes_now = {i['name'] for i in get_inspector().get_indexes('lead')}
    if 'ix_lead_email' not in lead_indexes_now:
        op.create_index('ix_lead_email', 'lead', ['email'])

    # ── 4. customer — per-org unique constraints ──────────────────────────────
    cust_indexes = {i['name'] for i in get_inspector().get_indexes('customer')}
    cust_ucs     = {uc['name'] for uc in get_inspector().get_unique_constraints('customer')}
    cust_cols    = {c['name']: c for c in get_inspector().get_columns('customer')}

    # Drop old global unique index on email
    if 'ix_customer_email' in cust_indexes:
        op.drop_index('ix_customer_email', table_name='customer')

    # Drop old named unique constraints
    for uc in ('uq_customer_email', 'uq_customer_phone_number'):
        if uc in cust_ucs:
            op.drop_constraint(uc, table_name='customer', type_='unique')

    # Make organization_id NOT NULL if currently nullable
    org_col = cust_cols.get('organization_id', {})
    if org_col.get('nullable', True):
        op.execute("UPDATE customer SET organization_id = 1 WHERE organization_id IS NULL")
        op.alter_column(
            'customer', 'organization_id',
            existing_type=sa.Integer(),
            nullable=False
        )

    # Add per-org composite unique constraints
    if 'uq_customer_email_org' not in cust_ucs:
        op.create_unique_constraint(
            'uq_customer_email_org', 'customer', ['email', 'organization_id']
        )
    if 'uq_customer_phone_org' not in cust_ucs:
        op.create_unique_constraint(
            'uq_customer_phone_org', 'customer', ['phone_number', 'organization_id']
        )

    # Re-create non-unique index on email
    cust_indexes_now = {i['name'] for i in get_inspector().get_indexes('customer')}
    if 'ix_customer_email' not in cust_indexes_now:
        op.create_index('ix_customer_email', 'customer', ['email'])


def downgrade():
    # ── 4. customer — restore global unique on email ─────────────────────────
    try:
        op.drop_constraint('uq_customer_email_org', 'customer', type_='unique')
    except Exception:
        pass
    try:
        op.drop_constraint('uq_customer_phone_org', 'customer', type_='unique')
    except Exception:
        pass
    try:
        op.drop_index('ix_customer_email', table_name='customer')
    except Exception:
        pass
    op.alter_column('customer', 'organization_id',
                    existing_type=sa.Integer(), nullable=True)
    op.create_index('ix_customer_email', 'customer', ['email'], unique=True)

    # ── 3. lead — restore global unique on email ──────────────────────────────
    try:
        op.drop_constraint('uq_lead_email_org', 'lead', type_='unique')
    except Exception:
        pass
    try:
        op.drop_constraint('uq_lead_phone_org', 'lead', type_='unique')
    except Exception:
        pass
    try:
        op.drop_index('ix_lead_email', table_name='lead')
    except Exception:
        pass
    op.alter_column('lead', 'organization_id',
                    existing_type=sa.Integer(), nullable=True)
    op.create_index('ix_lead_email', 'lead', ['email'], unique=True)

    # ── 2. quotation_settings.invoice_counter ─────────────────────────────────
    try:
        op.drop_column('quotation_settings', 'invoice_counter')
    except Exception:
        pass

    # ── 1. user.must_change_password ──────────────────────────────────────────
    try:
        op.drop_column('user', 'must_change_password')
    except Exception:
        pass

"""Remove global unique constraints

Revision ID: c04eef1c535e
Revises: 9886b7ca6c4f
Create Date: 2026-03-28 16:44:15.966263

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c04eef1c535e'
down_revision = '9886b7ca6c4f'
branch_labels = None
depends_on = None


def upgrade():
    try: op.execute("ALTER TABLE customer DROP INDEX ix_customer_email")
    except Exception: pass
    try: op.execute("ALTER TABLE customer DROP INDEX email")
    except Exception: pass
    try: op.execute("ALTER TABLE customer DROP INDEX phone_number")
    except Exception: pass
    
    try: op.execute("ALTER TABLE lead DROP INDEX ix_lead_email")
    except Exception: pass
    try: op.execute("ALTER TABLE lead DROP INDEX email")
    except Exception: pass
    try: op.execute("ALTER TABLE lead DROP INDEX phone_number")
    except Exception: pass

    try: op.execute("ALTER TABLE customer ADD INDEX ix_customer_email (email)")
    except Exception: pass
    try: op.execute("ALTER TABLE lead ADD INDEX ix_lead_email (email)")
    except Exception: pass

def downgrade():
    with op.batch_alter_table('lead', schema=None) as batch_op:
        batch_op.create_index('phone_number', ['phone_number'], unique=True)
        batch_op.create_index('email', ['email'], unique=True)

    with op.batch_alter_table('customer', schema=None) as batch_op:
        batch_op.create_index('phone_number', ['phone_number'], unique=True)
        batch_op.create_index('email', ['email'], unique=True)


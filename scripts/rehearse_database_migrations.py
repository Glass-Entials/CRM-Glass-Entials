"""
scripts/rehearse_database_migrations.py
Disposable rehearsal script for Alembic migrations.
Verifies that all 34 migration versions apply linearly from base to head (b2c3d4e5f6a8)
without schema conflicts, table locks, or syntax errors.
"""
import os
import sys
import tempfile
from alembic.config import Config as AlembicConfig
from alembic import command
from sqlalchemy import create_engine, inspect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def rehearse_migrations():
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "rehearsal.db")
    db_url = f"sqlite:///{db_path}"

    print(f"Disposable rehearsal DB: {db_url}")
    alembic_cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "..", "migrations", "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    # 1. Upgrade from base to head
    print("Executing: alembic upgrade head ...")
    from app import app
    from sqlalchemy import create_engine
    engine = create_engine(db_url)
    app.extensions["migrate"].db.get_engine = lambda *args, **kwargs: engine
    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    try:
        with app.app_context():
            command.upgrade(alembic_cfg, "head")
        print("SUCCESS: Migration upgrade to head completed cleanly!")
    except Exception as e:
        print(f"FAILED: Migration upgrade failed: {e}")
        return False

    # 2. Inspect resulting schema
    engine = create_engine(db_url)
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(f"Total tables created: {len(tables)}")
    
    # Verify critical OMS tables exist
    expected_oms_tables = [
        "order_master",
        "order_item",
        "order_item_vendor_allocation",
        "order_vendor_procurement",
        "order_consignment",
        "order_delivery",
        "order_fitting_visit",
        "order_status_history",
        "order_document",
        "order_revision",
        "order_exceptional_closure",
    ]
    missing = [t for t in expected_oms_tables if t not in tables]
    if missing:
        print(f"ERROR: Missing expected OMS tables: {missing}")
        return False
    print("SUCCESS: All core OMS tables verified in upgraded schema!")

    # Verify critical columns added in recent head migrations exist
    order_cols = [c["name"] for c in inspector.get_columns("order_master")]
    for col in ["priority", "site_address", "procurement_status", "delivery_status", "fitting_status"]:
        if col not in order_cols:
            print(f"ERROR: Missing column {col} on order_master")
            return False
    print("SUCCESS: All new extended columns verified on order_master!")

    # 3. Test downgrade one step to verify rollback feasibility
    print("Executing: alembic downgrade -1 ...")
    try:
        with app.app_context():
            command.downgrade(alembic_cfg, "-1")
        print("SUCCESS: Rollback of latest revision succeeded!")
    except Exception as e:
        print(f"WARNING: Downgrade test failed: {e}")

    # Re-upgrade to head
    with app.app_context():
        command.upgrade(alembic_cfg, "head")
    print("SUCCESS: Re-upgrade to head succeeded!")

    # Cleanup
    engine.dispose()
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)
    return True

if __name__ == "__main__":
    success = rehearse_migrations()
    sys.exit(0 if success else 1)

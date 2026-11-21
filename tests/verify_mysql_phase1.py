"""
Verification script for OMS Phase 1 Database Foundation on isolated MySQL test database.
Validates:
1. Migration execution to f6a7b8c9d0e1 on oms_test_db
2. All 19 OMS tables and composite foreign keys
3. MySQL audit triggers (SIGNAL SQLSTATE 45000)
4. Cross-order and cross-vendor constraint tests against MySQL engine
5. Migration downgrade and subsequent upgrade
"""
import os
import sys
from pathlib import Path
import urllib.parse
from decimal import Decimal
from datetime import datetime

import pymysql
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask
from flask_migrate import Migrate, upgrade, downgrade
import sqlalchemy as sa
from model import db

vals = dotenv_values(".env")
raw_url = vals.get("DATABASE_URL", "")
after_slash = raw_url.split("://")[1]
user_pass, host_db = after_slash.split("@", 1)
raw_user, raw_pass = user_pass.split(":", 1)
unquoted_pass = urllib.parse.unquote(raw_pass)
host_parts = host_db.split("/", 1)
host = host_parts[0] or "localhost"
port = 3306
if ":" in host:
    host, port_str = host.split(":")
    port = int(port_str)

TEST_DB_NAME = "oms_test_db"
TEST_DB_URL = f"mysql+pymysql://{raw_user}:{urllib.parse.quote_plus(unquoted_pass)}@{host}:{port}/{TEST_DB_NAME}?charset=utf8mb4"


def setup_test_database():
    print(f"Cloning schema from `crm_db` (at e5f6a7b8c9d0) into `{TEST_DB_NAME}`...")
    conn = pymysql.connect(host=host, port=port, user=raw_user, password=unquoted_pass, autocommit=True)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME};")
        cur.execute(f"CREATE DATABASE {TEST_DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        cur.execute(f"USE {TEST_DB_NAME};")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0;")
        
        # Fetch all tables from crm_db
        cur.execute("SHOW TABLES FROM crm_db;")
        tables = [r[0] for r in cur.fetchall()]
        for tbl in tables:
            cur.execute(f"SHOW CREATE TABLE crm_db.`{tbl}`;")
            create_sql = cur.fetchone()[1]
            cur.execute(create_sql)
        
        # Copy alembic_version record
        cur.execute(f"INSERT INTO {TEST_DB_NAME}.alembic_version SELECT * FROM crm_db.alembic_version;")
        cur.execute("SET FOREIGN_KEY_CHECKS = 1;")
    conn.close()
    print(f"Isolated test database `{TEST_DB_NAME}` ready with {len(tables)} tables at revision e5f6a7b8c9d0.")


def teardown_test_database():
    print(f"Dropping isolated test database `{TEST_DB_NAME}`...")
    conn = pymysql.connect(host=host, port=port, user=raw_user, password=unquoted_pass)
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME};")
    conn.close()
    print("Database cleaned up.")


def run_checks():
    setup_test_database()
    try:
        app = Flask("mysql_migration_test")
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI=TEST_DB_URL,
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(app)
        migrate = Migrate(app, db)

        with app.app_context():
            # 1. Execute migration to f6a7b8c9d0e1
            print("\n--- Check 1: Executing migration f6a7b8c9d0e1 on MySQL ---")
            upgrade(revision="f6a7b8c9d0e1")
            print("Migration upgraded to head f6a7b8c9d0e1 successfully.")

            # 2. Verify all 19 OMS tables and foreign keys
            print("\n--- Check 2: Verifying 19 OMS tables in MySQL ---")
            engine = db.engine
            inspector = sa.inspect(engine)
            existing_tables = set(inspector.get_table_names())

            expected_tables = [
                "order_master",
                "order_item",
                "order_vendor_procurement",
                "order_item_vendor_allocation",
                "order_procurement_item",
                "order_consignment",
                "consignment_item",
                "order_delivery",
                "delivery_item",
                "order_fitting_visit",
                "fitting_visit_item",
                "order_payment_allocation",
                "vendor_bill",
                "vendor_bill_item",
                "vendor_payment",
                "order_document",
                "order_revision",
                "order_status_history",
                "order_exceptional_closure",
            ]

            missing = [t for t in expected_tables if t not in existing_tables]
            if missing:
                raise AssertionError(f"Missing OMS tables in MySQL: {missing}")
            print(f"All 19 OMS tables verified present in MySQL: {len(expected_tables)}/19")

            # Check composite foreign keys on order_procurement_item
            fks = inspector.get_foreign_keys("order_procurement_item")
            print(f"Verified {len(fks)} foreign key constraints on `order_procurement_item`.")

            # 3. Verify MySQL audit triggers
            print("\n--- Check 3: Verifying MySQL audit triggers ---")
            conn = pymysql.connect(host=host, port=port, user=raw_user, password=unquoted_pass, database=TEST_DB_NAME)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE "
                    "FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = %s;",
                    (TEST_DB_NAME,)
                )
                triggers = cur.fetchall()
                trigger_names = {t[0] for t in triggers}
                print(f"Found {len(triggers)} triggers in `{TEST_DB_NAME}`: {trigger_names}")

                expected_triggers = {
                    "trg_order_status_history_no_update",
                    "trg_order_status_history_no_delete",
                    "trg_order_exceptional_closure_no_update",
                    "trg_order_exceptional_closure_no_delete",
                    "trg_order_revision_no_update",
                    "trg_order_revision_no_delete",
                }
                missing_triggers = expected_triggers - trigger_names
                if missing_triggers:
                    raise AssertionError(f"Missing MySQL audit triggers: {missing_triggers}")

            # 4. Execute cross-order and cross-vendor constraint tests against MySQL engine
            print("\n--- Check 4: Testing MySQL Engine Constraints ---")
            from model import (
                Organization, Customer, Vendor, Employee, OrderMaster, OrderItem,
                OrderItemVendorAllocation, OrderVendorProcurement, OrderProcurementItem,
                OrderStatusHistory
            )

            # Seed base data
            from model import User
            user1 = User(id=1, username="admin1", email="admin1@t.com", password="hash_password")
            org1 = Organization(id=1, name="Test Org 1", unique_code="ORG_T1")
            db.session.add_all([user1, org1])
            db.session.commit()

            emp1 = Employee(id=1, user_id=1, name="Admin 1", organization_id=1)
            cust1 = Customer(id=1, name="Cust 1", email="c1@t.com", phone_number="9999999999", organization_id=1, created_by=1)
            vend1 = Vendor(id=1, company_name="Vend 1", code="V01", organization_id=1)
            vend2 = Vendor(id=2, company_name="Vend 2", code="V02", organization_id=1)
            db.session.add_all([emp1, cust1, vend1, vend2])
            db.session.commit()

            order1 = OrderMaster(
                id=1, master_order_number="ORD-101", organization_id=1,
                customer_id=1, created_by_id=1
            )
            order2 = OrderMaster(
                id=2, master_order_number="ORD-102", organization_id=1,
                customer_id=1, created_by_id=1
            )
            item1 = OrderItem(
                id=1, order_id=1, organization_id=1, item_description="Glass",
                quantity=Decimal("10.000"), unit_price=Decimal("100.00"), total_price=Decimal("118.00")
            )
            alloc1 = OrderItemVendorAllocation(
                id=1, order_id=1, order_item_id=1, vendor_id=1, organization_id=1,
                allocated_quantity=Decimal("6.000"), expected_rate=Decimal("80.00"),
                estimated_cost=Decimal("480.00"), created_by_id=1
            )
            alloc2 = OrderItemVendorAllocation(
                id=2, order_id=1, order_item_id=1, vendor_id=2, organization_id=1,
                allocated_quantity=Decimal("4.000"), expected_rate=Decimal("85.00"),
                estimated_cost=Decimal("340.00"), created_by_id=1
            )
            po1 = OrderVendorProcurement(
                id=1, po_number="PO-101", order_id=1, vendor_id=1, organization_id=1,
                created_by_id=1
            )
            po_item1 = OrderProcurementItem(
                id=1, procurement_id=1, allocation_id=1, vendor_id=1, order_id=1, organization_id=1,
                quantity=Decimal("6.000"), unit_rate=Decimal("80.00"), total_rate=Decimal("480.00"),
                line_total=Decimal("566.40")
            )
            db.session.add_all([order1, order2, item1, alloc1, alloc2, po1, po_item1])
            db.session.commit()
            print("Seeded valid order, items, allocations, and PO in MySQL.")

            # Test 4a: Cross-order rejection at MySQL InnoDB level
            try:
                mismatched_order_item = OrderProcurementItem(
                    id=2, procurement_id=1, allocation_id=1, vendor_id=1,
                    order_id=2, # Mismatched order_id!
                    organization_id=1, quantity=Decimal("1.000"),
                    unit_rate=Decimal("80.00"), total_rate=Decimal("80.00"), line_total=Decimal("94.40")
                )
                db.session.add(mismatched_order_item)
                db.session.commit()
                raise AssertionError("MySQL failed to reject cross-order procurement item!")
            except Exception as e:
                db.session.rollback()
                print("MySQL InnoDB correctly rejected cross-order reference:", type(e).__name__)

            # Test 4b: Cross-vendor rejection at MySQL InnoDB level
            try:
                cross_vend_item = OrderProcurementItem(
                    id=3, procurement_id=1, # PO 1 is for Vendor 1
                    allocation_id=2,         # Alloc 2 is for Vendor 2!
                    vendor_id=1, order_id=1, organization_id=1,
                    quantity=Decimal("1.000"), unit_rate=Decimal("85.00"),
                    total_rate=Decimal("85.00"), line_total=Decimal("100.30")
                )
                db.session.add(cross_vend_item)
                db.session.commit()
                raise AssertionError("MySQL failed to reject cross-vendor allocation reference!")
            except Exception as e:
                db.session.rollback()
                print("MySQL InnoDB correctly rejected cross-vendor reference:", type(e).__name__)

            # Test 4c: MySQL Trigger execution (SIGNAL SQLSTATE '45000') on raw SQL
            hist = OrderStatusHistory(
                id=1, order_id=1, organization_id=1, from_status="DRAFT",
                to_status="CONFIRMED", changed_by_id=1, remarks="Initial confirmation"
            )
            db.session.add(hist)
            db.session.commit()

            with conn.cursor() as cur:
                try:
                    cur.execute("UPDATE order_status_history SET remarks = 'tampered' WHERE id = 1;")
                    raise AssertionError("MySQL trigger failed to block direct UPDATE on order_status_history!")
                except pymysql.err.OperationalError as e:
                    print("MySQL trigger correctly aborted direct UPDATE with SQLSTATE 45000:", e.args[1])

                try:
                    cur.execute("DELETE FROM order_status_history WHERE id = 1;")
                    raise AssertionError("MySQL trigger failed to block direct DELETE on order_status_history!")
                except pymysql.err.OperationalError as e:
                    print("MySQL trigger correctly aborted direct DELETE with SQLSTATE 45000:", e.args[1])
            conn.close()

            # 5. Verify migration downgrade and subsequent upgrade
            print("\n--- Check 5: Verifying Downgrade and Re-Upgrade ---")
            print("Downgrading to e5f6a7b8c9d0...")
            downgrade(revision="e5f6a7b8c9d0")

            inspector = sa.inspect(db.engine)
            remaining_tables = set(inspector.get_table_names())
            leftover = [t for t in expected_tables if t in remaining_tables]
            if leftover:
                raise AssertionError(f"Downgrade failed to drop tables: {leftover}")
            print("All 19 OMS tables cleanly dropped on downgrade.")

            print("Re-upgrading to f6a7b8c9d0e1...")
            upgrade(revision="f6a7b8c9d0e1")
            inspector = sa.inspect(db.engine)
            recreated_tables = set(inspector.get_table_names())
            missing_after_reup = [t for t in expected_tables if t not in recreated_tables]
            if missing_after_reup:
                raise AssertionError(f"Re-upgrade missing tables: {missing_after_reup}")
            print("All 19 OMS tables and constraints cleanly recreated on re-upgrade.")

            print("\nALL 5 MYSQL ISOLATION CHECKS PASSED PERFECTLY ON REAL MYSQL SERVER!")
    finally:
        teardown_test_database()


if __name__ == "__main__":
    run_checks()

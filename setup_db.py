"""
Fresh Database Setup Script
============================
Run this ONCE on a new machine to create all tables directly.
This avoids the MySQL FK circular dependency issue that can occur
when running `flask db upgrade` from scratch.

Usage:
    python setup_db.py

After running this, your database will be fully initialized and
you can use `flask db migrate` / `flask db upgrade` for future changes.
"""

import os
from dotenv import load_dotenv

load_dotenv()

from app import app
from model import db

def setup():
    with app.app_context():
        print("✅ Connected to database:", app.config.get('SQLALCHEMY_DATABASE_URI', '').split('@')[-1])
        print("⏳ Creating all tables...")
        
        # This directly creates all tables in correct dependency order
        # bypassing Alembic's migration ordering issue for fresh installs
        db.create_all()
        
        print("✅ All tables created successfully!")
        print()
        print("📌 Now stamp the migration head so Flask-Migrate knows the DB is up to date:")
        print("   flask db stamp head")
        print()
        print("✅ Setup complete! You can now run: python app.py")

if __name__ == '__main__':
    setup()

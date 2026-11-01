#!/usr/bin/env python3
"""
Hisab AI - Schema & Database Migration Runner
Applies missing tables, indexes, and schema upgrades idempotently.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, engine
from models import Base

def run_migrations():
    """Verify and apply all table migrations."""
    print("[Migration] Running idempotent schema upgrades...")
    init_db()
    Base.metadata.create_all(bind=engine)
    print("[Migration] All database tables and columns are up to date.")

if __name__ == "__main__":
    run_migrations()

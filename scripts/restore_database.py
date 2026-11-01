#!/usr/bin/env python3
"""
Hisab AI - Database Restore Utility
Restores database from an SQLite snapshot or structured JSON backup file.
"""
import os
import sys
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import engine, get_db, DB_FILE, init_db

def restore_from_sqlite(snapshot_path: str):
    """Restore SQLite database directly from file backup."""
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"Snapshot not found at: {snapshot_path}")
    
    # Backup current DB first as safety net
    if os.path.exists(DB_FILE):
        safety_path = f"{DB_FILE}.pre_restore"
        shutil.copy2(DB_FILE, safety_path)
        print(f"[Restore] Created safety copy of existing DB at {safety_path}")
        
    shutil.copy2(snapshot_path, DB_FILE)
    print(f"[Restore] Successfully restored database from {snapshot_path}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/restore_database.py <path_to_backup_file.db>")
        sys.exit(1)
        
    backup_file = sys.argv[1]
    restore_from_sqlite(backup_file)

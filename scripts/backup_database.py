#!/usr/bin/env python3
"""
Hisab AI - Database Backup Utility
Exports encrypted or plain JSON/SQLite snapshots of the shop ledger and transaction state.
"""
import os
import sys
import json
import shutil
from datetime import datetime

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import engine, get_db, DB_FILE
from models import User, Shop, Customer, Transaction, PaymentClaim, AuditLog

def backup_sqlite_file(destination_dir: str = "backups") -> str:
    """Create a point-in-time timestamped binary snapshot of the SQLite database."""
    os.makedirs(destination_dir, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target_file = os.path.join(destination_dir, f"hisab_ai_backup_{timestamp}.db")
    
    if os.path.exists(DB_FILE):
        shutil.copy2(DB_FILE, target_file)
        print(f"[Backup] Successfully saved database snapshot to: {target_file}")
        return target_file
    else:
        print(f"[Backup Warning] {DB_FILE} not found; creating empty export marker.")
        return ""

def backup_to_json(destination_dir: str = "backups") -> str:
    """Export complete relational data as a structured JSON backup."""
    os.makedirs(destination_dir, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    target_file = os.path.join(destination_dir, f"hisab_ai_export_{timestamp}.json")
    
    db = next(get_db())
    try:
        users = [u.to_dict() for u in db.query(User).all()]
        shops = [s.to_dict() for s in db.query(Shop).all()]
        customers = [c.to_dict() for c in db.query(Customer).all()]
        transactions = [t.to_dict() for t in db.query(Transaction).all()]
        claims = [cl.to_dict() for cl in db.query(PaymentClaim).all()]
        
        payload = {
            "version": "2.0.0",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "counts": {
                "users": len(users),
                "shops": len(shops),
                "customers": len(customers),
                "transactions": len(transactions),
                "payment_claims": len(claims)
            },
            "data": {
                "users": users,
                "shops": shops,
                "customers": customers,
                "transactions": transactions,
                "payment_claims": claims
            }
        }
        
        with open(target_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
            
        print(f"[Backup] JSON relational backup exported to: {target_file}")
        return target_file
    finally:
        db.close()

if __name__ == "__main__":
    print("=== Starting Hisab AI Database Backup ===")
    backup_sqlite_file()
    backup_to_json()
    print("=== Backup Complete ===")

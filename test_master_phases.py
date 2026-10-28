"""
Automated test suite verifying Phase 1, Phase 2, Phase 3, and Phase 4 features of HisabAI.
Runs against isolated in-memory SQLite database without requiring external API keys.
"""

import hashlib
import json
import unittest
from datetime import timedelta
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app
from models import (
    AuditLog,
    PaymentReminder,
    Product,
    StockMovement,
    Transaction,
    TransactionType,
    User,
    UserSession,
    get_current_ist_time,
    hash_password,
)
import pdf_generator

from test_app import TestingSession, client, test_engine


class TestHisabAIMasterPhases(unittest.TestCase):
    """Rigorous tests covering all four master upgrade phases."""

    def setUp(self):
        Base.metadata.create_all(bind=test_engine)
        self.db = TestingSession()

        # Seed Primary Owner
        self.owner = User(
            username="guptaji",
            shop_name="Gupta General Store",
            owner_name="Ramesh Gupta",
            email="gupta@gmail.com",
            password_hash=hash_password("password123"),
            role="OWNER"
        )
        self.db.add(self.owner)
        self.db.commit()
        self.db.refresh(self.owner)
        self.owner.shop_id = self.owner.id
        self.db.commit()

        # Seed Owner Session
        self.owner_token = "test_owner_token_12345"
        sess = UserSession(
            token=self.owner_token,
            user_id=self.owner.id,
            expires_at=get_current_ist_time() + timedelta(days=30)
        )
        self.db.add(sess)

        # Seed Manager
        self.manager = User(
            username="manager_anil",
            shop_name="Gupta General Store",
            owner_name="Anil Kumar",
            role="MANAGER",
            shop_id=self.owner.id,
            password_hash=hash_password("password123")
        )
        self.db.add(self.manager)
        self.db.commit()
        self.manager_token = "test_manager_token_12345"
        sess_m = UserSession(
            token=self.manager_token,
            user_id=self.manager.id,
            expires_at=get_current_ist_time() + timedelta(days=30)
        )
        self.db.add(sess_m)

        # Seed Staff
        self.staff = User(
            username="staff_sonu",
            shop_name="Gupta General Store",
            owner_name="Sonu Chaurasia",
            role="STAFF",
            shop_id=self.owner.id,
            password_hash=hash_password("password123")
        )
        self.db.add(self.staff)
        self.db.commit()
        self.staff_token = "test_staff_token_12345"
        sess_s = UserSession(
            token=self.staff_token,
            user_id=self.staff.id,
            expires_at=get_current_ist_time() + timedelta(days=30)
        )
        self.db.add(sess_s)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(bind=test_engine)

    def _headers(self, token):
        return {"Authorization": f"Bearer {token}"}

    # ==========================================
    # Phase 1: Smart Reminders
    # ==========================================
    def test_smart_reminder_flow(self):
        # 1. Add credit transaction for customer Sharma Ji
        tx1 = Transaction(
            user_id=self.owner.id,
            customer_name="Sharma Ji",
            customer_phone="9876543210",
            amount=1500.0,
            transaction_type=TransactionType.UDHAAR
        )
        # Add partial payment
        tx2 = Transaction(
            user_id=self.owner.id,
            customer_name="Sharma Ji",
            amount=500.0,
            transaction_type=TransactionType.JAMA
        )
        self.db.add_all([tx1, tx2])
        self.db.commit()

        # 2. Check eligible reminders (balance should be 1000.0)
        res = client.get("/api/reminders/eligible", headers=self._headers(self.owner_token))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total_eligible"], 1)
        self.assertEqual(data["customers"][0]["customer_name"], "Sharma Ji")
        self.assertEqual(data["customers"][0]["outstanding_balance"], 1000.0)
        self.assertFalse(data["customers"][0]["already_reminded_today"])

        # 3. Generate reminder message with WhatsApp URL
        gen_res = client.post(
            "/api/reminders/generate",
            json={"customer_name": "Sharma Ji", "customer_phone": "9876543210", "tone": "polite"},
            headers=self._headers(self.owner_token)
        )
        self.assertEqual(gen_res.status_code, 200)
        gen_data = gen_res.json()
        self.assertIn("Rs. 1,000.00", gen_data["message_text"])
        self.assertIn("https://wa.me/919876543210", gen_data["whatsapp_url"])
        self.assertIn("sms:919876543210", gen_data["sms_url"])

        # 4. Record reminder sent
        rec_res = client.post(
            "/api/reminders/record",
            json={
                "customer_name": "Sharma Ji",
                "customer_phone": "9876543210",
                "balance_amount": 1000.0,
                "message_text": gen_data["message_text"],
                "delivery_method": "WHATSAPP",
                "delivery_status": "SENT"
            },
            headers=self._headers(self.owner_token)
        )
        self.assertEqual(rec_res.status_code, 200)
        self.assertTrue(rec_res.json()["success"])

        # 5. Check eligible again: already_reminded_today must now be True!
        res_after = client.get("/api/reminders/eligible", headers=self._headers(self.owner_token))
        self.assertTrue(res_after.json()["customers"][0]["already_reminded_today"])

        # 6. Check reminder history endpoint
        hist_res = client.get("/api/reminders/history/Sharma%20Ji", headers=self._headers(self.owner_token))
        self.assertEqual(hist_res.status_code, 200)
        self.assertEqual(len(hist_res.json()), 1)
        self.assertEqual(hist_res.json()[0]["balance_snapshot"], 1000.0)

    # ==========================================
    # Phase 1: Search, Filter, Pagination & CSV Injection Protection
    # ==========================================
    def test_search_and_csv_injection_neutralization(self):
        # Insert transactions including one with dangerous formula injection strings
        t1 = Transaction(user_id=self.owner.id, customer_name="Ramesh Kumar", amount=250.0, transaction_type=TransactionType.UDHAAR)
        t2 = Transaction(user_id=self.owner.id, customer_name="Suresh Verma", amount=1200.0, transaction_type=TransactionType.JAMA)
        # Malicious formula injection in customer name and notes: =cmd|' /C calc'!A0
        t3 = Transaction(
            user_id=self.owner.id,
            customer_name="=cmd|' /C calc'!A0",
            notes="@SUM(1+1)",
            amount=99.0,
            transaction_type=TransactionType.UDHAAR
        )
        self.db.add_all([t1, t2, t3])
        self.db.commit()

        # Search by query
        s_res = client.get("/api/transactions/search?query=Ramesh", headers=self._headers(self.owner_token))
        self.assertEqual(s_res.status_code, 200)
        self.assertEqual(s_res.json()["total_count"], 1)

        # Filter by amount range
        amt_res = client.get("/api/transactions/search?min_amount=200&max_amount=1000", headers=self._headers(self.owner_token))
        self.assertEqual(amt_res.status_code, 200)
        self.assertEqual(amt_res.json()["total_count"], 1)
        self.assertEqual(amt_res.json()["transactions"][0]["customer_name"], "Ramesh Kumar")

        # CSV Export and verify formula neutralizer (leading quote prepend)
        csv_res = client.get("/api/transactions/export", headers=self._headers(self.owner_token))
        self.assertEqual(csv_res.status_code, 200)
        self.assertIn("text/csv", csv_res.headers["content-type"])
        csv_text = csv_res.text
        # Ensure =cmd was neutralized to '=cmd and @SUM was neutralized to '@SUM
        self.assertIn("'=cmd", csv_text)
        self.assertIn("'@SUM", csv_text)

    # ==========================================
    # Phase 1: PDF Receipts & Statements
    # ==========================================
    def test_pdf_receipt_and_statement(self):
        t1 = Transaction(user_id=self.owner.id, customer_name="Verma Ji", amount=500.0, transaction_type=TransactionType.UDHAAR)
        t2 = Transaction(user_id=self.owner.id, customer_name="Verma Ji", amount=200.0, transaction_type=TransactionType.JAMA)
        self.db.add_all([t1, t2])
        self.db.commit()

        # Receipt PDF
        r_res = client.get(f"/api/receipts/{t2.id}/pdf", headers=self._headers(self.owner_token))
        self.assertEqual(r_res.status_code, 200)
        self.assertEqual(r_res.headers["content-type"], "application/pdf")
        self.assertTrue(r_res.content.startswith(b"%PDF-1.4"))
        self.assertIn(b"%%EOF", r_res.content)

        # Statement PDF
        s_res = client.get("/api/customers/Verma%20Ji/statement/pdf", headers=self._headers(self.owner_token))
        self.assertEqual(s_res.status_code, 200)
        self.assertEqual(s_res.headers["content-type"], "application/pdf")
        self.assertTrue(s_res.content.startswith(b"%PDF-1.4"))

    # ==========================================
    # Phase 2: AI & Voice Assistant Extended Intents
    # ==========================================
    def test_voice_assistant_extended_intents(self):
        # 1. Navigation Intent
        nav_res = client.post(
            "/api/voice-assistant",
            json={"text": "ledger book kholo", "session_id": "test_s1"},
            headers=self._headers(self.owner_token)
        )
        self.assertEqual(nav_res.status_code, 200)
        self.assertEqual(nav_res.json()["action_taken"], "navigate_view")
        self.assertEqual(nav_res.json()["data"]["target_view"], "ledger")

        # 2. Daily summary query
        t = Transaction(user_id=self.owner.id, customer_name="Test Cust", amount=350.0, transaction_type=TransactionType.JAMA)
        self.db.add(t)
        self.db.commit()

        ds_res = client.get("/api/analytics/daily-summary", headers=self._headers(self.owner_token))
        self.assertEqual(ds_res.status_code, 200)
        ds_data = ds_res.json()
        self.assertEqual(ds_data["total_jama"], 350.0)
        self.assertIsNotNone(ds_data["ai_summary_text"])

    # ==========================================
    # Phase 3: Inventory Management & RBAC
    # ==========================================
    def test_inventory_and_rbac(self):
        # 1. Staff cannot create products (403 Forbidden)
        staff_prod_res = client.post(
            "/api/inventory/products",
            json={"name": "Basmati Rice 1kg", "selling_price": 120.0, "initial_stock": 10.0},
            headers=self._headers(self.staff_token)
        )
        self.assertEqual(staff_prod_res.status_code, 403)

        # 2. Manager can create product
        mgr_prod_res = client.post(
            "/api/inventory/products",
            json={"name": "Basmati Rice 1kg", "cost_price": 95.0, "selling_price": 120.0, "initial_stock": 10.0, "low_stock_threshold": 3.0},
            headers=self._headers(self.manager_token)
        )
        self.assertEqual(mgr_prod_res.status_code, 200)
        prod_id = mgr_prod_res.json()["id"]
        self.assertEqual(mgr_prod_res.json()["current_stock"], 10.0)

        # 3. Record stock movement (SOLD 8 units)
        mov_res = client.post(
            "/api/inventory/movements",
            json={"product_id": prod_id, "movement_type": "SOLD", "quantity": 8.0, "notes": "Counter sale"},
            headers=self._headers(self.staff_token)
        )
        self.assertEqual(mov_res.status_code, 200)
        self.assertEqual(mov_res.json()["resulting_stock"], 2.0)

        # 4. Low stock check: stock is 2.0, threshold is 3.0 -> should appear in low-stock
        low_res = client.get("/api/inventory/low-stock", headers=self._headers(self.owner_token))
        self.assertEqual(low_res.status_code, 200)
        self.assertEqual(len(low_res.json()), 1)
        self.assertEqual(low_res.json()[0]["id"], prod_id)

    # ==========================================
    # Phase 3: Team Members & RBAC
    # ==========================================
    def test_team_member_management(self):
        # 1. Owner can list members
        members_res = client.get("/api/team/members", headers=self._headers(self.owner_token))
        self.assertEqual(members_res.status_code, 200)
        self.assertGreaterEqual(len(members_res.json()), 3)

        # 2. Staff cannot manage team (403 Forbidden)
        staff_add_res = client.post(
            "/api/team/members",
            json={"username": "new_staff", "password": "password123", "role": "STAFF"},
            headers=self._headers(self.staff_token)
        )
        self.assertEqual(staff_add_res.status_code, 403)

        # 3. Owner creates new staff
        add_res = client.post(
            "/api/team/members",
            json={"username": "new_helper", "password": "password123", "owner_name": "Helper Ram", "role": "STAFF"},
            headers=self._headers(self.owner_token)
        )
        self.assertEqual(add_res.status_code, 200)
        new_id = add_res.json()["id"]

        # 4. Owner updates role to MANAGER
        role_res = client.put(
            f"/api/team/members/{new_id}/role",
            json={"role": "MANAGER"},
            headers=self._headers(self.owner_token)
        )
        self.assertEqual(role_res.status_code, 200)

        # 5. Owner removes member
        del_res = client.delete(f"/api/team/members/{new_id}", headers=self._headers(self.owner_token))
        self.assertEqual(del_res.status_code, 200)

    # ==========================================
    # Phase 4: Backup Export, SHA-256 Checksum & Restore
    # ==========================================
    def test_backup_and_restore(self):
        # Create some data
        tx = Transaction(user_id=self.owner.id, customer_name="Test Customer", amount=300.0, transaction_type=TransactionType.UDHAAR)
        p = Product(shop_id=self.owner.id, name="Sugar 1kg", current_stock=25.0)
        self.db.add_all([tx, p])
        self.db.commit()

        # Export backup
        export_res = client.get("/api/backup/export", headers=self._headers(self.owner_token))
        self.assertEqual(export_res.status_code, 200)
        backup_json = export_res.json()
        self.assertIn("checksum", backup_json)
        self.assertIn("metadata", backup_json)
        self.assertIn("data", backup_json)

        # Verify checksum matches
        canonical = json.dumps(backup_json["data"], sort_keys=True)
        expected_checksum = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.assertEqual(backup_json["checksum"], expected_checksum)

        # Tampered checksum should be rejected with 400
        tampered_backup = dict(backup_json)
        tampered_backup["checksum"] = "bad_checksum_hash"
        tamper_res = client.post("/api/backup/restore", json=tampered_backup, headers=self._headers(self.owner_token))
        self.assertEqual(tamper_res.status_code, 400)

        # Valid restore
        restore_res = client.post("/api/backup/restore", json=backup_json, headers=self._headers(self.owner_token))
        self.assertEqual(restore_res.status_code, 200)
        self.assertTrue(restore_res.json()["success"])

    # ==========================================
    # Phase 4: Audit Logs & Analytics Overview
    # ==========================================
    def test_audit_logs_and_analytics_overview(self):
        # Check audit logs (manager/owner only)
        log_res = client.get("/api/audit-logs", headers=self._headers(self.owner_token))
        self.assertEqual(log_res.status_code, 200)
        self.assertIsInstance(log_res.json(), list)

        # Analytics overview
        an_res = client.get("/api/analytics/overview?days=30", headers=self._headers(self.owner_token))
        self.assertEqual(an_res.status_code, 200)
        an_data = an_res.json()
        self.assertIn("customer_distribution", an_data)
        self.assertIn("top_debtors", an_data)
        self.assertIn("daily_trend", an_data)
        self.assertEqual(len(an_data["customer_distribution"]), 5)

"""
Comprehensive verification test suite for HisabAI audit fixes and master requirements:
1. ₹10 problem root cause verification, integer paise precision, and rapid duplicate suppression
2. Today's transactions IST date filtering
3. Working sync and data endpoints
4. Brevo SMTP customer email reminders with preview, template generation, delivery log, and audit trail
5. Overdue interest engine (15-day grace period, 2% per 15-day period, FIFO allocation, payment recalculation)
6. Layer A (deterministic) and Layer B (AI) daily business summary
7. Team member creation with unique username/email validation and RBAC
8. System reliability stats and instant SQLite physical database snapshots
9. Editable shop profile and settings persistence
"""

from datetime import datetime, timedelta
import json
import os
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import database
from database import Base, get_db
from email_service import generate_reminder_email_content, is_valid_email, send_brevo_email
from interest_service import calculate_customer_interest
import main
from main import app, get_effective_shop_id
import models
from models import (
    AuditLog,
    KOLKATA_TZ,
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
from test_app import TestingSession, client, test_engine


class TestAuditFixesAndRequirements:
    """Master audit test suite verifying all 20 required criteria."""

    @classmethod
    def setup_class(cls):
        Base.metadata.create_all(bind=test_engine)

    def setup_method(self):
        """Set up fresh state for each test using isolated database session."""
        self.db = TestingSession()
        self.db.query(AuditLog).delete()
        self.db.query(PaymentReminder).delete()
        self.db.query(StockMovement).delete()
        self.db.query(Product).delete()
        self.db.query(Transaction).delete()
        self.db.query(UserSession).delete()
        self.db.query(User).delete()
        self.db.commit()

        # Seed Primary Shop Owner
        self.owner = User(
            username="test_owner",
            shop_name="Kirana Superstore",
            owner_name="Mohit Test",
            email="owner@testkirana.com",
            phone="9876543210",
            address="Station Road, Jaipur",
            currency="INR",
            timezone="Asia/Kolkata",
            role="OWNER",
            password_hash=hash_password("ownerpass123"),
            interest_enabled=1,
            interest_rate_percent=2.0,
            interest_grace_days=15
        )
        self.db.add(self.owner)
        self.db.commit()
        self.db.refresh(self.owner)
        self.owner.shop_id = self.owner.id
        self.db.commit()

        # Create Session Token
        self.owner_token = "test_owner_token_abc123"
        sess = UserSession(
            token=self.owner_token,
            user_id=self.owner.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        )
        self.db.add(sess)
        self.db.commit()

        self.auth_headers = {"Authorization": f"Bearer {self.owner_token}"}
        self.auth_cookies = {"hisab_session": self.owner_token}

    def teardown_method(self):
        self.db.close()

    # -------------------------------------------------------------
    # 1. Root Cause & ₹10 Duplicate Suppression Verification
    # -------------------------------------------------------------
    def test_safe_integer_paise_and_duplicate_suppression(self):
        """Verifies integer paise precision and duplicate submit suppression within 4 seconds."""
        payload = {
            "customer_name": "Ramesh Gupta",
            "customer_email": "ramesh.gupta@example.com",
            "amount": 10.00,
            "transaction_type": "UDHAAR",
            "raw_text": "Chai patti udhaar"
        }

        # First request succeeds
        resp1 = client.post("/api/transactions", json=payload, headers=self.auth_headers)
        assert resp1.status_code == 201
        data1 = resp1.json()
        assert data1["amount"] == 10.0
        assert data1["customer_name"] == "Ramesh Gupta"
        tx_id1 = data1["id"]

        # Immediate rapid second submit (double click) within 4 seconds
        resp2 = client.post("/api/transactions", json=payload, headers=self.auth_headers)
        assert resp2.status_code in [200, 201]
        data2 = resp2.json()
        # Must return the existing transaction ID and not create a duplicate row!
        assert data2["id"] == tx_id1

        # Check database row count
        count = self.db.query(Transaction).filter(Transaction.customer_name == "Ramesh Gupta").count()
        assert count == 1, "Duplicate row was incorrectly created on double submit!"

        # Verify paise precision in DB
        tx_db = self.db.query(Transaction).filter(Transaction.id == tx_id1).first()
        assert tx_db.amount_paise == 1000

    # -------------------------------------------------------------
    # 2. Today's Transactions & IST Filtering
    # -------------------------------------------------------------
    def test_todays_transactions_ist_filtering(self):
        """Verifies transactions created today in IST appear in /api/summary."""
        now_ist = datetime.now(KOLKATA_TZ)
        today_str = now_ist.strftime("%Y-%m-%d")

        # Today's transaction
        tx_today = Transaction(
            user_id=self.owner.id,
            customer_name="Suresh Kumar",
            amount=500.0,
            amount_paise=50000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now_ist
        )
        # Yesterday's transaction
        tx_yesterday = Transaction(
            user_id=self.owner.id,
            customer_name="Mahesh Verma",
            amount=300.0,
            amount_paise=30000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now_ist - timedelta(days=1)
        )
        self.db.add_all([tx_today, tx_yesterday])
        self.db.commit()

        resp = client.get("/api/summary", headers=self.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == today_str
        assert data["transaction_count"] == 1
        assert data["total_udhaar_today"] == 500.0
        assert data["transactions"][0]["customer_name"] == "Suresh Kumar"

    # -------------------------------------------------------------
    # 3. Working Sync & Refresh Endpoints
    # -------------------------------------------------------------
    def test_sync_data_endpoints_operational(self):
        """Verifies summary, customer directory, and daily analytics endpoints are responsive."""
        r1 = client.get("/api/summary", headers=self.auth_headers)
        assert r1.status_code == 200

        r2 = client.get("/api/customers", headers=self.auth_headers)
        assert r2.status_code == 200

        r3 = client.get("/api/analytics/daily-summary?generate_ai=false", headers=self.auth_headers)
        assert r3.status_code == 200
        assert "total_udhaar" in r3.json()

    # -------------------------------------------------------------
    # 4. Overdue Interest Engine (15-Day Grace Period Formula)
    # -------------------------------------------------------------
    def test_overdue_interest_exact_formula(self):
        """
        Verifies exact overdue interest formula:
        - D <= 15: N = 0, Interest = 0
        - D = 16: N = 1, Interest = P * 0.02 * 1
        - D = 30: N = 1, Interest = P * 0.02 * 1
        - D = 31: N = 2, Interest = P * 0.02 * 2
        """
        now = datetime.now(KOLKATA_TZ)
        principal = 1000.0

        for days, expected_periods, expected_interest in [
            (5, 0, 0.0),
            (15, 0, 0.0),
            (16, 1, 20.0),
            (30, 1, 20.0),
            (31, 2, 40.0),
            (45, 2, 40.0),
            (46, 3, 60.0),
        ]:
            tx = Transaction(
                customer_name="Test Customer",
                amount=principal,
                amount_paise=int(principal * 100),
                transaction_type=TransactionType.UDHAAR,
                timestamp=now - timedelta(days=days)
            )
            res = calculate_customer_interest([tx], current_time=now, rate_percent=2.0, grace_days=15)
            assert res["overdue_periods"] == expected_periods, f"Failed periods for days={days}"
            assert res["accrued_interest"] == expected_interest, f"Failed interest for days={days}"
            assert res["total_balance"] == round(principal + expected_interest, 2)

    def test_overdue_interest_dynamic_recalculation_on_payment(self):
        """Verifies overdue interest recalculates dynamically upon partial and full payment."""
        now = datetime.now(KOLKATA_TZ)

        # 31 days ago: ₹2000 UDHAAR (N=2 periods -> 4% = ₹80 interest)
        tx_u = Transaction(
            customer_name="Deepak",
            amount=2000.0,
            amount_paise=200000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now - timedelta(days=31)
        )
        res_before = calculate_customer_interest([tx_u], current_time=now)
        assert res_before["principal_balance"] == 2000.0
        assert res_before["accrued_interest"] == 80.0
        assert res_before["total_balance"] == 2080.0

        # Today: Partial payment of ₹1000 JAMA
        tx_j1 = Transaction(
            customer_name="Deepak",
            amount=1000.0,
            amount_paise=100000,
            transaction_type=TransactionType.JAMA,
            timestamp=now
        )
        res_partial = calculate_customer_interest([tx_u, tx_j1], current_time=now)
        assert res_partial["principal_balance"] == 1000.0
        assert res_partial["accrued_interest"] == 40.0  # 1000 * 4%
        assert res_partial["total_balance"] == 1040.0

        # Today: Full settlement of remaining ₹1000 JAMA
        tx_j2 = Transaction(
            customer_name="Deepak",
            amount=1000.0,
            amount_paise=100000,
            transaction_type=TransactionType.JAMA,
            timestamp=now
        )
        res_full = calculate_customer_interest([tx_u, tx_j1, tx_j2], current_time=now)
        assert res_full["principal_balance"] == 0.0
        assert res_full["accrued_interest"] == 0.0
        assert res_full["total_balance"] == 0.0

    def test_interest_overview_endpoint(self):
        """Verifies GET /api/interest/overview returns shop-wide metrics and breakdowns."""
        now = datetime.now(KOLKATA_TZ)
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Karan",
            amount=1500.0,
            amount_paise=150000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now - timedelta(days=32)
        )
        self.db.add(tx)
        self.db.commit()

        resp = client.get("/api/interest/overview", headers=self.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["interest_enabled"] is True
        assert data["total_principal_outstanding"] == 1500.0
        assert data["total_interest_accrued"] == 60.0  # 1500 * 4% (N=2)
        assert data["overdue_customers_count"] == 1
        assert len(data["breakdowns"]) == 1
        assert data["breakdowns"][0]["customer_name"] == "Karan"

    # -------------------------------------------------------------
    # 5. Brevo SMTP Email Reminders
    # -------------------------------------------------------------
    def test_email_validation_and_template_generation(self):
        """Verifies email regex and template content generation with Hindi and English."""
        assert is_valid_email("customer@example.com") is True
        assert is_valid_email("invalid-email") is False
        assert is_valid_email("") is False
        assert is_valid_email(None) is False

        content = generate_reminder_email_content(
            customer_name="Sunil Sharma",
            principal_balance=1200.0,
            accrued_interest=48.0,
            total_balance=1248.0,
            overdue_days=32,
            shop_name="Kirana Superstore",
            owner_name="Mohit Test",
            shop_phone="9876543210"
        )
        assert "Sunil Sharma" in content["html"]
        assert "1,248.00" in content["html"] or "1248.00" in content["html"]
        assert "Kirana Superstore" in content["subject"]
        assert "शीघ्रातिशीघ्र" in content["html"]

    def test_email_reminder_preview_endpoint(self):
        """Verifies POST /api/reminders/email/preview returns subject and HTML content."""
        now = datetime.now(KOLKATA_TZ)
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Sunil Sharma",
            amount=1000.0,
            amount_paise=100000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now - timedelta(days=20)
        )
        self.db.add(tx)
        self.db.commit()

        resp = client.post(
            "/api/reminders/email/preview",
            json={"customer_name": "Sunil Sharma", "customer_email": "sunil@gmail.com"},
            headers=self.auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["customer_name"] == "Sunil Sharma"
        assert data["principal_balance"] == 1000.0
        assert data["overdue_days"] == 20
        assert "Kirana Superstore" in data["subject"]
        assert "<html>" in data["html_content"].lower() or "<!doctype html>" in data["html_content"].lower()

    @patch("main.send_brevo_email")
    def test_email_reminder_send_and_audit_logging(self, mock_send):
        """Verifies POST /api/reminders/email/send dispatches via Brevo SMTP and logs audit event."""
        mock_send.return_value = (True, "Delivered via Brevo SMTP mock")

        now = datetime.now(KOLKATA_TZ)
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Sunil Sharma",
            amount=1000.0,
            amount_paise=100000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now - timedelta(days=20)
        )
        self.db.add(tx)
        self.db.commit()

        payload = {
            "customer_name": "Sunil Sharma",
            "customer_email": "sunil@gmail.com",
            "custom_message": "Please settle before Saturday"
        }
        resp = client.post("/api/reminders/email/send", json=payload, headers=self.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ACCEPTED"
        assert data["recipient"] == "sunil@gmail.com"

        # Check PaymentReminder record created
        rem = self.db.query(PaymentReminder).filter(PaymentReminder.customer_name == "Sunil Sharma").first()
        assert rem is not None
        assert rem.delivery_method == "EMAIL"
        assert rem.delivery_status == "SENT"
        assert rem.customer_email == "sunil@gmail.com"

        # Check Audit Log recorded
        log = self.db.query(AuditLog).filter(AuditLog.action == "EMAIL_REMINDER_SENT").first()
        assert log is not None
        assert "sunil@gmail.com" in log.details_json

    # -------------------------------------------------------------
    # 6. AI Daily Business Summary & Vyapar Salah
    # -------------------------------------------------------------
    def test_daily_business_summary_layer_a_and_b(self):
        """Verifies Layer A deterministic metrics and fallback Layer B narrative."""
        now = datetime.now(KOLKATA_TZ)
        today_str = now.strftime("%Y-%m-%d")

        t1 = Transaction(
            user_id=self.owner.id,
            customer_name="Akash",
            amount=800.0,
            amount_paise=80000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now
        )
        t2 = Transaction(
            user_id=self.owner.id,
            customer_name="Akash",
            amount=300.0,
            amount_paise=30000,
            transaction_type=TransactionType.JAMA,
            timestamp=now
        )
        self.db.add_all([t1, t2])
        self.db.commit()

        resp = client.get("/api/analytics/daily-summary?generate_ai=true", headers=self.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["date"] == today_str
        assert data["total_udhaar"] == 800.0
        assert data["total_jama"] == 300.0
        assert data["net_movement"] == 500.0
        assert data["transaction_count"] == 2
        assert data["top_customer_today"] == "Akash"
        assert data["ai_summary_text"] is not None
        assert len(data["ai_summary_text"]) > 10

    # -------------------------------------------------------------
    # 7. Add Team Member Button & RBAC Validation
    # -------------------------------------------------------------
    def test_add_team_member_with_email_and_unique_validation(self):
        """Verifies team member creation with email, unique username, and unique email enforcement."""
        payload = {
            "username": "staff_rahul",
            "owner_name": "Rahul Sharma",
            "email": "rahul@testkirana.com",
            "password": "staffpassword123",
            "role": "STAFF"
        }
        resp = client.post("/api/team/members", json=payload, headers=self.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "staff_rahul"
        assert data["email"] == "rahul@testkirana.com"
        assert data["role"] == "STAFF"

        # Attempt duplicate username
        resp_dup = client.post("/api/team/members", json=payload, headers=self.auth_headers)
        assert resp_dup.status_code == 400
        assert "Username is already taken" in resp_dup.json()["detail"]

        # Attempt duplicate email with different username
        payload2 = {
            "username": "staff_rahul2",
            "email": "rahul@testkirana.com",
            "password": "staffpassword123",
            "role": "STAFF"
        }
        resp_dup_email = client.post("/api/team/members", json=payload2, headers=self.auth_headers)
        assert resp_dup_email.status_code == 400
        assert "Email 'rahul@testkirana.com' is already registered" in resp_dup_email.json()["detail"]

    # -------------------------------------------------------------
    # 8. Reliability, Snapshots & Audit Logs
    # -------------------------------------------------------------
    def test_system_reliability_and_snapshot_creation(self):
        """Verifies GET /api/system/reliability and POST /api/backup/create-snapshot."""
        r1 = client.get("/api/system/reliability", headers=self.auth_headers)
        assert r1.status_code == 200
        data1 = r1.json()
        assert "integrity_check" in data1
        assert "journal_mode" in data1
        assert "reliability_explanation" in data1

        # Create physical snapshot
        r2 = client.post("/api/backup/create-snapshot", headers=self.auth_headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["success"] is True
        assert "hisab_ai_snapshot_" in data2["filename"]
        assert data2["size_kb"] > 0

        # Verify snapshot appears in snapshot history
        r3 = client.get("/api/system/reliability", headers=self.auth_headers)
        assert r3.status_code == 200
        assert r3.json()["snapshots_count"] >= 1

    # -------------------------------------------------------------
    # 9. Editable Shop Profile Settings
    # -------------------------------------------------------------
    def test_editable_shop_profile_persistence(self):
        """Verifies GET /api/shop/profile and PUT /api/shop/profile updates."""
        r1 = client.get("/api/shop/profile", headers=self.auth_headers)
        assert r1.status_code == 200
        data1 = r1.json()
        assert data1["shop_name"] == "Kirana Superstore"

        # Update profile
        update_payload = {
            "shop_name": "Goyal & Sons Supermart",
            "owner_name": "Mohit Goyal",
            "phone": "9998887776",
            "email": "mohitgoyal@example.com",
            "address": "Civil Lines, New Delhi",
            "currency": "INR",
            "timezone": "Asia/Kolkata",
            "interest_enabled": True,
            "interest_rate_percent": 2.5,
            "interest_grace_days": 14
        }
        r2 = client.put("/api/shop/profile", json=update_payload, headers=self.auth_headers)
        assert r2.status_code == 200
        data2 = r2.json()
        assert data2["shop_name"] == "Goyal & Sons Supermart"
        assert data2["owner_name"] == "Mohit Goyal"
        assert data2["phone"] == "9998887776"
        assert data2["interest_rate_percent"] == 2.5
        assert data2["interest_grace_days"] == 14

        # Confirm persisted in DB
        owner_db = self.db.query(User).filter(User.id == self.owner.id).first()
        self.db.refresh(owner_db)
        assert owner_db.shop_name == "Goyal & Sons Supermart"
        assert owner_db.phone == "9998887776"
        assert owner_db.interest_rate_percent == 2.5

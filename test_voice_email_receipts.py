"""
Comprehensive verification test suite for HisabAI Master Implementation:
1. Team Member Modal & RBAC Account Creation & Persistence
2. Inventory Add Product Modal & Catalog Persistence with SKU Uniqueness
3. Mandatory Customer Email Enforcement on Transactions
4. Automatic Email Receipts Outbox & Retry Mechanism
5. Optimized Fast Hinglish Voice Assistant & Spoken Email Collection
6. Financial Integrity, Integer Paise Precision & Idempotency
"""

import time
from datetime import timedelta
from unittest.mock import patch
import pytest

import models
from models import (
    Base,
    Product,
    Transaction,
    TransactionReceipt,
    User,
    UserSession,
    hash_password,
    get_current_ist_time,
)
import database
import ai_service
import email_service
from main import app, PENDING_VOICE_OPERATIONS
from test_app import TestingSession, client, test_engine


class TestVoiceEmailReceiptsAndMasterFixes:
    """Master test suite for Voice AI, Mandatory Email, Receipts, Team & Inventory."""

    @classmethod
    def setup_class(cls):
        Base.metadata.create_all(bind=test_engine)
        database.init_db(test_engine)

    def setup_method(self):
        """Clean tables and initialize test owner."""
        self.db = TestingSession()
        self.db.query(TransactionReceipt).delete()
        self.db.query(Transaction).delete()
        self.db.query(Product).delete()
        self.db.query(UserSession).delete()
        self.db.query(User).delete()
        self.db.commit()
        PENDING_VOICE_OPERATIONS.clear()

        # Seed Owner
        self.owner = User(
            username="master_owner",
            shop_name="Raj Kirana Store",
            owner_name="Raj Sharma",
            email="raj@rajkirana.com",
            role="OWNER",
            password_hash=hash_password("OwnerPassword123!")
        )
        self.db.add(self.owner)
        self.db.commit()
        self.db.refresh(self.owner)
        self.owner.shop_id = self.owner.id
        self.db.commit()

        # Owner Auth Token
        self.owner_token = "token_master_owner_123"
        session = UserSession(
            token=self.owner_token,
            user_id=self.owner.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        )
        self.db.add(session)
        self.db.commit()
        self.auth_headers = {"Authorization": f"Bearer {self.owner_token}"}

    def teardown_method(self):
        self.db.close()
        PENDING_VOICE_OPERATIONS.clear()

    # ============================================================================
    # 1. Team & RBAC Add Member Workflow
    # ============================================================================

    def test_add_team_member_modal_workflow(self):
        """Verifies adding a team member persists in DB and duplicate usernames fail."""
        # 1. Owner creates a Manager account
        member_payload = {
            "username": "manager_amit",
            "owner_name": "Amit Verma",
            "email": "amit.manager@testshop.com",
            "password": "ManagerPass123!",
            "role": "MANAGER"
        }
        resp = client.post("/api/team/members", json=member_payload, headers=self.auth_headers)
        assert resp.status_code == 200, resp.text
        member_data = resp.json()
        assert member_data["username"] == "manager_amit"
        assert member_data["role"] == "MANAGER"

        # 2. Verify account persisted in database
        saved = self.db.query(User).filter(User.username == "manager_amit").first()
        assert saved is not None
        assert saved.role == "MANAGER"
        assert saved.shop_id == self.owner.id

        # 3. Duplicate username should be rejected with 400
        resp_dup = client.post("/api/team/members", json=member_payload, headers=self.auth_headers)
        assert resp_dup.status_code == 400
        assert "already taken" in resp_dup.json()["detail"].lower() or "already exists" in resp_dup.json()["detail"].lower()

    def test_add_team_member_rbac_permission_gating(self):
        """Verifies staff member cannot add other team members."""
        # Create a STAFF member
        staff = User(
            username="staff_sonu",
            owner_name="Sonu Kumar",
            shop_id=self.owner.id,
            role="STAFF",
            password_hash=hash_password("StaffPass123!")
        )
        self.db.add(staff)
        self.db.commit()
        self.db.refresh(staff)

        staff_token = "staff_token_xyz"
        self.db.add(UserSession(
            token=staff_token,
            user_id=staff.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        ))
        self.db.commit()
        staff_headers = {"Authorization": f"Bearer {staff_token}"}

        # Staff attempts to add another team member -> Forbidden (403)
        resp = client.post("/api/team/members", json={
            "username": "unauthorized_user",
            "password": "Pass123456!",
            "role": "STAFF"
        }, headers=staff_headers)
        assert resp.status_code == 403

    # ============================================================================
    # 2. Inventory Add Product Workflow
    # ============================================================================

    def test_add_product_modal_workflow(self):
        """Verifies product creation, persistence, SKU uniqueness, and validation."""
        # 1. Add valid product
        prod_payload = {
            "name": "Tata Salt 1kg",
            "sku": "TATA-SALT-1KG",
            "category": "Groceries",
            "cost_price": 22.50,
            "selling_price": 28.00,
            "initial_stock": 50.0,
            "low_stock_threshold": 10.0
        }
        resp = client.post("/api/inventory/products", json=prod_payload, headers=self.auth_headers)
        assert resp.status_code == 200, resp.text
        prod_data = resp.json()
        assert prod_data["name"] == "Tata Salt 1kg"
        assert prod_data["sku"] == "TATA-SALT-1KG"

        # 2. Verify product persisted in database with correct paise
        prod = self.db.query(Product).filter(Product.sku == "TATA-SALT-1KG").first()
        assert prod is not None
        assert prod.cost_price_paise == 2250
        assert prod.selling_price_paise == 2800
        assert prod.current_stock == 50.0

        # 3. Duplicate SKU in the same shop must be rejected
        resp_dup = client.post("/api/inventory/products", json=prod_payload, headers=self.auth_headers)
        assert resp_dup.status_code == 400
        assert "already exists" in resp_dup.json()["detail"].lower()

        # 4. Empty product name must be rejected
        resp_empty = client.post("/api/inventory/products", json={
            "name": "   ",
            "cost_price": 10,
            "selling_price": 15
        }, headers=self.auth_headers)
        assert resp_empty.status_code == 400

    # ============================================================================
    # 3. Mandatory Customer Email Enforcement
    # ============================================================================

    def test_transaction_rejected_without_customer_email(self):
        """Verifies transaction is strictly rejected when customer email is absent."""
        payload = {
            "customer_name": "New Customer Verma",
            "amount": 250.00,
            "transaction_type": "UDHAAR"
        }
        resp = client.post("/api/transactions", json=payload, headers=self.auth_headers)
        assert resp.status_code == 400
        assert "customer email is mandatory" in resp.json()["detail"].lower()

        # Verify no transaction was written to DB
        count = self.db.query(Transaction).filter(Transaction.customer_name == "New Customer Verma").count()
        assert count == 0

    def test_transaction_success_with_customer_email_and_auto_resolution(self):
        """Verifies transaction commits with email, and subsequent entries auto-resolve email."""
        # 1. First transaction with valid customer email succeeds
        payload1 = {
            "customer_name": "Mohan Lal",
            "customer_email": "mohan.lal@example.com",
            "amount": 400.00,
            "transaction_type": "UDHAAR"
        }
        resp1 = client.post("/api/transactions", json=payload1, headers=self.auth_headers)
        assert resp1.status_code == 201
        data1 = resp1.json()
        assert data1["customer_email"] == "mohan.lal@example.com"

        # 2. Subsequent transaction for Mohan Lal without passing email in payload
        # Must automatically resolve and reuse Mohan Lal's registered email!
        payload2 = {
            "customer_name": "Mohan Lal",
            "amount": 150.00,
            "transaction_type": "JAMA"
        }
        resp2 = client.post("/api/transactions", json=payload2, headers=self.auth_headers)
        assert resp2.status_code == 201
        data2 = resp2.json()
        assert data2["customer_email"] == "mohan.lal@example.com"

        # Verify both transactions persisted with correct email
        txs = self.db.query(Transaction).filter(Transaction.customer_name == "Mohan Lal").order_by(Transaction.id.asc()).all()
        assert len(txs) == 2
        assert txs[0].customer_email == "mohan.lal@example.com"
        assert txs[1].customer_email == "mohan.lal@example.com"

    def test_transaction_rejects_malformed_email(self):
        """Verifies invalid email formats are rejected."""
        payload = {
            "customer_name": "Suresh Raina",
            "customer_email": "not-a-valid-email-format",
            "amount": 300.00,
            "transaction_type": "UDHAAR"
        }
        resp = client.post("/api/transactions", json=payload, headers=self.auth_headers)
        assert resp.status_code in (400, 422)

    # ============================================================================
    # 4. Automatic Email Receipts & Retry Mechanism
    # ============================================================================

    def test_automatic_receipt_creation_and_retry(self):
        """Verifies receipt job creation upon commit and safe retry mechanism."""
        with patch("email_service.send_brevo_email", return_value=(True, "Delivered")):
            payload = {
                "customer_name": "Devendra Kumar",
                "customer_email": "devendra@test.in",
                "amount": 750.00,
                "transaction_type": "UDHAAR"
            }
            resp = client.post("/api/transactions", json=payload, headers=self.auth_headers)
            assert resp.status_code == 201
            tx_id = resp.json()["id"]

            # Check receipt status endpoint
            receipt_resp = client.get(f"/api/transactions/{tx_id}/receipt", headers=self.auth_headers)
            assert receipt_resp.status_code == 200
            rdata = receipt_resp.json()
            assert rdata["transaction_id"] == tx_id
            assert rdata["customer_email"] == "devendra@test.in"
            assert rdata["status"] in ("PENDING", "SENT")

        # Test Retry Endpoint without creating duplicate transaction
        with patch("email_service.send_brevo_email", return_value=(True, "Delivered via retry")):
            retry_resp = client.post(f"/api/transactions/{tx_id}/receipt/retry", headers=self.auth_headers)
            assert retry_resp.status_code == 200
            retry_data = retry_resp.json()
            assert retry_data["transaction_id"] == tx_id

        # Verify database still has exactly 1 financial transaction record
        tx_count = self.db.query(Transaction).filter(Transaction.customer_name == "Devendra Kumar").count()
        assert tx_count == 1, "Receipt retry must NEVER create duplicate financial transactions!"

    # ============================================================================
    # 5. Fast Hinglish Voice Assistant & Spoken Email Collection
    # ============================================================================

    def test_voice_hinglish_fast_matching_benchmarks(self):
        """Verifies that common Hinglish expressions resolve in < 5ms without external API."""
        phrases = [
            "Sharma ji ka 200 rupaye udhaar likh do",
            "Ramesh ne 500 rupaye jama karaye",
            "Gupta ji ke naam par 1200 ka udhaar add karo",
            "Aaj ka total hisaab batao",
            "Sharma ji ka balance batao",
            "sharma at gmail dot com"
        ]

        for p in phrases:
            t0 = time.perf_counter()
            res = ai_service.classify_voice_command(p)
            elapsed_ms = (time.perf_counter() - t0) * 1000
            assert elapsed_ms < 5.0, f"Phrase '{p}' took {elapsed_ms:.2f}ms, expected < 5ms"
            assert res.intent != models.VoiceIntentType.UNKNOWN

    def test_voice_missing_email_prompts_for_email_and_confirms(self):
        """
        Simulates complete conversational workflow when customer has no email:
        Step 1: Speak entry -> Assistant detects missing email and prompts:
                'Sharma ji ki email ID registered nahi hai. Kripya pehle email ID bataiye.'
        Step 2: Speak email -> Assistant validates email, updates pending op, and prompts confirmation:
                'Sharma ji ka ₹200.00 udhaar, email sharma@gmail.com. Kya main entry save kar doon?'
        Step 3: Confirm with 'haan' -> Transaction committed with email and receipt dispatched!
        """
        # Step 1: Speak entry without registered email
        req1 = {"text": "Sharma ji ka 200 rupaye udhaar likh do", "session_id": "test_voice_s1"}
        resp1 = client.post("/api/voice-assistant", json=req1, headers=self.auth_headers)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["action_taken"] == "waiting_for_customer_email"
        assert not data1["requires_confirmation"]
        assert "email id registered nahi hai" in data1["reply_message"].lower()
        op_id = data1["pending_operation"]["operation_id"]
        assert data1["pending_operation"]["status"] == "waiting_for_email"

        # Step 2: Speak email address
        req2 = {"text": "sharma@gmail.com", "pending_operation_id": op_id, "session_id": "test_voice_s1"}
        resp2 = client.post("/api/voice-assistant", json=req2, headers=self.auth_headers)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["requires_confirmation"] is True
        assert "sharma@gmail.com" in data2["reply_message"]
        assert data2["pending_operation"]["status"] == "pending"
        assert data2["pending_operation"]["customer_email"] == "sharma@gmail.com"

        # Step 3: Confirm transaction with 'haan'
        with patch("email_service.send_brevo_email", return_value=(True, "Delivered")):
            req3 = {"text": "haan", "pending_operation_id": op_id, "session_id": "test_voice_s1"}
            resp3 = client.post("/api/voice-assistant", json=req3, headers=self.auth_headers)
            assert resp3.status_code == 200
            data3 = resp3.json()
            assert data3["action_taken"] == "committed_transaction"
            assert not data3["requires_confirmation"]

        # Verify transaction in database has customer email
        tx = self.db.query(Transaction).filter(Transaction.customer_name == "Sharma Ji").first()
        assert tx is not None
        assert tx.amount_paise == 20000
        assert tx.customer_email == "sharma@gmail.com"

        # Verify receipt job created
        rcpt = self.db.query(TransactionReceipt).filter(TransactionReceipt.transaction_id == tx.id).first()
        assert rcpt is not None
        assert rcpt.customer_email == "sharma@gmail.com"

    def test_voice_spoken_email_in_single_entry_command(self):
        """
        Verifies that if customer email is spoken directly in the entry command,
        it immediately captures the email and asks for confirmation in a single turn.
        """
        req = {"text": "Sharma ji ka 200 rupaye udhaar likh do email sharma@gmail.com", "session_id": "single_turn"}
        resp = client.post("/api/voice-assistant", json=req, headers=self.auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["requires_confirmation"] is True
        assert data["action_taken"] == "prepared_entry"
        assert "sharma@gmail.com" in data["reply_message"]
        assert data["pending_operation"]["customer_email"] == "sharma@gmail.com"

    # ============================================================================
    # 6. Idempotency & Financial Double-Click Protection
    # ============================================================================

    def test_financial_double_click_suppression(self):
        """Verifies duplicate submit within 4s returns existing transaction."""
        payload = {
            "customer_name": "Ramesh DoubleClick",
            "customer_email": "ramesh.dc@example.com",
            "amount": 500.00,
            "transaction_type": "UDHAAR"
        }

        # First request commits transaction
        res1 = client.post("/api/transactions", json=payload, headers=self.auth_headers)
        assert res1.status_code == 201
        tx_id1 = res1.json()["id"]

        # Immediate second click (within 4s) returns the same transaction ID without creating duplicate row
        res2 = client.post("/api/transactions", json=payload, headers=self.auth_headers)
        assert res2.status_code in (200, 201)
        assert res2.json()["id"] == tx_id1

        count = self.db.query(Transaction).filter(Transaction.customer_name == "Ramesh DoubleClick").count()
        assert count == 1, "Duplicate submit within 4s must not create a duplicate row!"

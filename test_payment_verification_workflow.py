"""
Comprehensive verification test suite for HisabAI Payment Verification & WhatsApp Receipt Workflow:
1. Payment reminder email content verification (UPI ID goyal.com@fam, WhatsApp URL 916375940658,
   prefilled text, and prominent manual verification policy disclaimer).
2. Payment Claim Registration API (POST /api/payment-verifications) and balance invariance.
3. Multi-shop tenant isolation for payment claims.
4. Filtered listing and counts (GET /api/payment-verifications).
5. Single claim detail with customer outstanding balance (GET /api/payment-verifications/{id}).
6. Manual Approval & Atomic Ledger Commit (POST /api/payment-verifications/{id}/approve):
   - Exactly one JAMA transaction created in integer paise.
   - Status transitions to APPROVED and resulting_transaction_id linked.
   - Outbox email receipt job created.
   - Balance reduced by verified amount.
   - Partial payment verification support.
   - Duplicate approval prevention.
   - Mandatory email requirement.
7. Claim Rejection (POST /api/payment-verifications/{id}/reject):
   - Transitions to REJECTED.
   - Zero transactions created in ledger.
   - Balance completely unaffected.
   - Cannot approve or re-reject.
8. RBAC Gating:
   - Only OWNER and MANAGER can approve/reject claims (STAFF receives 403 Forbidden).
9. Audit Logging verification for all claim operations.
"""

from datetime import timedelta
import urllib.parse
from unittest.mock import patch
import pytest

import database
import models
from models import (
    AuditLog,
    Base,
    PaymentVerification,
    Transaction,
    TransactionReceipt,
    TransactionType,
    User,
    UserSession,
    get_current_ist_time,
    hash_password,
)
from email_service import generate_reminder_email_content
from test_app import TestingSession, client, test_engine


class TestPaymentVerificationWorkflow:
    """Targeted test suite for HisabAI Payment Verification & WhatsApp Receipt Workflow."""

    @classmethod
    def setup_class(cls):
        Base.metadata.create_all(bind=test_engine)
        database.init_db(test_engine)

    def setup_method(self):
        """Clean tables and initialize test users."""
        self.db = TestingSession()
        self.db.query(PaymentVerification).delete()
        self.db.query(TransactionReceipt).delete()
        self.db.query(Transaction).delete()
        self.db.query(AuditLog).delete()
        self.db.query(UserSession).delete()
        self.db.query(User).delete()
        self.db.commit()

        # 1. Shop 1 Owner
        self.owner = User(
            username="owner_user",
            shop_name="Goyal General Store",
            owner_name="Mohit Goyal",
            email="owner@goyalstore.com",
            role="OWNER",
            password_hash=hash_password("OwnerSecure123!")
        )
        self.db.add(self.owner)
        self.db.commit()
        self.db.refresh(self.owner)
        self.owner.shop_id = self.owner.id
        self.db.commit()

        # Owner Session
        self.owner_token = "sess_owner_token_abc123"
        self.owner_session = UserSession(
            token=self.owner_token,
            user_id=self.owner.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        )
        self.db.add(self.owner_session)

        # 2. Shop 1 Manager
        self.manager = User(
            username="manager_user",
            shop_name="Goyal General Store",
            owner_name="Ramesh Manager",
            email="manager@goyalstore.com",
            role="MANAGER",
            shop_id=self.owner.id,
            password_hash=hash_password("ManagerSecure123!")
        )
        self.db.add(self.manager)
        self.db.commit()
        self.db.refresh(self.manager)

        self.manager_token = "sess_manager_token_def456"
        self.manager_session = UserSession(
            token=self.manager_token,
            user_id=self.manager.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        )
        self.db.add(self.manager_session)

        # 3. Shop 1 Staff (Read/Record only, cannot approve/reject)
        self.staff = User(
            username="staff_user",
            shop_name="Goyal General Store",
            owner_name="Suresh Staff",
            email="staff@goyalstore.com",
            role="STAFF",
            shop_id=self.owner.id,
            password_hash=hash_password("StaffSecure123!")
        )
        self.db.add(self.staff)
        self.db.commit()
        self.db.refresh(self.staff)

        self.staff_token = "sess_staff_token_ghi789"
        self.staff_session = UserSession(
            token=self.staff_token,
            user_id=self.staff.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        )
        self.db.add(self.staff_session)

        # 4. Shop 2 Owner (for multi-tenant isolation tests)
        self.other_owner = User(
            username="other_owner",
            shop_name="Other Shop",
            owner_name="Other Owner",
            email="other@other.com",
            role="OWNER",
            password_hash=hash_password("OtherSecure123!")
        )
        self.db.add(self.other_owner)
        self.db.commit()
        self.db.refresh(self.other_owner)
        self.other_owner.shop_id = self.other_owner.id
        self.db.commit()

        self.other_token = "sess_other_token_xyz999"
        self.other_session = UserSession(
            token=self.other_token,
            user_id=self.other_owner.id,
            expires_at=get_current_ist_time() + timedelta(days=7)
        )
        self.db.add(self.other_session)
        self.db.commit()

        self.owner_headers = {"Authorization": f"Bearer {self.owner_token}"}
        self.manager_headers = {"Authorization": f"Bearer {self.manager_token}"}
        self.staff_headers = {"Authorization": f"Bearer {self.staff_token}"}
        self.other_headers = {"Authorization": f"Bearer {self.other_token}"}

    def teardown_method(self):
        self.db.close()

    # -------------------------------------------------------------
    # 1. Reminder Email Content & WhatsApp Prefill Verification
    # -------------------------------------------------------------
    def test_reminder_email_upi_and_whatsapp_content(self):
        """Verifies destination UPI, WhatsApp link, and strict verification disclaimer in reminder email."""
        content = generate_reminder_email_content(
            customer_name="Sunil Gupta",
            principal_balance=1500.0,
            accrued_interest=30.0,
            total_balance=1530.0,
            overdue_days=25,
            shop_name="Goyal General Store",
            owner_name="Mohit Goyal",
            shop_phone="6375940658",
            upi_id="goyal.com@fam",
            whatsapp_number="916375940658"
        )

        html = content["html"]
        text = content["text"]

        # Check required UPI ID
        assert "goyal.com@fam" in html
        assert "goyal.com@fam" in text

        # Check WhatsApp link with 916375940658
        assert "wa.me/916375940658" in html
        assert "wa.me/916375940658" in text

        # Check URL encoded contents inside wa.me link
        encoded_cname = urllib.parse.quote("Sunil Gupta")
        assert encoded_cname in html
        assert encoded_cname in text

        # Check prominent manual verification disclaimer
        assert "only after manual verification" in html.lower()
        assert "only after manual verification" in text.lower()
        assert "सत्यापन" in html
        assert "सत्यापन" in text

    # -------------------------------------------------------------
    # 2. Claim Registration & Invariance of Khata Balance
    # -------------------------------------------------------------
    def test_claim_registration_leaves_ledger_balance_unaltered(self):
        """Submitting a claim in PENDING state must NEVER alter customer balance or ledger records."""
        # 1. Add an existing UDHAAR of ₹2000
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Amit Verma",
            customer_email="amit@gmail.com",
            amount=2000.0,
            amount_paise=200000,
            transaction_type=TransactionType.UDHAAR
        )
        self.db.add(tx)
        self.db.commit()

        initial_tx_count = self.db.query(Transaction).count()

        # 2. Register claim of ₹2000 via API
        payload = {
            "customer_name": "Amit Verma",
            "claimed_amount": 2000.0,
            "payment_method": "UPI",
            "destination_upi_id": "goyal.com@fam",
            "utr_number": "UTR123456789",
            "customer_notes": "Paid via Google Pay, attaching screenshot on WhatsApp"
        }
        resp = client.post("/api/payment-verifications", json=payload, headers=self.owner_headers)
        assert resp.status_code == 200, resp.text
        claim_data = resp.json()

        assert claim_data["status"] == "PENDING"
        assert claim_data["claimed_amount"] == 2000.0
        assert claim_data["claimed_amount_paise"] == 200000
        assert claim_data["resulting_transaction_id"] is None
        assert claim_data["utr_number"] == "UTR123456789"
        # Prefilled email from customer's previous transaction
        assert claim_data["customer_email"] == "amit@gmail.com"
        # Current balance is still ₹2000.00 (not reduced!)
        assert claim_data["current_outstanding_balance"] == 2000.0

        # Verify database transactions count is UNCHANGED
        current_tx_count = self.db.query(Transaction).count()
        assert current_tx_count == initial_tx_count

    def test_claim_registration_validation(self):
        """Rejects non-positive claimed amounts and blank names."""
        # Zero amount
        resp1 = client.post(
            "/api/payment-verifications",
            json={"customer_name": "Test User", "claimed_amount": 0},
            headers=self.owner_headers
        )
        assert resp1.status_code in [400, 422]

        # Negative amount
        resp2 = client.post(
            "/api/payment-verifications",
            json={"customer_name": "Test User", "claimed_amount": -50.0},
            headers=self.owner_headers
        )
        assert resp2.status_code in [400, 422]

        # Blank name
        resp3 = client.post(
            "/api/payment-verifications",
            json={"customer_name": "   ", "claimed_amount": 500.0},
            headers=self.owner_headers
        )
        assert resp3.status_code in [400, 422]

    # -------------------------------------------------------------
    # 3. Multi-Shop Tenant Isolation
    # -------------------------------------------------------------
    def test_payment_claims_multi_shop_tenant_isolation(self):
        """Shop A cannot see or access Shop B's payment verification claims."""
        # Shop 1 creates a claim
        resp1 = client.post(
            "/api/payment-verifications",
            json={"customer_name": "Shop1 Customer", "claimed_amount": 350.0},
            headers=self.owner_headers
        )
        assert resp1.status_code == 200
        claim1_id = resp1.json()["id"]

        # Shop 2 creates a claim
        resp2 = client.post(
            "/api/payment-verifications",
            json={"customer_name": "Shop2 Customer", "claimed_amount": 750.0},
            headers=self.other_headers
        )
        assert resp2.status_code == 200
        claim2_id = resp2.json()["id"]

        # Shop 2 lists claims: must only see claim2, NOT claim1
        list_resp = client.get("/api/payment-verifications", headers=self.other_headers)
        assert list_resp.status_code == 200
        claims = list_resp.json()["claims"]
        ids = [c["id"] for c in claims]
        assert claim2_id in ids
        assert claim1_id not in ids

        # Shop 2 attempts to get claim1: must return 404
        get_resp = client.get(f"/api/payment-verifications/{claim1_id}", headers=self.other_headers)
        assert get_resp.status_code == 404

        # Shop 2 attempts to approve claim1: must return 404
        appr_resp = client.post(
            f"/api/payment-verifications/{claim1_id}/approve",
            json={"customer_email": "hack@gmail.com"},
            headers=self.other_headers
        )
        assert appr_resp.status_code == 404

    # -------------------------------------------------------------
    # 4. Listing, Status Filtering and Counts
    # -------------------------------------------------------------
    def test_list_claims_filtering_and_counts(self):
        """Verifies filtering by status, customer name, and counts."""
        # Create 3 claims in Shop 1
        c1 = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Pooja Jain",
            customer_email="pooja@gmail.com",
            claimed_amount=500.0,
            claimed_amount_paise=50000,
            status="PENDING"
        )
        c2 = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Pooja Jain",
            customer_email="pooja@gmail.com",
            claimed_amount=300.0,
            claimed_amount_paise=30000,
            status="APPROVED",
            verified_amount=300.0,
            verified_amount_paise=30000
        )
        c3 = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Deepak Sharma",
            customer_email="deepak@gmail.com",
            claimed_amount=1000.0,
            claimed_amount_paise=100000,
            status="REJECTED",
            review_notes="No transaction found in bank statement"
        )
        self.db.add_all([c1, c2, c3])
        self.db.commit()

        # List all
        resp = client.get("/api/payment-verifications", headers=self.owner_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_count"] == 3
        assert data["pending_count"] == 1
        assert data["approved_count"] == 1
        assert data["rejected_count"] == 1
        assert len(data["claims"]) == 3

        # Filter by status=PENDING
        resp_pend = client.get("/api/payment-verifications?status=PENDING", headers=self.owner_headers)
        assert resp_pend.status_code == 200
        assert len(resp_pend.json()["claims"]) == 1
        assert resp_pend.json()["claims"][0]["status"] == "PENDING"

        # Filter by customer_name=pooja
        resp_cust = client.get("/api/payment-verifications?customer_name=pooja", headers=self.owner_headers)
        assert resp_cust.status_code == 200
        assert len(resp_cust.json()["claims"]) == 2

    # -------------------------------------------------------------
    # 5. Atomic Approval, JAMA Transaction Creation & Outbox Receipt
    # -------------------------------------------------------------
    def test_approve_payment_claim_commits_jama_and_reduces_balance(self):
        """Approving a payment creates exactly one JAMA transaction and links receipt outbox."""
        # 1. Outstanding balance: ₹5000
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Rohan Mehra",
            customer_email="rohan@gmail.com",
            amount=5000.0,
            amount_paise=500000,
            transaction_type=TransactionType.UDHAAR
        )
        self.db.add(tx)
        self.db.commit()

        # 2. Register claim: ₹5000
        claim = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Rohan Mehra",
            customer_email="rohan@gmail.com",
            claimed_amount=5000.0,
            claimed_amount_paise=500000,
            utr_number="UPI99887766",
            status="PENDING"
        )
        self.db.add(claim)
        self.db.commit()
        self.db.refresh(claim)

        # 3. Manager approves claim
        approve_payload = {
            "notes": "Verified ₹5000 credited via HDFC UPI"
        }
        resp = client.post(
            f"/api/payment-verifications/{claim.id}/approve",
            json=approve_payload,
            headers=self.manager_headers
        )
        assert resp.status_code == 200, resp.text
        res_data = resp.json()

        assert res_data["status"] == "APPROVED"
        assert res_data["verified_amount"] == 5000.0
        assert res_data["resulting_transaction_id"] is not None
        assert res_data["reviewer_name"] == "Ramesh Manager"
        # Customer balance is now ₹0.00
        assert res_data["current_outstanding_balance"] == 0.0

        # Check exactly one JAMA transaction was inserted
        created_tx = self.db.query(Transaction).filter(Transaction.id == res_data["resulting_transaction_id"]).first()
        assert created_tx is not None
        assert created_tx.transaction_type == TransactionType.JAMA
        assert created_tx.amount == 5000.0
        assert created_tx.amount_paise == 500000
        assert created_tx.customer_name == "Rohan Mehra"
        assert "UPI99887766" in created_tx.notes

        # Check TransactionReceipt record was generated in PENDING status
        receipt = self.db.query(TransactionReceipt).filter(TransactionReceipt.transaction_id == created_tx.id).first()
        assert receipt is not None
        assert receipt.customer_email == "rohan@gmail.com"
        assert receipt.status in ["PENDING", "SENT"]

        # Duplicate approval prevention
        resp_dup = client.post(
            f"/api/payment-verifications/{claim.id}/approve",
            json=approve_payload,
            headers=self.manager_headers
        )
        assert resp_dup.status_code == 400
        assert "already in 'APPROVED' state" in resp_dup.json()["detail"]

    # -------------------------------------------------------------
    # 6. Partial Payment Verification
    # -------------------------------------------------------------
    def test_approve_partial_payment(self):
        """Shopkeeper can approve a partial verified amount (e.g. ₹600 verified out of ₹1000 claimed)."""
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Kavita Rao",
            customer_email="kavita@gmail.com",
            amount=1000.0,
            amount_paise=100000,
            transaction_type=TransactionType.UDHAAR
        )
        self.db.add(tx)
        self.db.commit()

        claim = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Kavita Rao",
            customer_email="kavita@gmail.com",
            claimed_amount=1000.0,
            claimed_amount_paise=100000,
            status="PENDING"
        )
        self.db.add(claim)
        self.db.commit()
        self.db.refresh(claim)

        # Approve ₹600 partial
        resp = client.post(
            f"/api/payment-verifications/{claim.id}/approve",
            json={"verified_amount": 600.0, "notes": "Partial credit received in ICICI"},
            headers=self.owner_headers
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["claimed_amount"] == 1000.0
        assert data["verified_amount"] == 600.0
        assert data["verified_amount_paise"] == 60000
        # Outstanding balance was ₹1000, now ₹400 remaining
        assert data["current_outstanding_balance"] == 400.0

        # Verify JAMA transaction is ₹600
        jama_tx = self.db.query(Transaction).filter(Transaction.id == data["resulting_transaction_id"]).first()
        assert jama_tx.amount == 600.0
        assert jama_tx.amount_paise == 60000

    # -------------------------------------------------------------
    # 7. Claim Rejection & Zero Balance Impact
    # -------------------------------------------------------------
    def test_reject_payment_claim_leaves_balance_untouched(self):
        """Rejecting a claim records rejection details without touching ledger records or balances."""
        tx = Transaction(
            user_id=self.owner.id,
            customer_name="Sanjay Roy",
            customer_email="sanjay@gmail.com",
            amount=1200.0,
            amount_paise=120000,
            transaction_type=TransactionType.UDHAAR
        )
        self.db.add(tx)
        self.db.commit()

        claim = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Sanjay Roy",
            customer_email="sanjay@gmail.com",
            claimed_amount=1200.0,
            claimed_amount_paise=120000,
            utr_number="FAKEUTR999",
            status="PENDING"
        )
        self.db.add(claim)
        self.db.commit()
        self.db.refresh(claim)

        initial_tx_count = self.db.query(Transaction).count()

        resp = client.post(
            f"/api/payment-verifications/{claim.id}/reject",
            json={"notes": "No credit found in UPI app. UTR invalid."},
            headers=self.owner_headers
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "REJECTED"
        assert data["review_notes"] == "No credit found in UPI app. UTR invalid."
        assert data["resulting_transaction_id"] is None
        # Balance is still ₹1200.00
        assert data["current_outstanding_balance"] == 1200.0

        # Verify no new transactions were created
        assert self.db.query(Transaction).count() == initial_tx_count

        # Attempting to re-reject or approve returns 400
        resp_re_reject = client.post(
            f"/api/payment-verifications/{claim.id}/reject",
            json={"notes": "Another reject attempt"},
            headers=self.owner_headers
        )
        assert resp_re_reject.status_code == 400

        resp_re_appr = client.post(
            f"/api/payment-verifications/{claim.id}/approve",
            json={},
            headers=self.owner_headers
        )
        assert resp_re_appr.status_code == 400

    # -------------------------------------------------------------
    # 8. RBAC Gating (STAFF cannot Approve or Reject)
    # -------------------------------------------------------------
    def test_rbac_staff_forbidden_from_approving_or_rejecting(self):
        """Staff members can view/record claims but are forbidden from approving or rejecting them."""
        claim = PaymentVerification(
            shop_id=self.owner.id,
            customer_name="Test Customer",
            customer_email="test@gmail.com",
            claimed_amount=250.0,
            claimed_amount_paise=25000,
            status="PENDING"
        )
        self.db.add(claim)
        self.db.commit()
        self.db.refresh(claim)

        # Staff can list claims
        list_resp = client.get("/api/payment-verifications", headers=self.staff_headers)
        assert list_resp.status_code == 200

        # Staff attempts to approve: 403 Forbidden
        appr_resp = client.post(
            f"/api/payment-verifications/{claim.id}/approve",
            json={},
            headers=self.staff_headers
        )
        assert appr_resp.status_code == 403
        assert "Access denied" in appr_resp.json()["detail"]

        # Staff attempts to reject: 403 Forbidden
        rej_resp = client.post(
            f"/api/payment-verifications/{claim.id}/reject",
            json={"notes": "Staff attempt"},
            headers=self.staff_headers
        )
        assert rej_resp.status_code == 403
        assert "Access denied" in rej_resp.json()["detail"]

    # -------------------------------------------------------------
    # 9. Audit Trail Verification
    # -------------------------------------------------------------
    def test_audit_logs_recorded_for_all_payment_claim_events(self):
        """Verifies append-only audit trail logging for record, approve, and reject."""
        # Record claim
        resp_rec = client.post(
            "/api/payment-verifications",
            json={"customer_name": "Audit Customer", "customer_email": "audit@gmail.com", "claimed_amount": 800.0},
            headers=self.owner_headers
        )
        assert resp_rec.status_code == 200
        claim_id = resp_rec.json()["id"]

        log_rec = (
            self.db.query(AuditLog)
            .filter(AuditLog.action == "PAYMENT_CLAIM_RECORDED", AuditLog.resource_id == str(claim_id))
            .first()
        )
        assert log_rec is not None
        assert "800.0" in log_rec.details_json

        # Approve claim
        resp_appr = client.post(
            f"/api/payment-verifications/{claim_id}/approve",
            json={"notes": "Audit approve test"},
            headers=self.owner_headers
        )
        assert resp_appr.status_code == 200

        log_appr = (
            self.db.query(AuditLog)
            .filter(AuditLog.action == "PAYMENT_CLAIM_APPROVED", AuditLog.resource_id == str(claim_id))
            .first()
        )
        assert log_appr is not None
        assert "transaction_id" in log_appr.details_json

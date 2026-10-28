"""
Comprehensive automated test suite for HisabAI.
Uses an isolated in-memory SQLite database (sqlite:///:memory:) with StaticPool
and mocks the Google GenAI service so tests never require a live API key.
"""

import os
import sqlite3
from datetime import datetime, timedelta
import unittest
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app
from models import (
    ExtractedTransaction,
    KOLKATA_TZ,
    PendingOperationData,
    Transaction,
    TransactionType,
    VoiceAssistantRequest,
    VoiceAssistantResponse,
    VoiceIntentResult,
    VoiceIntentType,
    get_current_ist_time,
)
import ai_service
from ai_service import (
    AIAmbiguityError,
    AIConfigurationError,
    AIProviderError,
    fast_match_voice_intent,
    normalize_hindi_number_words,
    redact_secrets,
    strip_markdown_fences,
    strip_wake_word,
)

# Test-specific in-memory SQLite database
TEST_DB_URL = "sqlite:///:memory:"

test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSession = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=test_engine
)


def override_get_db():
    """Dependency override providing isolated in-memory DB session."""
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


class TestHisabAIFullSuite(unittest.TestCase):
    """Complete test suite for HisabAI application."""

    def setUp(self):
        """Create fresh in-memory tables before each test."""
        Base.metadata.create_all(bind=test_engine)

    def tearDown(self):
        """Drop all in-memory tables after each test."""
        Base.metadata.drop_all(bind=test_engine)

    # 0. Favicon Endpoint
    def test_favicon_endpoint(self):
        """Verifies /favicon.ico returns SVG icon with 200 OK."""
        response = client.get("/favicon.ico")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        self.assertIn("<svg", response.text)

    # 1. Health Check
    def test_health_endpoint(self):
        """Verifies /api/health reports system status and database connectivity."""
        response = client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("status", data)
        self.assertEqual(data["database"], "connected")
        self.assertIn("ai_configured", data)
        self.assertIn("server_time_ist", data)

    def test_health_ai_configured_flag(self):
        """Verifies /api/health accurately reflects whether AI is configured."""
        with patch("ai_service.is_ai_configured", return_value=True):
            resp_true = client.get("/api/health")
            self.assertTrue(resp_true.json()["ai_configured"])

        with patch("ai_service.is_ai_configured", return_value=False):
            resp_false = client.get("/api/health")
            self.assertFalse(resp_false.json()["ai_configured"])

    # 2. Parsing Endpoint (POST /api/parse)
    def test_parse_endpoint_success(self):
        """Verifies /api/parse extracts transaction details without saving to DB."""
        mock_result = ExtractedTransaction(
            customer_name="Sharma ji",
            amount=200.0,
            transaction_type="UDHAAR"
        )
        with patch("ai_service.parse_hinglish_input", return_value=mock_result):
            resp = client.post("/api/parse", json={"text": "Sharma ji ka 200 udhaar likh do"})

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["customer_name"], "Sharma ji")
        self.assertEqual(data["amount"], 200.0)
        self.assertEqual(data["transaction_type"], "UDHAAR")

        # Verify nothing was saved to DB during parse
        db = TestingSession()
        count = db.query(Transaction).count()
        db.close()
        self.assertEqual(count, 0)

    def test_parse_endpoint_ambiguity_returns_400(self):
        """Verifies /api/parse returns 400 Bad Request when phrase lacks details."""
        with patch("ai_service.parse_hinglish_input", side_effect=AIAmbiguityError("Essential details missing")):
            resp = client.post("/api/parse", json={"text": "kuch bhi likh do"})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("missing", resp.json()["detail"].lower())

    def test_parse_endpoint_missing_ai_key_returns_503(self):
        """Verifies /api/parse returns 503 Service Unavailable when GEMMA_API_KEY is missing."""
        with patch("ai_service.parse_hinglish_input", side_effect=AIConfigurationError("GEMMA_API_KEY is not configured")):
            resp = client.post("/api/parse", json={"text": "Sharma ji 200 udhaar"})

        self.assertEqual(resp.status_code, 503)
        self.assertIn("not configured", resp.json()["detail"].lower())

    def test_parse_endpoint_provider_failure_returns_502(self):
        """Verifies /api/parse returns 502 Bad Gateway when Google API fails or times out."""
        with patch("ai_service.parse_hinglish_input", side_effect=AIProviderError("AI service temporarily unavailable")):
            resp = client.post("/api/parse", json={"text": "Sharma ji 200 udhaar"})

        self.assertEqual(resp.status_code, 502)
        self.assertIn("unavailable", resp.json()["detail"].lower())

    def test_parse_endpoint_empty_text_rejected(self):
        """Verifies /api/parse rejects empty or whitespace-only inputs."""
        resp = client.post("/api/parse", json={"text": "   "})
        self.assertEqual(resp.status_code, 422)

    # 3. Transaction Creation (POST /api/transaction)
    def test_create_transaction_confirmed_udhaar(self):
        """Verifies saving a confirmed UDHAAR transaction stores exact integer paise."""
        payload = {
            "customer_name": "Raju Kirana",
            "amount": 350.50,
            "transaction_type": "UDHAAR"
        }
        resp = client.post("/api/transaction", json=payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["customer_name"], "Raju Kirana")
        self.assertEqual(data["amount"], 350.50)
        self.assertEqual(data["transaction_type"], "UDHAAR")

        # Verify in DB that amount_paise is exact integer 35050
        db = TestingSession()
        tx = db.query(Transaction).filter_by(id=data["id"]).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount_paise, 35050)
        self.assertEqual(tx.amount, 350.50)
        db.close()

    def test_create_transaction_confirmed_jama(self):
        """Verifies saving a confirmed JAMA transaction."""
        payload = {
            "customer_name": "Raju Kirana",
            "amount": 200.0,
            "transaction_type": "JAMA"
        }
        resp = client.post("/api/transaction", json=payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["transaction_type"], "JAMA")
        self.assertEqual(data["amount"], 200.0)

    def test_create_transaction_rejects_negative_or_zero(self):
        """Verifies non-positive amounts are rejected with 400 or 422."""
        resp_zero = client.post(
            "/api/transaction",
            json={"customer_name": "Gupta ji", "amount": 0, "transaction_type": "UDHAAR"}
        )
        self.assertIn(resp_zero.status_code, (400, 422))

        resp_neg = client.post(
            "/api/transaction",
            json={"customer_name": "Gupta ji", "amount": -100, "transaction_type": "UDHAAR"}
        )
        self.assertIn(resp_neg.status_code, (400, 422))

    def test_create_transaction_rejects_empty_name(self):
        """Verifies blank customer name is rejected."""
        resp = client.post(
            "/api/transaction",
            json={"customer_name": "   ", "amount": 100, "transaction_type": "UDHAAR"}
        )
        self.assertIn(resp.status_code, (400, 422))

    def test_create_transaction_backwards_compatibility_raw_text(self):
        """Verifies POST /api/transaction supports legacy raw text payload."""
        mock_result = ExtractedTransaction(
            customer_name="Verma ji",
            amount=500.0,
            transaction_type="UDHAAR"
        )
        with patch("ai_service.parse_hinglish_input", return_value=mock_result):
            resp = client.post("/api/transaction", json={"text": "Verma ji ka 500 udhaar"})

        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["customer_name"], "Verma ji")
        self.assertEqual(data["amount"], 500.0)

    # 4. Review-Before-Save Workflow Simulation
    def test_review_before_save_workflow(self):
        """
        Simulates the full user workflow:
        1. Parse Hinglish command.
        2. User reviews and edits the customer name / amount.
        3. User confirms and saves the edited transaction.
        """
        # Step 1: Parse
        mock_ai = ExtractedTransaction(
            customer_name="Sharma",
            amount=200.0,
            transaction_type="UDHAAR"
        )
        with patch("ai_service.parse_hinglish_input", return_value=mock_ai):
            parse_resp = client.post("/api/parse", json={"text": "Sharma ka 200 udhaar"})
        self.assertEqual(parse_resp.status_code, 200)

        # Step 2 & 3: User edits "Sharma" -> "Sharma ji", 200.0 -> 250.0 and confirms
        save_resp = client.post(
            "/api/transaction",
            json={
                "customer_name": "Sharma ji",
                "amount": 250.0,
                "transaction_type": "UDHAAR"
            }
        )
        self.assertEqual(save_resp.status_code, 201)
        saved = save_resp.json()
        self.assertEqual(saved["customer_name"], "Sharma ji")
        self.assertEqual(saved["amount"], 250.0)

    # 5. Daily Summary (GET /api/summary) & IST Day Boundaries
    def test_daily_summary_and_ist_day_boundaries(self):
        """Verifies today's summary strictly adheres to Asia/Kolkata business date."""
        db = TestingSession()
        now_ist = get_current_ist_time()

        # Transaction 1: Today UDHAAR (₹300.25)
        tx1 = Transaction(
            customer_name="Customer A",
            amount_paise=30025,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now_ist
        )
        # Transaction 2: Today JAMA (₹100.25)
        tx2 = Transaction(
            customer_name="Customer A",
            amount_paise=10025,
            transaction_type=TransactionType.JAMA,
            timestamp=now_ist
        )
        # Transaction 3: Yesterday transaction (must NOT be counted in today's summary)
        yesterday_ist = now_ist - timedelta(days=1)
        tx_old = Transaction(
            customer_name="Customer B",
            amount_paise=99900,
            transaction_type=TransactionType.UDHAAR,
            timestamp=yesterday_ist
        )
        db.add_all([tx1, tx2, tx_old])
        db.commit()
        db.close()

        resp = client.get("/api/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["date"], now_ist.strftime("%Y-%m-%d"))
        self.assertEqual(data["total_udhaar_today"], 300.25)
        self.assertEqual(data["total_jama_today"], 100.25)
        self.assertEqual(data["net_today"], 200.0)  # 300.25 - 100.25
        self.assertEqual(data["transaction_count"], 2)
        self.assertEqual(len(data["transactions"]), 2)

    # 6. Customer Ledger (GET /api/customer/{name})
    def test_customer_ledger_case_insensitivity_and_spaces(self):
        """Verifies customer balance lookup handles casing, spaces, and URL encoding."""
        db = TestingSession()
        now_ist = get_current_ist_time()

        # Sharma ji takes ₹500 credit, pays ₹200 back
        tx1 = Transaction(
            customer_name="Sharma ji",
            amount_paise=50000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now_ist
        )
        tx2 = Transaction(
            customer_name="Sharma ji",
            amount_paise=20000,
            transaction_type=TransactionType.JAMA,
            timestamp=now_ist
        )
        db.add_all([tx1, tx2])
        db.commit()
        db.close()

        # Query with lowercase and encoded space ("sharma%20ji")
        resp = client.get("/api/customer/sharma%20ji")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["customer_name"], "Sharma ji")
        self.assertEqual(data["lifetime_udhaar"], 500.0)
        self.assertEqual(data["lifetime_jama"], 200.0)
        self.assertEqual(data["outstanding_balance"], 300.0)
        self.assertIn("300", data["status_label"])
        self.assertEqual(data["transaction_count"], 2)

    def test_customer_not_found(self):
        """Verifies 404 response when customer does not exist in ledger."""
        resp = client.get("/api/customer/NonExistentCustomer")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("not found", resp.json()["detail"].lower())

    # 7. Financial Precision Test (No binary float drift)
    def test_integer_paise_financial_precision(self):
        """
        Verifies that multiple fractional transactions (e.g. 0.10 + 0.20)
        do not suffer from floating-point accumulation drift (e.g. 0.30000000000000004).
        """
        db = TestingSession()
        now_ist = get_current_ist_time()

        # Add 10 transactions of ₹0.10 each
        for _ in range(10):
            db.add(Transaction(
                customer_name="PrecisionTest",
                amount_paise=10,  # 10 paise = ₹0.10
                transaction_type=TransactionType.UDHAAR,
                timestamp=now_ist
            ))
        db.commit()
        db.close()

        resp = client.get("/api/customer/PrecisionTest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["lifetime_udhaar"], 1.0)
        self.assertEqual(data["outstanding_balance"], 1.0)

    # 8. Unit Tests for AI Service Logic & Security
    def test_redact_secrets(self):
        """Verifies sensitive API keys are stripped from error logs."""
        raw_msg = "Error using key=AIzaSyDUMMY_zaSyD-1234567890abcdef1234567890abcde"
        clean_msg = redact_secrets(raw_msg)
        self.assertNotIn("AIzaSyD", clean_msg)
        self.assertIn("[REDACTED", clean_msg)

    def test_strip_markdown_fences(self):
        """Verifies clean JSON extraction from code-blocked AI replies."""
        fenced = "```json\n{\"customer_name\": \"Raju\", \"amount\": 100, \"transaction_type\": \"JAMA\"}\n```"
        clean = strip_markdown_fences(fenced)
        self.assertEqual(clean, '{"customer_name": "Raju", "amount": 100, "transaction_type": "JAMA"}')

    def test_ai_service_missing_key_raises_configuration_error(self):
        """Verifies parse_hinglish_input raises AIConfigurationError when key is absent."""
        with patch("ai_service.get_configured_api_key", return_value=None):
            with patch("ai_service.is_ai_configured", return_value=False):
                with self.assertRaises(AIConfigurationError):
                    ai_service.parse_hinglish_input("Raju 200 udhaar")

    def test_ai_service_empty_text_raises_ambiguity_error(self):
        """Verifies parse_hinglish_input raises AIAmbiguityError on empty text."""
        with patch("ai_service.is_ai_configured", return_value=True):
            with self.assertRaises(AIAmbiguityError):
                ai_service.parse_hinglish_input("")

    # 9. Voice Assistant Fast-Match & NLP Preprocessing Unit Tests
    def test_normalize_devanagari_numbers(self):
        """Verifies Devanagari Hindi number words are converted to digits."""
        self.assertIn("250", normalize_hindi_number_words("ढाई सौ रुपये"))
        self.assertIn("2000", normalize_hindi_number_words("दो हजार रुपये"))
        self.assertIn("500", normalize_hindi_number_words("पाँच सौ"))
        self.assertIn("500", normalize_hindi_number_words("पांच सौ"))
        self.assertIn("300", normalize_hindi_number_words("तीन सौ"))
        self.assertIn("1000", normalize_hindi_number_words("एक हजार"))
        self.assertIn("50", normalize_hindi_number_words("पचास"))
        self.assertIn("100", normalize_hindi_number_words("एक सौ"))

    def test_rupee_symbol_normalization(self):
        """Verifies ₹ symbol prefix is stripped from numbers before parsing."""
        self.assertEqual(normalize_hindi_number_words("₹2000"), "2000")
        self.assertEqual(normalize_hindi_number_words("₹ 500"), "500")
        # Embedded in sentence
        result = normalize_hindi_number_words("₹2000 ka udhaar")
        self.assertIn("2000", result)
        self.assertNotIn("₹", result)

    def test_strip_wake_word_devanagari_merged(self):
        """Verifies merged Devanagari wake word (no space) is stripped correctly."""
        self.assertEqual(strip_wake_word("सुनोहिसाब राजू का"), "राजू का")
        self.assertEqual(strip_wake_word("सुनो हिसाब Sharma ji ka"), "Sharma ji ka")

    def test_voice_assistant_greeting_unknown(self):
        """Wake-word-only or greeting should NOT create a pending transaction."""
        resp = client.post("/api/voice-assistant", json={"text": "Suno Hisab, kaise ho?"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Must not require confirmation (not a financial entry)
        self.assertFalse(data["requires_confirmation"])
        self.assertIsNone(data["pending_operation"])

    def test_voice_assistant_wake_word_only(self):
        """Wake word alone should not create a transaction."""
        resp = client.post("/api/voice-assistant", json={"text": "Suno Hisab"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["requires_confirmation"])
        self.assertIsNone(data["pending_operation"])

    def test_voice_assistant_devanagari_balance_query(self):
        """Devanagari voice query for customer balance should be handled."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add(Transaction(
            customer_name="Sharma ji",
            amount_paise=25000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now_ist
        ))
        db.commit()
        db.close()

        resp = client.post("/api/voice-assistant", json={"text": "शर्मा जी का कितना बकाया है?"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(data["requires_confirmation"])
        # Should be a query response (not entry)
        self.assertIsNone(data["pending_operation"])

    def test_normalize_hindi_number_words(self):
        """Verifies conversion of spoken Hindi number words to digits."""
        self.assertEqual(normalize_hindi_number_words("paanch sau rupaye"), "500 rupaye")
        self.assertEqual(normalize_hindi_number_words("ek hazaar do sau"), "1200")
        self.assertEqual(normalize_hindi_number_words("pachas rupaye"), "50 rupaye")

    def test_strip_wake_word(self):
        """Verifies stripping of 'Suno Hisab' variations and Devnagari wake words."""
        self.assertEqual(strip_wake_word("Suno Hisab Sharma ji ka hisab"), "Sharma ji ka hisab")
        self.assertEqual(strip_wake_word("suno hisaab aaj ka summary"), "aaj ka summary")
        self.assertEqual(strip_wake_word("सुनो हिसाब राजू का 500 जमा"), "राजू का 500 जमा")
        self.assertEqual(strip_wake_word("Suno hisab"), "")

    def test_fast_match_voice_intents(self):
        """Verifies fast, offline matching for confirmation, cancellation, and corrections."""
        # Confirmation
        c1 = fast_match_voice_intent("haan")
        self.assertIsNotNone(c1)
        self.assertEqual(c1.intent, VoiceIntentType.CONFIRM)

        c2 = fast_match_voice_intent("theek hai save kar do")
        self.assertIsNotNone(c2)
        self.assertEqual(c2.intent, VoiceIntentType.CONFIRM)

        # Cancellation
        can = fast_match_voice_intent("nahi cancel karo")
        self.assertIsNotNone(can)
        self.assertEqual(can.intent, VoiceIntentType.CANCEL)

        # Stop
        stp = fast_match_voice_intent("band karo")
        self.assertIsNotNone(stp)
        self.assertEqual(stp.intent, VoiceIntentType.STOP_ASSISTANT)

        # Repeat
        rep = fast_match_voice_intent("dobara sunao")
        self.assertIsNotNone(rep)
        self.assertEqual(rep.intent, VoiceIntentType.REPEAT_LAST_RESPONSE)

        # Numeric correction
        cor = fast_match_voice_intent("500 nahi 800")
        self.assertIsNotNone(cor)
        self.assertEqual(cor.intent, VoiceIntentType.CORRECT_PENDING_ENTRY)
        self.assertEqual(cor.amount, 800.0)

    # 10. Voice Assistant Endpoints & Two-Step Workflow Tests
    def test_voice_assistant_balance_query(self):
        """Verifies read-only customer balance lookup via voice assistant."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add_all([
            Transaction(
                customer_name="Sharma ji",
                amount_paise=70000,
                transaction_type=TransactionType.UDHAAR,
                timestamp=now_ist
            ),
            Transaction(
                customer_name="Sharma ji",
                amount_paise=20000,
                transaction_type=TransactionType.JAMA,
                timestamp=now_ist
            )
        ])
        db.commit()
        db.close()

        req = {"text": "Sharma ji ka kitna baaki hai?"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["action_taken"], "customer_balance")
        self.assertIn("500", data["reply_message"])
        self.assertIn("Sharma ji", data["reply_message"])

    def test_voice_assistant_balance_query_not_found(self):
        """Verifies voice assistant response when customer does not exist."""
        req = {"text": "Kabir ka kitna baaki hai?"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["action_taken"], "customer_balance")
        self.assertIn("koi customer nahi mila", data["reply_message"])

    def test_voice_assistant_daily_summary(self):
        """Verifies voice assistant daily summary response."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add_all([
            Transaction(
                customer_name="A",
                amount_paise=100000,
                transaction_type=TransactionType.UDHAAR,
                timestamp=now_ist
            ),
            Transaction(
                customer_name="B",
                amount_paise=40000,
                transaction_type=TransactionType.JAMA,
                timestamp=now_ist
            )
        ])
        db.commit()
        db.close()

        req = {"text": "Aaj ka hisab batao"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["action_taken"], "daily_summary")
        self.assertIn("1,000", data["reply_message"])
        self.assertIn("400", data["reply_message"])

    def test_voice_assistant_customer_list(self):
        """Verifies voice assistant reports outstanding debts list."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add(Transaction(
            customer_name="Gupta ji",
            amount_paise=50000,
            transaction_type=TransactionType.UDHAAR,
            timestamp=now_ist
        ))
        db.commit()
        db.close()

        req = {"text": "Kis kis ka udhaar bacha hai?"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertEqual(data["action_taken"], "customer_list")
        self.assertIn("Gupta ji", data["reply_message"])

    def test_voice_assistant_two_step_entry_and_confirmation_idempotency(self):
        """
        Verifies:
        1. Step 1 (ENTRY) creates a pending operation and does NOT write to database.
        2. Step 2 (CONFIRM) writes exactly once to database.
        3. Repeating Step 2 (Idempotency) returns the already committed transaction and does NOT duplicate DB rows.
        """
        # Step 1: Speak an entry command
        req1 = {"text": "Suno Hisab Raju ka 350 rupaye udhaar likh do"}
        resp1 = client.post("/api/voice-assistant", json=req1)
        self.assertEqual(resp1.status_code, 200)
        data1 = resp1.json()

        self.assertEqual(data1["action_taken"], "prepared_entry")
        self.assertTrue(data1["requires_confirmation"])
        self.assertIsNotNone(data1["pending_operation"])
        pending_id = data1["pending_operation"]["operation_id"]

        # Ensure NO transaction was written to DB yet
        db = TestingSession()
        tx_count_before = db.query(Transaction).count()
        db.close()
        self.assertEqual(tx_count_before, 0)

        # Step 2: Confirm with "haan"
        req2 = {"text": "haan save kar do", "pending_operation_id": pending_id}
        resp2 = client.post("/api/voice-assistant", json=req2)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()

        self.assertEqual(data2["action_taken"], "committed_transaction")
        self.assertIn("Raju", data2["reply_message"])
        self.assertIn("350", data2["reply_message"])

        # Check DB has exactly 1 record
        db = TestingSession()
        all_tx = db.query(Transaction).all()
        self.assertEqual(len(all_tx), 1)
        self.assertEqual(all_tx[0].customer_name, "Raju")
        self.assertEqual(all_tx[0].amount_paise, 35000)
        db.close()

        # Step 3: Repeated confirmation (Idempotency check)
        req3 = {"text": "confirm", "pending_operation_id": pending_id}
        resp3 = client.post("/api/voice-assistant", json=req3)
        self.assertEqual(resp3.status_code, 200)
        data3 = resp3.json()
        self.assertEqual(data3["action_taken"], "already_committed")
        self.assertIn("Raju", data3["reply_message"])

        # Check DB STILL has exactly 1 record
        db = TestingSession()
        tx_count_after = db.query(Transaction).count()
        db.close()
        self.assertEqual(tx_count_after, 1)

    def test_voice_assistant_expired_pending_operation(self):
        """Verifies that an expired pending operation is rejected and not committed."""
        import main
        from datetime import timedelta
        # Setup an expired pending operation directly in main.PENDING_VOICE_OPERATIONS
        expired_id = "test-expired-op-123"
        past_time = get_current_ist_time() - timedelta(minutes=10)
        main.PENDING_VOICE_OPERATIONS[expired_id] = {
            "operation_id": expired_id,
            "customer_name": "Late Customer",
            "amount": 999.0,
            "transaction_type": "UDHAAR",
            "session_id": "test",
            "created_dt": past_time,
            "expires_dt": past_time,
            "created_at": past_time.isoformat(),
            "expires_at": past_time.isoformat(),
            "status": "pending",
            "committed_tx_id": None
        }

        # Attempt confirmation on expired op
        req = {"text": "haan", "pending_operation_id": expired_id, "action": "confirm"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["action_taken"], "expired")
        self.assertFalse(data["requires_confirmation"])

        # Verify no database entry was created
        db = TestingSession()
        tx = db.query(Transaction).filter_by(customer_name="Late Customer").first()
        self.assertIsNone(tx)
        db.close()

    def test_voice_assistant_entry_cancellation(self):
        """Verifies cancellation discards pending operation without any database write."""
        req1 = {"text": "Mohan ka 500 jama likh do"}
        resp1 = client.post("/api/voice-assistant", json=req1)
        self.assertEqual(resp1.status_code, 200)
        pending_id = resp1.json()["pending_operation"]["operation_id"]

        # User cancels
        req2 = {"text": "nahi mat karo", "pending_operation_id": pending_id}
        resp2 = client.post("/api/voice-assistant", json=req2)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()

        self.assertEqual(data2["action_taken"], "cancelled")
        self.assertIn("cancel kar di gayi", data2["reply_message"])

        db = TestingSession()
        self.assertEqual(db.query(Transaction).count(), 0)
        db.close()

    def test_voice_assistant_entry_correction(self):
        """Verifies user can correct the pending amount before confirming."""
        req1 = {"text": "Sharma ji ka 400 udhaar likho"}
        resp1 = client.post("/api/voice-assistant", json=req1)
        self.assertEqual(resp1.status_code, 200)
        pending_id = resp1.json()["pending_operation"]["operation_id"]

        # Correction: "400 nahi 600"
        req2 = {"text": "400 nahi 600", "pending_operation_id": pending_id}
        resp2 = client.post("/api/voice-assistant", json=req2)
        self.assertEqual(resp2.status_code, 200)
        data2 = resp2.json()

        self.assertEqual(data2["action_taken"], "corrected_pending_entry")
        self.assertEqual(data2["pending_operation"]["amount"], 600.0)

        # Confirm corrected amount
        req3 = {"text": "haan", "pending_operation_id": pending_id}
        resp3 = client.post("/api/voice-assistant", json=req3)
        self.assertEqual(resp3.status_code, 200)

        db = TestingSession()
        tx = db.query(Transaction).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount_paise, 60000)
        db.close()

    # 11. Authentication & Security Tests
    def test_auth_signup_login_logout_flow(self):
        """Verifies complete authentication lifecycle: signup, duplicate rejection, login, auth me, logout."""
        # 1. Signup
        signup_payload = {
            "username": "ramesh_kirana",
            "password": "secure_password_123",
            "shop_name": "Ramesh General Store"
        }
        res_signup = client.post("/api/auth/signup", json=signup_payload)
        self.assertEqual(res_signup.status_code, 200)
        data_signup = res_signup.json()
        self.assertTrue(data_signup["success"])
        self.assertIn("token", data_signup)
        token = data_signup["token"]
        self.assertEqual(data_signup["user"]["username"], "ramesh_kirana")

        # 2. Duplicate signup rejected
        res_dup = client.post("/api/auth/signup", json=signup_payload)
        self.assertEqual(res_dup.status_code, 400)
        self.assertIn("already registered", res_dup.json()["detail"].lower())

        # 3. Access /api/auth/me with Bearer token
        res_me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(res_me.status_code, 200)
        self.assertEqual(res_me.json()["username"], "ramesh_kirana")

        # 4. Login with invalid password
        res_bad_pw = client.post("/api/auth/login", json={"username": "ramesh_kirana", "password": "wrong_password"})
        self.assertEqual(res_bad_pw.status_code, 401)

        # 5. Login with valid password
        res_login = client.post("/api/auth/login", json={"username": "ramesh_kirana", "password": "secure_password_123"})
        self.assertEqual(res_login.status_code, 200)
        new_token = res_login.json()["token"]

        # 6. Logout
        res_logout = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {new_token}"})
        self.assertEqual(res_logout.status_code, 200)
        self.assertTrue(res_logout.json()["success"])

        # 7. Access /api/auth/me after logout -> 401
        res_after = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
        self.assertEqual(res_after.status_code, 401)

    # 12. Customer Directory & Historical Ledger Tests
    def test_customer_directory_and_search(self):
        """Verifies customer directory calculation and search filtering."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add_all([
            Transaction(customer_name="Suresh Kumar", amount_paise=100000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist),
            Transaction(customer_name="Suresh Kumar", amount_paise=30000, transaction_type=TransactionType.JAMA, timestamp=now_ist),
            Transaction(customer_name="Mahesh Ji", amount_paise=50000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist)
        ])
        db.commit()
        db.close()

        # Full directory
        resp = client.get("/api/customers")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_customers"], 2)
        # Total debt: Suresh (700) + Mahesh (500) = 1200
        self.assertEqual(data["total_outstanding_debt"], 1200.0)

        # Search filter
        resp_search = client.get("/api/customers?search=Suresh")
        self.assertEqual(resp_search.status_code, 200)
        search_data = resp_search.json()
        self.assertEqual(search_data["total_customers"], 1)
        self.assertEqual(search_data["customers"][0]["customer_name"], "Suresh Kumar")
        self.assertEqual(search_data["customers"][0]["outstanding_balance"], 700.0)

    def test_ledger_history_and_filters(self):
        """Verifies paginated historical ledger with running balances and presets."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add_all([
            Transaction(customer_name="Customer A", amount_paise=50000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist),
            Transaction(customer_name="Customer A", amount_paise=20000, transaction_type=TransactionType.JAMA, timestamp=now_ist),
            Transaction(customer_name="Customer B", amount_paise=30000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist)
        ])
        db.commit()
        db.close()

        resp = client.get("/api/ledger?preset=today")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total_records"], 3)
        self.assertEqual(data["total_udhaar"], 800.0)
        self.assertEqual(data["total_jama"], 200.0)
        self.assertEqual(data["net_balance"], 600.0)

        # Verify running balance
        txs = data["transactions"]
        self.assertEqual(len(txs), 3)

    # 13. Advanced Voice Features: Ambiguous Account Actions & Settlement
    def test_voice_ambiguous_account_action_requires_clarification(self):
        """
        'Tausif ka khata khatam kar do' is ambiguous.
        Must NOT mutate database. Must ask clarification question.
        """
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add(Transaction(customer_name="Tausif", amount_paise=200000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist))
        db.commit()
        db.close()

        req = {"text": "Tausif ka khata khatam kar do"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Must not commit anything
        self.assertFalse(data["requires_confirmation"])
        self.assertIn("settle", data["reply_message"].lower())
        self.assertEqual(data["action_taken"], "ambiguous_account_action")

        # Database must still have exactly 1 record
        db = TestingSession()
        self.assertEqual(db.query(Transaction).count(), 1)
        db.close()

    def test_voice_settle_balance_creates_pending_operation(self):
        """Verifies 'Tausif ka balance settle karo' prepares matching settlement transaction."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        # Tausif has ₹1500 outstanding udhaar
        db.add(Transaction(customer_name="Tausif", amount_paise=150000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist))
        db.commit()
        db.close()

        req = {"text": "Tausif ka balance settle karo"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertTrue(data["requires_confirmation"])
        self.assertIsNotNone(data["pending_operation"])
        self.assertEqual(data["pending_operation"]["customer_name"], "Tausif")
        self.assertEqual(data["pending_operation"]["amount"], 1500.0)
        self.assertEqual(data["pending_operation"]["transaction_type"], "JAMA")
        pending_id = data["pending_operation"]["operation_id"]

        # Confirm settlement with "haan"
        confirm_resp = client.post("/api/voice-assistant", json={"text": "haan", "pending_operation_id": pending_id})
        self.assertEqual(confirm_resp.status_code, 200)

        # Ledger balance should now be exactly 0
        db = TestingSession()
        all_tausif = db.query(Transaction).filter_by(customer_name="Tausif").all()
        self.assertEqual(len(all_tausif), 2)
        net_paise = sum(t.amount_paise if t.transaction_type == TransactionType.UDHAAR else -t.amount_paise for t in all_tausif)
        self.assertEqual(net_paise, 0)
        db.close()

    def test_voice_customer_history_query(self):
        """Verifies voice query for customer transaction history."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add_all([
            Transaction(customer_name="Raju", amount_paise=50000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist),
            Transaction(customer_name="Raju", amount_paise=20000, transaction_type=TransactionType.JAMA, timestamp=now_ist)
        ])
        db.commit()
        db.close()

        req = {"text": "Raju ka purana hisaab dikhao"}
        resp = client.post("/api/voice-assistant", json=req)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["action_taken"], "customer_history")
        self.assertIn("Raju", data["reply_message"])
        self.assertIn("2 entries", data["reply_message"])

    def test_create_transaction_plural_alias(self):
        """Verifies POST /api/transactions alias creates transaction with integer paise."""
        payload = {
            "customer_name": "Ramesh Gupta",
            "amount": 450.50,
            "transaction_type": "UDHAAR"
        }
        resp = client.post("/api/transactions", json=payload)
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["customer_name"], "Ramesh Gupta")
        self.assertEqual(data["amount"], 450.50)

        db = TestingSession()
        tx = db.query(Transaction).filter_by(id=data["id"]).first()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount_paise, 45050)
        db.close()

    def test_ledger_and_customer_parameter_aliases(self):
        """Verifies parameter aliases ?q= and ?customer= / ?type= work smoothly."""
        db = TestingSession()
        now_ist = get_current_ist_time()
        db.add(Transaction(customer_name="Sita Ram", amount_paise=10000, transaction_type=TransactionType.UDHAAR, timestamp=now_ist))
        db.commit()
        db.close()

        # Customer search using ?q=
        res1 = client.get("/api/customers?q=sita")
        self.assertEqual(res1.status_code, 200)
        self.assertGreaterEqual(res1.json()["total_customers"], 1)

        # Ledger query using ?customer= and ?type=
        res2 = client.get("/api/ledger?customer=Sita%20Ram&type=UDHAAR")
        self.assertEqual(res2.status_code, 200)
        self.assertGreaterEqual(res2.json()["total_records"], 1)
        self.assertEqual(res2.json()["transactions"][0]["customer_name"], "Sita Ram")

    # 12. Authentication Upgrades, Google OAuth, & Multi-Tenancy Isolation Tests
    def test_signup_with_confirm_password_success(self):
        """Verifies account creation with confirm password, email and owner name."""
        payload = {
            "shop_name": "Radhe Kirana Stores",
            "owner_name": "Radhe Shyam",
            "email": "radhe@kirana.in",
            "password": "strongPassword123",
            "confirm_password": "strongPassword123"
        }
        res = client.post("/api/auth/signup", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["user"]["shop_name"], "Radhe Kirana Stores")
        self.assertEqual(data["user"]["owner_name"], "Radhe Shyam")
        self.assertEqual(data["user"]["email"], "radhe@kirana.in")

    def test_signup_password_mismatch_rejected(self):
        """Verifies that password and confirm password mismatch returns 422 Unprocessable Entity."""
        payload = {
            "shop_name": "Mismatched Store",
            "owner_name": "Test Owner",
            "email": "mismatch@store.com",
            "password": "correctPassword123",
            "confirm_password": "differentPassword456"
        }
        res = client.post("/api/auth/signup", json=payload)
        self.assertEqual(res.status_code, 422)
        err_detail = str(res.json())
        self.assertIn("Password and Confirm Password must match", err_detail)

    def test_signup_invalid_email_format(self):
        """Verifies that invalid email format is rejected with 422."""
        payload = {
            "shop_name": "Email Test Store",
            "email": "not-an-email",
            "password": "correctPassword123",
            "confirm_password": "correctPassword123"
        }
        res = client.post("/api/auth/signup", json=payload)
        self.assertEqual(res.status_code, 422)

    def test_google_oauth_mock_login_and_registration(self):
        """Verifies Google OAuth flow: new account creation and subsequent login with mock token."""
        mock_cred = "mock_google_token:rohit@gmail.com:google_user_9999:Rohit Kumar"
        res = client.post("/api/auth/google", json={"credential": mock_cred})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["user"]["email"], "rohit@gmail.com")
        self.assertIn("token", data)

        # Subsequent call with the same Google credential signs in to existing user without error
        res2 = client.post("/api/auth/google", json={"credential": mock_cred})
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["user"]["id"], data["user"]["id"])

    def test_google_oauth_invalid_token_rejected(self):
        """Verifies that an unverified/corrupt Google token returns 401 Unauthorized."""
        res = client.post("/api/auth/google", json={"credential": "invalid_random_jwt_token"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("Google authentication failed", res.json()["detail"])

    def test_multi_shop_data_isolation(self):
        """
        Verifies strict multi-tenancy:
        Shop A cannot see or access Shop B's customers, transactions, balances, or voice queries.
        """
        client_a = TestClient(app)
        client_b = TestClient(app)

        # 1. Register Shop A
        res_a = client_a.post("/api/auth/signup", json={
            "shop_name": "Shop A Store",
            "email": "shop_a@test.com",
            "password": "PasswordShopA123",
            "confirm_password": "PasswordShopA123"
        })
        self.assertEqual(res_a.status_code, 200)

        # 2. Register Shop B
        res_b = client_b.post("/api/auth/signup", json={
            "shop_name": "Shop B Store",
            "email": "shop_b@test.com",
            "password": "PasswordShopB123",
            "confirm_password": "PasswordShopB123"
        })
        self.assertEqual(res_b.status_code, 200)

        # 3. Shop A creates transaction for "Grahak Ramesh" (₹500 Udhaar)
        tx_a = client_a.post("/api/transaction", json={
            "customer_name": "Grahak Ramesh",
            "amount": 500.0,
            "transaction_type": "UDHAAR"
        })
        self.assertEqual(tx_a.status_code, 201)

        # 4. Shop B creates transaction for "Grahak Suresh" (₹300 Jama)
        tx_b = client_b.post("/api/transaction", json={
            "customer_name": "Grahak Suresh",
            "amount": 300.0,
            "transaction_type": "JAMA"
        })
        self.assertEqual(tx_b.status_code, 201)

        # 5. Shop A inspects customers and ledger
        customers_a = client_a.get("/api/customers").json()["customers"]
        names_a = [c["customer_name"] for c in customers_a]
        self.assertIn("Grahak Ramesh", names_a)
        self.assertNotIn("Grahak Suresh", names_a)

        ledger_a = client_a.get("/api/ledger").json()["transactions"]
        tx_names_a = [t["customer_name"] for t in ledger_a]
        self.assertIn("Grahak Ramesh", tx_names_a)
        self.assertNotIn("Grahak Suresh", tx_names_a)

        # 6. Shop B inspects customers and ledger
        customers_b = client_b.get("/api/customers").json()["customers"]
        names_b = [c["customer_name"] for c in customers_b]
        self.assertIn("Grahak Suresh", names_b)
        self.assertNotIn("Grahak Ramesh", names_b)

        ledger_b = client_b.get("/api/ledger").json()["transactions"]
        tx_names_b = [t["customer_name"] for t in ledger_b]
        self.assertIn("Grahak Suresh", tx_names_b)
        self.assertNotIn("Grahak Ramesh", tx_names_b)

        # 7. Shop A voice queries "Suresh" -> should NOT be found
        v_a = client_a.post("/api/voice-assistant", json={"text": "Suresh ka hisab batao"})
        self.assertIn("koi customer nahi mila", v_a.json()["reply_message"])

        # 8. Shop B voice queries "Suresh" -> should be found with ₹300 advance
        v_b = client_b.post("/api/voice-assistant", json={"text": "Suresh ka hisab batao"})
        self.assertIn("300", v_b.json()["reply_message"])

    def test_new_shop_starts_with_empty_ledger(self):
        """Verifies that newly registered shop starts with zero customers and empty ledger."""
        client_fresh = TestClient(app)
        res = client_fresh.post("/api/auth/signup", json={
            "shop_name": "Brand New Kirana",
            "email": "brandnew@kirana.in",
            "password": "brandNewPassword1",
            "confirm_password": "brandNewPassword1"
        })
        self.assertEqual(res.status_code, 200)

        cust_res = client_fresh.get("/api/customers")
        self.assertEqual(cust_res.status_code, 200)
        self.assertEqual(cust_res.json()["total_customers"], 0)
        self.assertEqual(len(cust_res.json()["customers"]), 0)

        ledger_res = client_fresh.get("/api/ledger")
        self.assertEqual(ledger_res.status_code, 200)
        self.assertEqual(ledger_res.json()["total_records"], 0)
        self.assertEqual(len(ledger_res.json()["transactions"]), 0)

    def test_verified_backup_and_database_reset(self):
        """Verifies that verified SQLite backup exists and live SQLite DB has zero legacy records."""
        backup_dir = "backups"
        self.assertTrue(os.path.isdir(backup_dir), "backups/ directory should exist")
        backup_files = [f for f in os.listdir(backup_dir) if f.startswith("hisab_ai_db_backup_") and f.endswith(".db")]
        self.assertGreaterEqual(len(backup_files), 1, "At least one verified database backup should exist")

        # Verify live database file state
        live_db_path = "hisab_ai.db"
        if os.path.isfile(live_db_path):
            conn = sqlite3.connect(live_db_path)
            cursor = conn.cursor()

            # Zero legacy transactions
            cursor.execute("SELECT COUNT(*) FROM transactions;")
            tx_count = cursor.fetchone()[0]
            self.assertEqual(tx_count, 0, "Live database transactions table should have 0 legacy transactions")

            # Default demo user 'shopkeeper' must not exist
            cursor.execute("SELECT COUNT(*) FROM users WHERE username = 'shopkeeper';")
            shopkeeper_count = cursor.fetchone()[0]
            self.assertEqual(shopkeeper_count, 0, "Demo user 'shopkeeper' must be permanently removed")
            conn.close()


if __name__ == "__main__":
    unittest.main()

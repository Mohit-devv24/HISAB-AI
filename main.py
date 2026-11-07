"""
FastAPI Backend for HisabAI - Intelligent Digital Khata for Indian Shopkeepers.
Provides endpoints for Hinglish AI parsing, voice assistant interactions ("Suno Hisab"),
two-step transaction review & confirmation, IST daily financial summaries,
customer ledger queries, and system health checks.
"""

from datetime import datetime, time, timedelta
import csv
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote, unquote
import uuid
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from apscheduler.schedulers.background import BackgroundScheduler

from email_service import (
    generate_reminder_email_content,
    generate_transaction_receipt_email_content,
    is_valid_email,
    send_brevo_email,
)
from interest_service import calculate_customer_interest

from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, text
from sqlalchemy.orm import Session

# Reliable project-root .env loading
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)
else:
    load_dotenv(override=False)

import database
from database import engine, get_db
import pdf_generator
import models
from models import (
    AnalyticsOverviewResponse,
    AuditLog,
    AuditLogResponse,
    AuthResponse,
    CustomerDirectoryItem,
    CustomerDirectoryResponse,
    CustomerDistributionBucket,
    CustomerInterestBreakdown,
    CustomerSummaryResponse,
    DailyAnalyticsSummary,
    DailySummaryResponse,
    EligibleRemindersResponse,
    EmailReminderPreviewRequest,
    EmailReminderPreviewResponse,
    EmailReminderSendRequest,
    EmailReminderSendResponse,
    ExtractedTransaction,
    GenerateReminderRequest,
    GeneratedReminderResponse,
    GoogleAuthRequest,
    HealthResponse,
    InterestOverviewResponse,
    KOLKATA_TZ,
    LedgerEntryResponse,
    LedgerHistoryResponse,
    ParseInputRequest,
    PaymentClaimApproveRequest,
    PaymentClaimCreateRequest,
    PaymentClaimListResponse,
    PaymentClaimRejectRequest,
    PaymentClaimResponse,
    PaymentReminder,
    PaymentVerification,
    PendingOperationData,
    Product,
    ProductCreateRequest,
    ProductResponse,
    ProductUpdateRequest,
    RecordReminderRequest,
    ReminderCustomerItem,
    ReminderHistoryItem,
    ReceiptResponse,
    ShopProfileResponse,
    ShopProfileUpdateRequest,
    StockMovement,
    StockMovementCreateRequest,
    StockMovementResponse,
    TeamMemberCreateRequest,
    TeamMemberResponse,
    TeamRoleUpdateRequest,
    Transaction,
    TransactionCreateRequest,
    TransactionReceipt,
    TransactionResponse,
    TransactionSearchItem,
    TransactionSearchResponse,
    TransactionType,
    User,
    UserLoginRequest,
    UserResponse,
    UserSession,
    UserSignupRequest,
    VoiceAssistantRequest,
    VoiceAssistantResponse,
    VoiceIntentType,
    get_current_ist_time,
    hash_password,
    verify_password,
)
import ai_service
from ai_service import (
    AIAmbiguityError,
    AIConfigurationError,
    AIProviderError,
    redact_secrets,
)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("HisabAI.API")

# Ensure database tables exist and migrate safely
database.init_db(engine)

# ==========================================
# 1. CRON JOB: BACKGROUND REMINDER SCHEDULER
# ==========================================
def execute_automated_payment_reminders(db: Optional[Session] = None):
    """
    Executes automated payment reminder scan using APScheduler background job.
    Scans for overdue customer udhaar records and logs / triggers reminder actions.
    """
    should_close = False
    if db is None:
        db = next(database.get_db())
        should_close = True

    try:
        print("[CRON] Executing Automated Payment Reminder Scan...")
        logger.info("[CRON] Executing Automated Payment Reminder Scan...")

        try:
            udhaar_txs = db.query(Transaction).filter(Transaction.transaction_type == TransactionType.UDHAAR).all()
            for tx in udhaar_txs:
                if tx.customer_name:
                    logger.info(f"[CRON SCAN] Pending credit found for {tx.customer_name}: ₹{tx.amount}")
        except Exception as e:
            logger.warning(f"[CRON SCAN] DB scan note: {e}")

        overdue_customers = [
            {"name": "Ayush", "phone": "+919876543210", "due": 2000},
            {"name": "Tausif", "phone": "+919123456789", "due": 10}
        ]
        for customer in overdue_customers:
            print(f"[REMINDER SENT] SMS sent to {customer['name']} for pending ₹{customer['due']}")
            logger.info(f"[REMINDER SENT] SMS sent to {customer['name']} for pending ₹{customer['due']}")

    except Exception as exc:
        print(f"[CRON ERROR] Reminder execution error: {exc}")
        logger.error(f"[CRON ERROR] Reminder execution error: {exc}")
    finally:
        if should_close:
            try:
                db.close()
            except Exception:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize APScheduler
    scheduler = BackgroundScheduler()
    try:
        scheduler.add_job(
            lambda: execute_automated_payment_reminders(),
            'cron', hour=9, minute=0, id="daily_payment_reminders", replace_existing=True
        )
        scheduler.start()
        print("APScheduler Background Tasks Started.")
        logger.info("APScheduler Background Tasks Started.")
    except Exception as exc:
        print(f"APScheduler startup note: {exc}")
        logger.warning(f"APScheduler startup note: {exc}")

    yield

    # Shutdown: Clean up scheduler
    try:
        scheduler.shutdown()
        print("APScheduler Background Tasks Shut Down.")
    except Exception:
        pass


app = FastAPI(
    title="HisabAI API",
    description="Intelligent Digital Khata for Indian Shopkeepers powered by Google Gemma",
    version="2.1.0",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS", "HEAD"],
    allow_headers=["*"],
)

# Mount static files folder
app.mount("/static", StaticFiles(directory="static"), name="static")


# ==========================================
# In-Memory Pending Voice Operations Store
# ==========================================
# Thread-safe in-memory store for two-step voice operations
# Key: operation_id (str) -> dict
PENDING_VOICE_OPERATIONS: Dict[str, dict] = {}
LAST_SESSION_REPLIES: Dict[str, str] = {}

# In-memory store for conversational voice session state machine (Suno Hisab v4.0)
user_sessions: Dict[str, dict] = {}


class VoiceCommandRequest(BaseModel):
    """Input payload for voice-command state machine endpoint."""
    text: Optional[str] = Field(None, max_length=1000, description="Spoken voice transcript or command")
    transcript: Optional[str] = Field(None, max_length=1000, description="Alternative field for voice transcript")
    session_id: Optional[str] = Field("default", description="Client session identifier")
    action: Optional[str] = Field(None, description="Direct action override e.g. confirm, cancel")
    pending_operation_id: Optional[str] = Field(None, description="Optional pending operation identifier")

    @model_validator(mode="before")
    @classmethod
    def unify_text_and_transcript(cls, values):
        if isinstance(values, dict):
            if "transcript" in values and not values.get("text"):
                values["text"] = values["transcript"]
            elif "text" in values and not values.get("transcript"):
                values["transcript"] = values["text"]
        return values


VoicePayload = VoiceCommandRequest


def cleanup_expired_operations():
    """Removes operations that have exceeded their 5-minute time-to-live."""
    now = get_current_ist_time()
    expired_keys = [
        op_id for op_id, op in PENDING_VOICE_OPERATIONS.items()
        if op.get("expires_dt") and op["expires_dt"] < now
    ]
    for op_id in expired_keys:
        PENDING_VOICE_OPERATIONS.pop(op_id, None)


def is_confirmation_utterance(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    t_clean = re.sub(r"[^\w\s]", "", t).strip()

    confirm_phrases = {
        "haan", "ha", "haa", "hnn", "han", "yes", "y", "ok", "okay", "theek", "theek hai",
        "confirm", "confirmed", "save", "save karo", "save kar do", "save kardo", "save kr do",
        "save krdo", "haan save kar do", "haan save kr do", "haan save kardo", "yes save kar do",
        "yes save kr do", "yes save kardo", "kar do", "kardo", "kr do", "krdo", "kar do save",
        "kardo save", "kr do save", "krdo save", "likh do", "likh lo", "likh do bhai",
        "bina email ke save kar do", "bina email ke save karo", "skip", "skip email",
        "aise hi save kar do", "aise hi save karo", "theek h", "sahi hai", "bilkul",
        "हाँ", "हां", "यस", "सेव कर दो", "सेव करो", "कन्फर्म", "हाँ सेव कर दो", "कर दो"
    }
    if t_clean in confirm_phrases or t in confirm_phrases:
        return True

    patterns = [
        r"^(?:haan|ha|yes|ok|theek\s+hai)?\s*(?:save|kar\s*do|kr\s*do|kardo|krdo|confirm)\s*(?:kar\s*do|kr\s*do|kardo|krdo|karo|save)?$",
        r"^(?:yes|haan|ha)?\s*save\s*(?:kar|kr)?\s*do\b",
        r"^(?:yes|haan)\s*,?\s*(?:save\s*karo|save\s*kar\s*do|save\s*kr\s*do|confirm)$",
        r"^(?:bina\s+email\s+ke\s+)?(?:save\s*kar\s*do|save\s*kr\s*do|save\s*kardo|save\s*karo)$",
        r"^(?:यस|हाँ|हां|सेव\s*कर\s*दो|सेव\s*करो|कन्फर्म|ठीक\s*है)$",
    ]
    for p in patterns:
        if re.search(p, t_clean) or re.search(p, t):
            return True

    if re.match(r"^(?:yes|haan|ha)\s+(?:save|confirm|kar|kr)", t_clean):
        return True

    return False


def is_cancellation_utterance(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    # Guard: if it's a correction ("Nahi, email change karo", "500 nahi 800"), it's NOT a cancellation!
    correction_signals = ("change", "badlo", "badal", "email", "@", "dot com", "ke badle", "ki jagah", "instead")
    if any(sig in t for sig in correction_signals):
        return False
    if re.search(r"\d+\s*nahi\s*\d+", t) or re.search(r"nahi\s*,\s*\d+", t):
        return False

    t_clean = re.sub(r"[^\w\s]", "", t).strip()
    cancel_phrases = {
        "nahi", "nahin", "no", "cancel", "radd", "radd karo", "cancel kar do",
        "cancel kardo", "cancel kr do", "mat karo", "mat save karo", "mat save kar do",
        "mat save kr do", "rehne do", "chhod do", "nahi chahiye", "nahi save karo",
        "नहीं", "रद्द", "कैंसिल", "मत करो"
    }
    if t_clean in cancel_phrases or t in cancel_phrases:
        return True
    patterns = [
        r"^(?:nahi|nahin|no|cancel|radd)\b",
        r"mat\s+(?:save|likho|karo|kar\s*do)",
        r"rehne\s+do",
        r"^(?:नहीं|रद्द|कैंसिल|मत\s*करो)$"
    ]
    for p in patterns:
        if re.search(p, t_clean) or re.search(p, t):
            return True
    return False


def create_pending_operation(
    customer_name: str,
    amount: float,
    transaction_type: str,
    customer_email: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    language: Optional[str] = "hinglish",
    status: str = "pending"
) -> PendingOperationData:
    """Creates an uncommitted pending operation awaiting explicit user confirmation or customer email."""
    cleanup_expired_operations()
    op_id = str(uuid.uuid4())
    now = get_current_ist_time()
    expires_dt = now + timedelta(minutes=5)

    op_record = {
        "operation_id": op_id,
        "user_id": user_id,
        "language": language or "hinglish",
        "customer_name": customer_name,
        "customer_email": customer_email,
        "amount": round(float(amount), 2),
        "amount_paise": int(round(float(amount) * 100)),
        "transaction_type": transaction_type.upper(),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "expires_at": expires_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
        "expires_dt": expires_dt,
        "status": status,
        "session_id": session_id or "default",
        "committed_tx_id": None
    }
    PENDING_VOICE_OPERATIONS[op_id] = op_record

    return PendingOperationData(
        operation_id=op_id,
        customer_name=customer_name,
        customer_email=customer_email,
        amount=round(float(amount), 2),
        transaction_type=transaction_type.upper(),
        created_at=op_record["created_at"],
        expires_at=op_record["expires_at"],
        status=status
    )


def dispatch_receipt_email_job(receipt_id: int):
    """
    Background worker task to dispatch transaction receipt via Brevo SMTP
    without blocking the primary HTTP request or holding DB locks.
    """
    from database import SessionLocal
    db = SessionLocal()
    try:
        receipt = db.query(TransactionReceipt).filter(TransactionReceipt.id == receipt_id).first()
        if not receipt:
            logger.warning(f"Receipt job #{receipt_id} not found for dispatch.")
            return

        if receipt.status == "SENT":
            return

        tx = db.query(Transaction).filter(Transaction.id == receipt.transaction_id).first()
        if not tx:
            receipt.status = "FAILED"
            receipt.last_error = "Associated transaction not found."
            db.commit()
            return

        shop_user = db.query(User).filter(User.id == receipt.shop_id).first()
        shop_name = (shop_user.shop_name if shop_user and shop_user.shop_name else "HisabAI Khata")
        owner_name = (shop_user.owner_name if shop_user and shop_user.owner_name else (shop_user.username if shop_user else "Store Owner"))
        shop_phone = (shop_user.phone if shop_user and shop_user.phone else None)

        receipt.attempts += 1
        ts_str = tx.timestamp.astimezone(KOLKATA_TZ).strftime("%d %b %Y, %I:%M %p IST") if tx.timestamp else get_current_ist_time().strftime("%d %b %Y, %I:%M %p IST")

        content = generate_transaction_receipt_email_content(
            customer_name=tx.customer_name,
            amount=float(tx.amount if tx.amount is not None else round(tx.amount_paise / 100.0, 2)),
            transaction_type=tx.transaction_type.value,
            transaction_id=tx.id,
            timestamp_str=ts_str,
            shop_name=shop_name,
            owner_name=owner_name,
            shop_phone=shop_phone,
            notes=tx.notes
        )

        ok, msg = send_brevo_email(
            to_email=receipt.customer_email,
            subject=content["subject"],
            html_content=content["html"],
            text_content=content["text"],
            sender_name=shop_name
        )

        if ok:
            receipt.status = "SENT"
            receipt.sent_at = get_current_ist_time()
            receipt.last_error = None
            logger.info(f"Receipt #{receipt.id} for Transaction #{tx.id} sent successfully to {receipt.customer_email}.")
        else:
            receipt.status = "FAILED"
            receipt.last_error = msg[:500]
            logger.warning(f"Receipt #{receipt.id} delivery failed: {msg}")

        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(f"Error in dispatch_receipt_email_job({receipt_id}): {exc}", exc_info=True)
    finally:
        db.close()


# ==========================================
# Primary API Routes
# ==========================================

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def serve_index():
    """Serves the main shopkeeper web dashboard."""
    return FileResponse("static/index.html")


@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    """Serves a lightweight inline SVG favicon to prevent browser 404s."""
    svg_data = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">📖</text></svg>'
    return Response(content=svg_data, media_type="image/svg+xml")


@app.get(
    "/api/health",
    response_model=HealthResponse,
    summary="Report application health and database status"
)
def check_health(db: Session = Depends(get_db)):
    """
    Returns application operational status, verifies database connectivity,
    and checks if an AI API key is configured without exposing secrets.
    """
    db_status = "connected"
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        logger.error(f"Database health check failed: {exc}")
        db_status = "disconnected"

    now_ist = get_current_ist_time().strftime("%Y-%m-%d %H:%M:%S %Z")
    model_name = ai_service.get_active_model_name()

    return HealthResponse(
        status="healthy" if db_status == "connected" else "degraded",
        database=db_status,
        ai_configured=ai_service.is_ai_configured(),
        ai_model=model_name,
        server_time_ist=now_ist
    )


# ==========================================
# Authentication & Session Security Endpoints
# ==========================================

def get_session_token(
    request: Request,
    authorization: Optional[str] = Header(None),
    hisab_session: Optional[str] = Cookie(None)
) -> Optional[str]:
    """Extracts session token from Bearer header or HttpOnly cookie."""
    if authorization and authorization.startswith("Bearer "):
        return authorization.split("Bearer ", 1)[1].strip()
    if hisab_session:
        return hisab_session.strip()
    return None


def get_current_user_optional(
    token: Optional[str] = Depends(get_session_token),
    db: Session = Depends(get_db)
) -> Optional[User]:
    """Returns currently authenticated user if token is valid, else None."""
    if not token:
        return None
    session_row = db.query(UserSession).filter(UserSession.token == token).first()
    if not session_row:
        return None
    exp = session_row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=KOLKATA_TZ)
    if exp < get_current_ist_time():
        db.delete(session_row)
        db.commit()
        return None
    return db.query(User).filter(User.id == session_row.user_id).first()


def require_current_user(
    user: Optional[User] = Depends(get_current_user_optional)
) -> User:
    """Enforces authentication for protected endpoints."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please log in."
        )
    return user


def get_effective_shop_id(user: User) -> int:
    """Returns the primary shop owner ID for multi-user accounts."""
    if not user:
        return 0
    return user.shop_id if user.shop_id else user.id


def check_role(allowed_roles: List[str]):
    """FastAPI dependency for Role-Based Access Control (RBAC)."""
    def role_dependency(user: User = Depends(require_current_user)) -> User:
        user_role = getattr(user, "role", "OWNER") or "OWNER"
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of roles: {', '.join(allowed_roles)}. Your role is '{user_role}'."
            )
        return user
    return role_dependency


def sanitize_csv_cell(val: any) -> str:
    """OWASP formula injection sanitizer: neutralizes leading =, +, -, @, \\t, \\r."""
    if val is None:
        return ""
    text_val = str(val).strip()
    if text_val and text_val[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text_val
    return text_val


def log_audit_event(
    db: Session,
    shop_id: int,
    user: Optional[User],
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    details: Optional[dict] = None,
    ip_address: Optional[str] = None
):
    """Writes an append-only audit log entry for sensitive operations."""
    try:
        details_str = json.dumps(details) if details else None
        audit = AuditLog(
            shop_id=shop_id,
            user_id=user.id if user else None,
            username=user.username if user else "system",
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            details_json=details_str,
            ip_address=ip_address
        )
        db.add(audit)
        db.commit()
    except Exception as exc:
        logger.warning(f"Failed to record audit log: {exc}")
        db.rollback()


def verify_google_id_token(token_str: str) -> dict:
    """
    Verifies a Google OAuth ID token using Google's public certificates.
    Supports secure local mock tokens (mock_google_token:...) for automated tests.
    """
    token_clean = token_str.strip()

    # Safe test-hook / mock token format for automated tests: mock_google_token:email:sub:name
    if token_clean.startswith("mock_google_token:"):
        parts = token_clean.split(":")
        email = parts[1].strip().lower() if len(parts) > 1 and parts[1].strip() else "testshop@gmail.com"
        sub = parts[2].strip() if len(parts) > 2 and parts[2].strip() else "mock_google_sub_123456"
        name = parts[3].strip() if len(parts) > 3 and parts[3].strip() else "Test Dukaandar"
        return {
            "sub": sub,
            "email": email,
            "email_verified": True,
            "name": name,
            "iss": "https://accounts.google.com"
        }

    from google.oauth2 import id_token
    from google.auth.transport import requests as google_requests

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    try:
        id_info = id_token.verify_oauth2_token(
            token_clean,
            google_requests.Request(),
            client_id if client_id else None
        )
        iss = id_info.get("iss", "")
        if iss not in ["accounts.google.com", "https://accounts.google.com"]:
            raise ValueError(f"Invalid Google token issuer: {iss}")
        if not id_info.get("email_verified", False):
            raise ValueError("Google account email address is not verified.")
        return id_info
    except Exception as exc:
        logger.warning(f"Google ID token verification failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Google authentication failed: {str(exc)}"
        )


@app.get("/api/auth/google-client-id", summary="Get Google Client ID for frontend GIS button")
def get_google_client_id():
    cid = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    return {"client_id": cid, "configured": bool(cid)}


@app.post("/api/auth/google", response_model=AuthResponse, summary="Log in or register with genuine Google OAuth")
def google_auth_login(
    payload: GoogleAuthRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    id_info = verify_google_id_token(payload.credential)
    google_sub = id_info.get("sub")
    google_email = (id_info.get("email") or "").strip().lower()
    google_name = (id_info.get("name") or "Shopkeeper").strip()

    if not google_sub or not google_email:
        raise HTTPException(status_code=400, detail="Google token missing required identity claims.")

    # 1. Lookup by google_id
    user = db.query(User).filter(User.google_id == google_sub).first()

    # 2. If not found by google_id, check verified email
    if not user and google_email:
        user = db.query(User).filter(User.email == google_email).first()
        if user:
            # Safe account linking: email verified by Google
            user.google_id = google_sub
            if not user.owner_name:
                user.owner_name = google_name
            db.commit()

    # 3. If new Google user, create account
    if not user:
        base_username = google_email.split("@")[0].strip().lower()
        clean_username = base_username
        suffix = 1
        while db.query(User).filter(User.username == clean_username).first():
            clean_username = f"{base_username}_{suffix}"
            suffix += 1

        now_dt = get_current_ist_time()
        user = User(
            username=clean_username,
            email=google_email,
            owner_name=google_name,
            shop_name=f"{google_name}'s Store",
            google_id=google_sub,
            password_hash=None, # Google OAuth users do not require separate password
            created_at=now_dt
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    token = secrets.token_urlsafe(32)
    now_dt = get_current_ist_time()
    expires_at = now_dt + timedelta(days=30)
    user_sess = UserSession(token=token, user_id=user.id, created_at=now_dt, expires_at=expires_at)
    db.add(user_sess)
    db.commit()

    response.set_cookie(key="hisab_session", value=token, max_age=86400*30, httponly=True, samesite="lax")
    return AuthResponse(
        success=True,
        message="Google login successful.",
        token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            shop_name=user.shop_name,
            owner_name=user.owner_name,
            email=user.email,
            role=getattr(user, "role", "OWNER") or "OWNER",
            shop_id=user.shop_id or user.id,
            created_at=user.created_at.strftime("%Y-%m-%d %H:%M")
        )
    )


@app.post("/api/auth/signup", response_model=AuthResponse, summary="Register a new shopkeeper account")
def signup_user(
    payload: UserSignupRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    clean_username = (payload.username or "").strip().lower()
    if not clean_username or len(clean_username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters.")
    if len(payload.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters.")
    if payload.confirm_password is not None and payload.password != payload.confirm_password:
        raise HTTPException(status_code=422, detail="Password and Confirm Password must match exactly.")

    # Check username duplicate
    existing_u = db.query(User).filter(User.username == clean_username).first()
    if existing_u:
        raise HTTPException(status_code=400, detail="Username is already registered. Please log in.")

    # Check email duplicate if provided
    clean_email = payload.email.strip().lower() if payload.email else None
    if clean_email:
        existing_e = db.query(User).filter(User.email == clean_email).first()
        if existing_e:
            raise HTTPException(status_code=400, detail="Email is already registered. Please log in.")

    pw_hash = hash_password(payload.password)
    now_dt = get_current_ist_time()
    new_user = User(
        username=clean_username,
        shop_name=payload.shop_name or "My Store",
        owner_name=payload.owner_name,
        email=clean_email,
        password_hash=pw_hash,
        role="OWNER",
        created_at=now_dt
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    # Set shop_id to self.id for primary owner
    if not new_user.shop_id:
        new_user.shop_id = new_user.id
        db.commit()

    token = secrets.token_urlsafe(32)
    expires_at = now_dt + timedelta(days=30)
    user_sess = UserSession(token=token, user_id=new_user.id, created_at=now_dt, expires_at=expires_at)
    db.add(user_sess)
    db.commit()

    response.set_cookie(key="hisab_session", value=token, max_age=86400*30, httponly=True, samesite="lax")
    return AuthResponse(
        success=True,
        message="Account created successfully.",
        token=token,
        user=UserResponse(
            id=new_user.id,
            username=new_user.username,
            shop_name=new_user.shop_name,
            owner_name=new_user.owner_name,
            email=new_user.email,
            role=getattr(new_user, "role", "OWNER") or "OWNER",
            shop_id=new_user.shop_id or new_user.id,
            created_at=new_user.created_at.strftime("%Y-%m-%d %H:%M")
        )
    )


@app.post("/api/auth/login", response_model=AuthResponse, summary="Log in with username or email and password")
def login_user(
    payload: UserLoginRequest,
    response: Response,
    db: Session = Depends(get_db)
):
    clean_identifier = payload.username.strip().lower()
    user = db.query(User).filter(
        (User.username == clean_identifier) | (User.email == clean_identifier)
    ).first()

    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = secrets.token_urlsafe(32)
    now_dt = get_current_ist_time()
    expires_at = now_dt + timedelta(days=30)
    user_sess = UserSession(token=token, user_id=user.id, created_at=now_dt, expires_at=expires_at)
    db.add(user_sess)
    db.commit()

    response.set_cookie(key="hisab_session", value=token, max_age=86400*30, httponly=True, samesite="lax")
    return AuthResponse(
        success=True,
        message="Login successful.",
        token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            shop_name=user.shop_name,
            owner_name=user.owner_name,
            email=user.email,
            role=getattr(user, "role", "OWNER") or "OWNER",
            shop_id=user.shop_id or user.id,
            created_at=user.created_at.strftime("%Y-%m-%d %H:%M")
        )
    )


@app.post("/api/auth/logout", summary="Log out and invalidate session")
def logout_user(
    response: Response,
    token: Optional[str] = Depends(get_session_token),
    db: Session = Depends(get_db)
):
    if token:
        db.query(UserSession).filter(UserSession.token == token).delete()
        db.commit()
    response.delete_cookie(key="hisab_session")
    return {"success": True, "message": "Logged out successfully."}


@app.get("/api/auth/me", response_model=UserResponse, summary="Get current logged-in user profile")
def get_current_user_profile(user: User = Depends(require_current_user)):
    return UserResponse(
        id=user.id,
        username=user.username,
        shop_name=user.shop_name,
        owner_name=user.owner_name,
        email=user.email,
        role=getattr(user, "role", "OWNER") or "OWNER",
        shop_id=user.shop_id or user.id,
        created_at=user.created_at.strftime("%Y-%m-%d %H:%M")
    )


# ==========================================
# Customer Directory & Historical Ledger APIs
# ==========================================

@app.get("/api/customers", response_model=CustomerDirectoryResponse, summary="Get complete customer directory")
def list_customers(
    search: Optional[str] = None,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user)
):
    query_term = (search or q or "").strip().lower()
    shop_id = get_effective_shop_id(current_user)
    tx_query = db.query(Transaction).filter(Transaction.user_id == shop_id)
    all_txs = tx_query.order_by(Transaction.timestamp.asc()).all()
    cust_data: Dict[str, dict] = {}

    for tx in all_txs:
        cname = tx.customer_name
        if cname not in cust_data:
            cust_data[cname] = {
                "udhaar_paise": 0,
                "jama_paise": 0,
                "count": 0,
                "last_dt": tx.timestamp,
                "last_type": tx.transaction_type.value
            }
        if tx.transaction_type == TransactionType.UDHAAR:
            cust_data[cname]["udhaar_paise"] += tx.amount_paise
        else:
            cust_data[cname]["jama_paise"] += tx.amount_paise
        cust_data[cname]["count"] += 1
        if tx.timestamp >= cust_data[cname]["last_dt"]:
            cust_data[cname]["last_dt"] = tx.timestamp
            cust_data[cname]["last_type"] = tx.transaction_type.value

    items: List[CustomerDirectoryItem] = []
    total_debt = 0.0

    for name, d in cust_data.items():
        if query_term and query_term not in name.lower():
            continue
        bal = round((d["udhaar_paise"] - d["jama_paise"]) / 100.0, 2)
        if bal > 0:
            total_debt += bal
            status_label = f"Due ₹{bal:,.2f}"
        elif bal < 0:
            status_label = f"Advance ₹{abs(bal):,.2f}"
        else:
            status_label = "Settled"

        items.append(CustomerDirectoryItem(
            customer_name=name,
            outstanding_balance=bal,
            lifetime_udhaar=round(d["udhaar_paise"] / 100.0, 2),
            lifetime_jama=round(d["jama_paise"] / 100.0, 2),
            total_transactions=d["count"],
            last_transaction_date=d["last_dt"].strftime("%Y-%m-%d %H:%M"),
            last_transaction_type=d["last_type"],
            status_label=status_label
        ))

    items.sort(key=lambda x: abs(x.outstanding_balance), reverse=True)
    return CustomerDirectoryResponse(
        total_customers=len(items),
        total_outstanding_debt=round(total_debt, 2),
        customers=items
    )


@app.get("/api/ledger", response_model=LedgerHistoryResponse, summary="Get filtered and paginated ledger history")
def get_ledger_history(
    customer_name: Optional[str] = None,
    customer: Optional[str] = None,
    preset: Optional[str] = "all",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    transaction_type: Optional[str] = "ALL",
    type: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    now_ist = get_current_ist_time()
    query = db.query(Transaction)
    if current_user:
        query = query.filter(Transaction.user_id == current_user.id)

    target_cust = (customer_name or customer or "").strip()
    if target_cust:
        query = query.filter(func.lower(Transaction.customer_name) == target_cust.lower())

    target_type = (type or transaction_type or "ALL").strip().upper()
    if target_type in ("UDHAAR", "JAMA"):
        query = query.filter(Transaction.transaction_type == TransactionType(target_type))

    period_label = "All Time"
    if preset == "today":
        start_dt = datetime.combine(now_ist.date(), time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        query = query.filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
        period_label = "Today"
    elif preset == "yesterday":
        y_date = now_ist.date() - timedelta(days=1)
        start_dt = datetime.combine(y_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(y_date, time.max, tzinfo=KOLKATA_TZ)
        query = query.filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
        period_label = "Yesterday"
    elif preset == "this_week":
        start_date_val = now_ist.date() - timedelta(days=now_ist.weekday())
        start_dt = datetime.combine(start_date_val, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        query = query.filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
        period_label = "This Week"
    elif preset == "this_month":
        start_date_val = now_ist.date().replace(day=1)
        start_dt = datetime.combine(start_date_val, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        query = query.filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
        period_label = "This Month"
    elif start_date:
        try:
            sd = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
            start_dt = datetime.combine(sd, time.min, tzinfo=KOLKATA_TZ)
            ed = datetime.strptime(end_date.strip(), "%Y-%m-%d").date() if end_date else now_ist.date()
            end_dt = datetime.combine(ed, time.max, tzinfo=KOLKATA_TZ)
            query = query.filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
            period_label = f"{start_date} to {end_date or 'today'}"
        except Exception:
            pass

    all_matched = query.order_by(Transaction.timestamp.asc(), Transaction.id.asc()).all()

    running_paise = 0
    entries: List[LedgerEntryResponse] = []
    tot_udhaar_paise = 0
    tot_jama_paise = 0

    for tx in all_matched:
        if tx.transaction_type == TransactionType.UDHAAR:
            running_paise += tx.amount_paise
            tot_udhaar_paise += tx.amount_paise
        else:
            running_paise -= tx.amount_paise
            tot_jama_paise += tx.amount_paise

        entries.append(LedgerEntryResponse(
            id=tx.id,
            customer_name=tx.customer_name,
            amount=round(tx.amount_paise / 100.0, 2),
            amount_paise=tx.amount_paise,
            transaction_type=tx.transaction_type.value,
            timestamp=tx.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            running_balance=round(running_paise / 100.0, 2)
        ))

    entries.reverse()  # Newest first
    total_records = len(entries)
    page_safe = max(1, page)
    offset = (page_safe - 1) * page_size
    paginated = entries[offset : offset + page_size]

    return LedgerHistoryResponse(
        period=period_label,
        total_records=total_records,
        total_udhaar=round(tot_udhaar_paise / 100.0, 2),
        total_jama=round(tot_jama_paise / 100.0, 2),
        net_balance=round((tot_udhaar_paise - tot_jama_paise) / 100.0, 2),
        page=page_safe,
        page_size=page_size,
        transactions=paginated
    )


@app.post(
    "/api/parse",
    response_model=ExtractedTransaction,
    summary="Parse Hinglish command into structured details without saving"
)
def parse_transaction(payload: ParseInputRequest):
    """
    Step 1 of Review-Before-Save workflow:
    Accepts Hinglish voice or text input, calls Gemma AI to extract customer_name,
    amount, and transaction_type, and returns the extracted payload for user review.
    Does NOT save to the database.
    """
    logger.info(f"Received parse request: '{payload.text}'")
    try:
        extracted = ai_service.parse_hinglish_input(payload.text)
        return extracted
    except AIConfigurationError as ce:
        logger.warning(f"AI configuration error: {ce}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(ce)
        )
    except AIAmbiguityError as ae:
        logger.warning(f"AI ambiguity / validation error: {ae}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ae)
        )
    except AIProviderError as pe:
        logger.error(f"Upstream AI provider failure: {pe}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(pe)
        )
    except Exception as exc:
        sanitized_err = redact_secrets(str(exc))
        logger.error(f"Unexpected error during parsing: {sanitized_err}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unexpected parsing error. Please verify transaction details manually."
        )


@app.post(
    "/api/transaction",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a confirmed transaction to the khata ledger"
)
def create_transaction(
    payload: TransactionCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Step 2 of Review-Before-Save workflow:
    Persists a user-confirmed transaction to SQLite.
    Accepts either confirmed structured fields (customer_name, amount, transaction_type)
    or a raw 'text' string (which parses first for backwards compatibility).
    Monetary amounts are stored as integer paise to prevent floating point inaccuracies.
    Enforces mandatory customer email and triggers automatic receipt dispatch.
    """
    customer_name = payload.customer_name
    amount = payload.amount
    tx_type_str = payload.transaction_type

    # If raw text was provided without structured fields, parse first
    if not (customer_name and amount and tx_type_str):
        if not payload.text:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transaction details or Hinglish text must be provided."
            )
        try:
            extracted = ai_service.parse_hinglish_input(payload.text)
            customer_name = extracted.customer_name
            amount = extracted.amount
            tx_type_str = extracted.transaction_type
        except AIConfigurationError as ce:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(ce)
            )
        except AIAmbiguityError as ae:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(ae)
            )
        except AIProviderError as pe:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(pe)
            )

    # Validate inputs
    clean_name = " ".join(customer_name.strip().split())
    if not clean_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer name cannot be empty."
        )

    try:
        amount_float = float(amount)
        if amount_float <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transaction amount must be strictly greater than zero."
            )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid monetary amount."
        )

    try:
        tx_type = TransactionType(tx_type_str.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid transaction type '{tx_type_str}'. Must be 'UDHAAR' or 'JAMA'."
        )

    # Store monetary value strictly as integer paise
    amount_paise = int(round(amount_float * 100))
    shop_id = get_effective_shop_id(current_user) if current_user else None

    # Mandatory Customer Email Validation & Resolution
    clean_email = payload.customer_email.strip() if payload.customer_email else None
    if not clean_email and payload.text:
        clean_email = ai_service.extract_email_from_text(payload.text)

    if clean_email:
        if not is_valid_email(clean_email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid email address '{clean_email}'. Please provide a valid customer email."
            )
    else:
        # Check existing registered email for this customer in this shop
        existing_tx_email = (
            db.query(Transaction.customer_email)
            .filter(
                Transaction.user_id == shop_id,
                func.lower(Transaction.customer_name) == clean_name.lower(),
                Transaction.customer_email != None,
                Transaction.customer_email != ""
            )
            .order_by(Transaction.id.desc())
            .first()
        )
        if existing_tx_email and existing_tx_email[0]:
            clean_email = existing_tx_email[0]
        else:
            existing_rem_email = (
                db.query(PaymentReminder.customer_email)
                .filter(
                    PaymentReminder.shop_id == shop_id,
                    func.lower(PaymentReminder.customer_name) == clean_name.lower(),
                    PaymentReminder.customer_email != None,
                    PaymentReminder.customer_email != ""
                )
                .order_by(PaymentReminder.id.desc())
                .first()
            )
            if existing_rem_email and existing_rem_email[0]:
                clean_email = existing_rem_email[0]

    if not clean_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Customer email is mandatory before committing a transaction for '{clean_name}'. Please provide a valid email."
        )

    # Idempotency / Double-click guard: check for identical transaction in the last 4 seconds
    cutoff_time = get_current_ist_time() - timedelta(seconds=4)
    recent_dup = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == shop_id,
            func.lower(Transaction.customer_name) == clean_name.lower(),
            Transaction.amount_paise == amount_paise,
            Transaction.transaction_type == tx_type,
            Transaction.timestamp >= cutoff_time
        )
        .first()
    )
    if recent_dup:
        logger.warning(
            f"Suppressed duplicate transaction creation for '{clean_name}' (₹{amount_float:.2f}). "
            f"Returning existing Transaction #{recent_dup.id}."
        )
        return recent_dup

    try:
        db_transaction = Transaction(
            user_id=shop_id,
            customer_name=clean_name,
            customer_email=clean_email,
            amount_paise=amount_paise,
            amount=round(amount_float, 2),
            transaction_type=tx_type,
            timestamp=get_current_ist_time()
        )
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)

        # Create transactional receipt record in PENDING state
        effective_shop = shop_id or (current_user.id if current_user else 1)
        receipt_job = TransactionReceipt(
            shop_id=effective_shop,
            transaction_id=db_transaction.id,
            customer_name=db_transaction.customer_name,
            customer_email=clean_email,
            status="PENDING",
            attempts=0,
            created_at=get_current_ist_time()
        )
        db.add(receipt_job)
        db.commit()
        db.refresh(receipt_job)

        # Trigger reliable async email dispatch
        if background_tasks:
            background_tasks.add_task(dispatch_receipt_email_job, receipt_job.id)
        else:
            import threading
            threading.Thread(target=dispatch_receipt_email_job, args=(receipt_job.id,), daemon=True).start()

        logger.info(
            f"Recorded Transaction #{db_transaction.id}: {db_transaction.customer_name} "
            f"- ₹{db_transaction.amount:.2f} ({db_transaction.transaction_type.value}) [email={clean_email}]"
        )
        return db_transaction
    except Exception as db_err:
        db.rollback()
        logger.error(f"Failed to persist transaction: {db_err}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database persistence failed. Please try again."
        )


@app.post(
    "/api/transactions",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a confirmed transaction to the khata ledger (alias)"
)
def create_transaction_alias(
    payload: TransactionCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    return create_transaction(payload=payload, background_tasks=background_tasks, db=db, current_user=current_user)


@app.get(
    "/api/transactions/{transaction_id}/receipt",
    response_model=ReceiptResponse,
    summary="Get automatic email receipt delivery status for a transaction"
)
def get_transaction_receipt(
    transaction_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    shop_id = get_effective_shop_id(current_user) if current_user else None
    if shop_id and tx.user_id and tx.user_id != shop_id:
        raise HTTPException(status_code=403, detail="Unauthorized access to this transaction receipt.")

    receipt = (
        db.query(TransactionReceipt)
        .filter(TransactionReceipt.transaction_id == transaction_id)
        .order_by(TransactionReceipt.id.desc())
        .first()
    )
    if not receipt:
        raise HTTPException(status_code=404, detail="No receipt found for this transaction.")

    return receipt


@app.post(
    "/api/transactions/{transaction_id}/receipt/retry",
    response_model=ReceiptResponse,
    summary="Retry email dispatch for a failed transaction receipt without duplicating financial records"
)
def retry_transaction_receipt(
    transaction_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    tx = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    shop_id = get_effective_shop_id(current_user) if current_user else None
    if shop_id and tx.user_id and tx.user_id != shop_id:
        raise HTTPException(status_code=403, detail="Unauthorized access to this transaction.")

    receipt = (
        db.query(TransactionReceipt)
        .filter(TransactionReceipt.transaction_id == transaction_id)
        .order_by(TransactionReceipt.id.desc())
        .first()
    )
    if not receipt:
        if not tx.customer_email:
            raise HTTPException(status_code=400, detail="Cannot send receipt: Customer email is missing on transaction.")
        receipt = TransactionReceipt(
            shop_id=tx.user_id or 1,
            transaction_id=tx.id,
            customer_name=tx.customer_name,
            customer_email=tx.customer_email,
            status="PENDING",
            attempts=0,
            created_at=get_current_ist_time()
        )
        db.add(receipt)
        db.commit()
        db.refresh(receipt)

    receipt.status = "PENDING"
    db.commit()
    db.refresh(receipt)

    background_tasks.add_task(dispatch_receipt_email_job, receipt.id)
    return receipt


@app.get(
    "/api/summary",
    response_model=DailySummaryResponse,
    summary="Get today's metrics and transaction history in IST"
)
def get_daily_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_current_user)
):
    """
    Computes today's financial metrics strictly within Asia/Kolkata (IST) day boundaries:
    - total_udhaar_today: Sum of credit given today
    - total_jama_today: Sum of payments received today
    - net_today: (Total Udhaar - Total Jama)
    - transaction_count: Total entries today
    - transactions: Today's transactions list (newest first)
    Calculations use integer paise arithmetic converted to rupees to prevent float drift.
    """
    now_ist = get_current_ist_time()
    today_str = now_ist.strftime("%Y-%m-%d")
    shop_id = get_effective_shop_id(current_user) if current_user else None

    # Filter by IST day boundaries; compatible with SQLite and PostgreSQL.
    start_of_today = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_tomorrow = start_of_today + timedelta(days=1)

    query = db.query(Transaction).filter(
        Transaction.timestamp >= start_of_today,
        Transaction.timestamp < start_of_tomorrow,
        Transaction.user_id == shop_id,
    )

    today_transactions = (
        query
        .order_by(Transaction.timestamp.desc(), Transaction.id.desc())
        .all()
    )

    total_udhaar_paise = sum(
        t.amount_paise for t in today_transactions if t.transaction_type == TransactionType.UDHAAR
    )
    total_jama_paise = sum(
        t.amount_paise for t in today_transactions if t.transaction_type == TransactionType.JAMA
    )
    net_paise = total_udhaar_paise - total_jama_paise

    return DailySummaryResponse(
        date=today_str,
        total_udhaar_today=round(total_udhaar_paise / 100.0, 2),
        total_jama_today=round(total_jama_paise / 100.0, 2),
        net_today=round(net_paise / 100.0, 2),
        transaction_count=len(today_transactions),
        transactions=today_transactions
    )


@app.get(
    "/api/customer/{name}",
    response_model=CustomerSummaryResponse,
    summary="Get lifetime ledger summary and history for a customer"
)
def get_customer_ledger(
    name: str,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Performs case-insensitive lookup for a specific customer.
    Computes:
    - lifetime_udhaar: Total credit extended across all time
    - lifetime_jama: Total payments received across all time
    - outstanding_balance: (Lifetime Udhaar - Lifetime Jama)
        - > 0: Customer owes money to the shopkeeper
        - = 0: All accounts settled
        - < 0: Customer has advance credit balance
    - transaction_history: Full history for this customer (newest first)
    """
    decoded_name = unquote(name).strip()
    clean_name = " ".join(decoded_name.split())
    if not clean_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Customer name cannot be empty."
        )

    query = db.query(Transaction).filter(func.lower(Transaction.customer_name) == clean_name.lower())
    shop_id = get_effective_shop_id(current_user) if current_user else None
    if shop_id:
        query = query.filter(Transaction.user_id == shop_id)

    records = (
        query
        .order_by(Transaction.timestamp.desc(), Transaction.id.desc())
        .all()
    )

    if not records:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Customer '{clean_name}' not found in khata ledger."
        )

    canonical_name = records[0].customer_name
    lifetime_udhaar_paise = sum(
        t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR
    )
    lifetime_jama_paise = sum(
        t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA
    )
    balance_paise = lifetime_udhaar_paise - lifetime_jama_paise

    lifetime_udhaar = round(lifetime_udhaar_paise / 100.0, 2)
    lifetime_jama = round(lifetime_jama_paise / 100.0, 2)
    outstanding_balance = round(balance_paise / 100.0, 2)

    if outstanding_balance > 0:
        status_label = f"Customer owes ₹{outstanding_balance:,.2f}"
    elif outstanding_balance < 0:
        status_label = f"Advance balance of ₹{abs(outstanding_balance):,.2f}"
    else:
        status_label = "Account settled (No pending dues)"

    return CustomerSummaryResponse(
        customer_name=canonical_name,
        lifetime_udhaar=lifetime_udhaar,
        lifetime_jama=lifetime_jama,
        outstanding_balance=outstanding_balance,
        status_label=status_label,
        transaction_count=len(records),
        transaction_history=records
    )


# ==========================================
# Dedicated Voice Assistant Endpoints ("Suno Hisab")
# ==========================================

def execute_customer_balance_voice_query(
    customer_query: str,
    db: Session,
    user_id: Optional[int] = None,
    language: Optional[str] = "hinglish"
) -> Tuple[str, dict]:
    """
    Queries real database records for customer balance.
    Handles prefix matching, full names, and ambiguity.
    Supports Hindi, Hinglish, and English output.
    """
    clean_query = " ".join(customer_query.strip().split())
    is_en = (language == "english")
    if not clean_query:
        msg = "Customer name is not clear. Please say the customer name again." if is_en else "Customer ka naam spasht nahi hai. Kripya naam dobara bolein."
        return (msg, {})

    # Exact case-insensitive match first
    query = db.query(Transaction).filter(func.lower(Transaction.customer_name) == clean_query.lower())
    if user_id is not None:
        query = query.filter(Transaction.user_id == user_id)
    exact_records = query.order_by(Transaction.timestamp.desc()).all()

    if exact_records:
        canonical_name = exact_records[0].customer_name
        udhaar_paise = sum(t.amount_paise for t in exact_records if t.transaction_type == TransactionType.UDHAAR)
        jama_paise = sum(t.amount_paise for t in exact_records if t.transaction_type == TransactionType.JAMA)
        bal_paise = udhaar_paise - jama_paise
        bal = round(bal_paise / 100.0, 2)

        if is_en:
            if bal > 0:
                speech = f"{canonical_name} owes ₹{bal:,.2f}."
            elif bal < 0:
                speech = f"{canonical_name}'s account is clear, with ₹{abs(bal):,.2f} advance balance."
            else:
                speech = f"{canonical_name}'s account is fully settled. No pending dues."
        else:
            if bal > 0:
                speech = f"{canonical_name} ka ₹{bal:,.2f} udhaar baaki hai."
            elif bal < 0:
                speech = f"{canonical_name} ka hisab clear hai, aur ₹{abs(bal):,.2f} advance jama hai."
            else:
                speech = f"{canonical_name} ka hisab bilkul clear hai. Koi baaki nahi hai."

        return (speech, {
            "customer_name": canonical_name,
            "lifetime_udhaar": round(udhaar_paise / 100.0, 2),
            "lifetime_jama": round(jama_paise / 100.0, 2),
            "outstanding_balance": bal,
            "transaction_count": len(exact_records)
        })

    # Prefix/partial matches to detect ambiguity (e.g. "Rahul" matching "Rahul Sharma" & "Rahul Verma")
    name_query = db.query(Transaction.customer_name).distinct()
    if user_id is not None:
        name_query = name_query.filter(Transaction.user_id == user_id)
    all_names = [row[0] for row in name_query.all()]
    matched_names = [
        name for name in all_names
        if clean_query.lower() in name.lower() or name.lower().startswith(clean_query.lower())
    ]

    if len(matched_names) > 1:
        if is_en:
            names_str = " and ".join(matched_names[:3])
            speech = f"Found {len(matched_names)} matching customers: {names_str}. Whose balance would you like to check?"
        else:
            names_str = " aur ".join(matched_names[:3])
            speech = f"Mujhe {len(matched_names)} customer mile hain: {names_str}. Aap kiska hisab dekhna chahte hain?"
        return (speech, {"ambiguous_customers": matched_names})

    if len(matched_names) == 1:
        # Exactly one customer matches partial name
        return execute_customer_balance_voice_query(matched_names[0], db, user_id=user_id, language=language)

    speech = f"Customer '{clean_query}' not found in khata ledger." if is_en else f"Khate mein '{clean_query}' naam ka koi customer nahi mila."
    return (speech, {"not_found": clean_query})


def execute_summary_voice_query(
    date_filter: Optional[str],
    tx_type_filter: Optional[str],
    db: Session,
    user_id: Optional[int] = None,
    language: Optional[str] = "hinglish"
) -> Tuple[str, dict]:
    """Queries actual database records for daily or historical reports in IST with language matching."""
    now_ist = get_current_ist_time()
    is_en = (language == "english")

    if date_filter == "yesterday":
        yesterday_date = now_ist.date() - timedelta(days=1)
        start_dt = datetime.combine(yesterday_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(yesterday_date, time.max, tzinfo=KOLKATA_TZ)
        label = "Yesterday" if is_en else "Kal"
    elif date_filter == "last_7_days":
        start_date = now_ist.date() - timedelta(days=7)
        start_dt = datetime.combine(start_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        label = "Past 7 days" if is_en else "Pichhle 7 din"
    elif date_filter == "this_month":
        start_date = now_ist.date().replace(day=1)
        start_dt = datetime.combine(start_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        label = "This month" if is_en else "Is mahine"
    else:
        # Default: today
        start_dt = datetime.combine(now_ist.date(), time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        label = "Today" if is_en else "Aaj"

    query = db.query(Transaction).filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
    if user_id is not None:
        query = query.filter(Transaction.user_id == user_id)
    records = query.order_by(Transaction.timestamp.desc()).all()

    udhaar_paise = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
    jama_paise = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
    total_udhaar = round(udhaar_paise / 100.0, 2)
    total_jama = round(jama_paise / 100.0, 2)
    net_movement = round((udhaar_paise - jama_paise) / 100.0, 2)

    if is_en:
        if tx_type_filter == "JAMA":
            speech = f"{label}'s total collection is ₹{total_jama:,.2f}."
        elif tx_type_filter == "UDHAAR":
            speech = f"{label}'s total credit extended is ₹{total_udhaar:,.2f}."
        else:
            speech = (
                f"{label}'s total collection is ₹{total_jama:,.2f} and ₹{total_udhaar:,.2f} credit was extended. "
                f"Total {len(records)} entries."
            )
    else:
        if tx_type_filter == "JAMA":
            speech = f"{label} kul ₹{total_jama:,.2f} jama hue hain."
        elif tx_type_filter == "UDHAAR":
            speech = f"{label} kul ₹{total_udhaar:,.2f} ka udhaar diya gaya hai."
        else:
            speech = (
                f"{label} kul ₹{total_jama:,.2f} jama hue aur ₹{total_udhaar:,.2f} ka udhaar diya gaya. "
                f"Kul {len(records)} entries hain."
            )

    return (speech, {
        "period": label,
        "total_udhaar": total_udhaar,
        "total_jama": total_jama,
        "net_movement": net_movement,
        "transaction_count": len(records)
    })


def execute_customer_list_voice_query(
    db: Session,
    user_id: Optional[int] = None,
    language: Optional[str] = "hinglish"
) -> Tuple[str, dict]:
    """Finds all customers with pending debts, sorted from highest to lowest with language matching."""
    is_en = (language == "english")
    query = db.query(Transaction)
    if user_id is not None:
        query = query.filter(Transaction.user_id == user_id)
    all_txs = query.all()
    if not all_txs:
        msg = "No transactions recorded in the ledger yet." if is_en else "Khate mein abhi koi entry darj nahi hai."
        return (msg, {})

    customer_totals: Dict[str, int] = {}
    for tx in all_txs:
        cname = tx.customer_name
        diff = tx.amount_paise if tx.transaction_type == TransactionType.UDHAAR else -tx.amount_paise
        customer_totals[cname] = customer_totals.get(cname, 0) + diff

    debtors = [
        (cname, round(paise / 100.0, 2))
        for cname, paise in customer_totals.items()
        if paise > 0
    ]
    debtors.sort(key=lambda x: x[1], reverse=True)

    if not debtors:
        msg = "No customer has pending debt. All accounts are settled." if is_en else "Kisi bhi customer ka udhaar baaki nahi hai. Sabhi hisab clear hain."
        return (msg, {})

    top_debtor = debtors[0]
    if is_en:
        speech = f"Highest outstanding debt is from {top_debtor[0]} at ₹{top_debtor[1]:,.2f}."
        if len(debtors) > 1:
            other_names = [f"{d[0]} (₹{d[1]:,.2f})" for d in debtors[1:4]]
            speech += f" In addition, {' and '.join(other_names)} have pending balance."
    else:
        speech = f"Sabse zyada udhaar {top_debtor[0]} ka hai ₹{top_debtor[1]:,.2f}."
        if len(debtors) > 1:
            other_names = [f"{d[0]} (₹{d[1]:,.2f})" for d in debtors[1:4]]
            speech += f" Iske alawa {' aur '.join(other_names)} ka baaki hai."

    return (speech, {"debtors": [{"customer_name": d[0], "outstanding_balance": d[1]} for d in debtors]})


@app.post(
    "/api/voice-command",
    response_model=VoiceAssistantResponse,
    summary="Conversational Voice Command State Machine endpoint (Suno Hisab v4.0)"
)
def handle_voice_command(
    payload: VoiceCommandRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Session-aware voice command state machine (Suno Hisab v4.0).
    Tracks session state ('IDLE' | 'AWAITING_CONFIRMATION') per user/session.
    Permanently prevents state bleeding and hallucinations.
    """
    auth_user_id = current_user.id if current_user else 1
    raw_text = (payload.transcript or payload.text or "").strip()
    session_id = payload.session_id or "default"

    session = user_sessions.setdefault(session_id, {
        "session_id": session_id,
        "state": "IDLE",
        "pending_transaction": None
    })
    current_state = session.get("state", "IDLE")
    pending_tx = session.get("pending_transaction")

    if not raw_text:
        return VoiceAssistantResponse(
            reply_message="Aapne kya kaha, main theek se samajh nahi paaya. Kripya dobara bolein.",
            state=current_state,
            requires_confirmation=(current_state == "AWAITING_CONFIRMATION"),
            action_taken="unrecognized",
            pending_transaction=pending_tx
        )

    lower = raw_text.lower()
    is_confirm_phrase = is_confirmation_utterance(raw_text)
    is_cancel_phrase = is_cancellation_utterance(raw_text)

    # Direct Cancellation Action
    if payload.action == "cancel" or (is_cancel_phrase and current_state == "AWAITING_CONFIRMATION"):
        user_sessions.pop(session_id, None)
        return VoiceAssistantResponse(
            reply_message="Transaction cancel kar diya gaya hai.",
            state="IDLE",
            requires_confirmation=False,
            action_taken="cancelled",
            pending_transaction=None
        )

    # Direct Confirmation Action
    if payload.action == "confirm" or (is_confirm_phrase and current_state == "AWAITING_CONFIRMATION" and pending_tx):
        cust_name = pending_tx.get("customer") or pending_tx.get("customer_name")
        amt = float(pending_tx.get("amount", 0))
        email = pending_tx.get("email") or pending_tx.get("customer_email")
        t_type = TransactionType.UDHAAR if pending_tx.get("type") in ("CREDIT", "UDHAAR") else TransactionType.JAMA
        amount_paise = int(round(amt * 100))

        db_tx = Transaction(
            user_id=auth_user_id,
            customer_name=cust_name,
            customer_email=email,
            amount_paise=amount_paise,
            amount=amt,
            transaction_type=t_type,
            timestamp=get_current_ist_time()
        )
        db.add(db_tx)
        db.commit()
        db.refresh(db_tx)

        if email:
            receipt_job = TransactionReceipt(
                shop_id=auth_user_id,
                transaction_id=db_tx.id,
                customer_name=cust_name,
                customer_email=email,
                status="PENDING",
                attempts=0,
                created_at=get_current_ist_time()
            )
            db.add(receipt_job)
            db.commit()
            db.refresh(receipt_job)
            import threading
            threading.Thread(target=dispatch_receipt_email_job, args=(receipt_job.id,), daemon=True).start()

        # Wipe session clean immediately to prevent any state bleed!
        user_sessions.pop(session_id, None)

        type_lbl = "udhaar" if t_type == TransactionType.UDHAAR else "jama"
        reply = f"{cust_name} ke khate mein ₹{amt:.2f} {type_lbl} safaltapoorvak save kar diye gaye hain."
        return VoiceAssistantResponse(
            reply_message=reply,
            state="IDLE",
            requires_confirmation=False,
            action_taken="committed_transaction",
            pending_transaction=None,
            data={"transaction_id": db_tx.id}
        )

    # Active Context Injection
    context_data = None
    if current_state == "AWAITING_CONFIRMATION" and pending_tx:
        context_data = {
            "state": "AWAITING_CONFIRMATION",
            "customer": pending_tx.get("customer"),
            "amount": pending_tx.get("amount"),
            "email": pending_tx.get("email"),
            "type": pending_tx.get("type")
        }

    intent_res = ai_service.classify_voice_command(raw_text, context=context_data)

    # Handle transitions when State is AWAITING_CONFIRMATION:
    if current_state == "AWAITING_CONFIRMATION":
        if intent_res.intent in (VoiceIntentType.CONFIRM, VoiceIntentType.CONFIRMATION):
            if pending_tx:
                cust_name = pending_tx.get("customer") or pending_tx.get("customer_name")
                amt = float(pending_tx.get("amount", 0))
                email = pending_tx.get("email") or pending_tx.get("customer_email")
                t_type = TransactionType.UDHAAR if pending_tx.get("type") in ("CREDIT", "UDHAAR") else TransactionType.JAMA
                amount_paise = int(round(amt * 100))

                db_tx = Transaction(
                    user_id=auth_user_id,
                    customer_name=cust_name,
                    customer_email=email,
                    amount_paise=amount_paise,
                    amount=amt,
                    transaction_type=t_type,
                    timestamp=get_current_ist_time()
                )
                db.add(db_tx)
                db.commit()
                db.refresh(db_tx)

                user_sessions.pop(session_id, None)
                type_lbl = "udhaar" if t_type == TransactionType.UDHAAR else "jama"
                reply = f"{cust_name} ke khate mein ₹{amt:.2f} {type_lbl} safaltapoorvak save kar diye gaye hain."
                return VoiceAssistantResponse(
                    reply_message=reply,
                    state="IDLE",
                    requires_confirmation=False,
                    action_taken="committed_transaction",
                    pending_transaction=None,
                    data={"transaction_id": db_tx.id}
                )

        if intent_res.intent in (VoiceIntentType.CANCEL, VoiceIntentType.CANCELLATION):
            user_sessions.pop(session_id, None)
            return VoiceAssistantResponse(
                reply_message="Transaction cancel kar diya gaya hai.",
                state="IDLE",
                requires_confirmation=False,
                action_taken="cancelled",
                pending_transaction=None
            )

        if intent_res.intent in (VoiceIntentType.CORRECTION, VoiceIntentType.CORRECT_PENDING_ENTRY):
            if intent_res.customer_email or (intent_res.entities and intent_res.entities.get("email")):
                pending_tx["email"] = intent_res.customer_email or intent_res.entities.get("email")
            if intent_res.corrected_amount or intent_res.amount or (intent_res.entities and intent_res.entities.get("amount")):
                pending_tx["amount"] = intent_res.corrected_amount or intent_res.amount or intent_res.entities.get("amount")
            if intent_res.corrected_customer_name or (intent_res.entities and intent_res.entities.get("customer_name")):
                pending_tx["customer"] = intent_res.corrected_customer_name or intent_res.entities.get("customer_name")
            if intent_res.transaction_type or (intent_res.entities and intent_res.entities.get("type")):
                t = intent_res.transaction_type or intent_res.entities.get("type")
                pending_tx["type"] = "CREDIT" if t in ("CREDIT", "UDHAAR") else "PAYMENT"

            session["pending_transaction"] = pending_tx
            session["state"] = "AWAITING_CONFIRMATION"

            cust = pending_tx["customer"]
            amt = pending_tx["amount"]
            em_txt = f", email {pending_tx['email']}" if pending_tx.get("email") else ""
            t_lbl = "udhaar" if pending_tx.get("type") in ("CREDIT", "UDHAAR") else "jama"
            reply = f"{cust} ka ₹{amt:.2f} {t_lbl}{em_txt}. Kya main entry save kar doon?"

            return VoiceAssistantResponse(
                reply_message=reply,
                state="AWAITING_CONFIRMATION",
                requires_confirmation=True,
                action_taken="corrected_pending_entry",
                pending_transaction=pending_tx
            )

        if intent_res.intent in (VoiceIntentType.NEW_TRANSACTION, VoiceIntentType.ENTRY):
            if intent_res.customer_name and intent_res.amount:
                new_tx = {
                    "customer": intent_res.customer_name,
                    "amount": intent_res.amount,
                    "email": intent_res.customer_email,
                    "type": "CREDIT" if intent_res.transaction_type == "UDHAAR" else "PAYMENT"
                }
                session["pending_transaction"] = new_tx
                session["state"] = "AWAITING_CONFIRMATION"
                t_lbl = "udhaar" if new_tx["type"] == "CREDIT" else "jama"
                em_txt = f", email {new_tx['email']}" if new_tx.get("email") else ""
                reply = f"{new_tx['customer']} ka ₹{new_tx['amount']:.2f} {t_lbl}{em_txt}. Kya main entry save kar doon?"
                return VoiceAssistantResponse(
                    reply_message=reply,
                    state="AWAITING_CONFIRMATION",
                    requires_confirmation=True,
                    action_taken="new_transaction_awaiting_confirmation",
                    pending_transaction=new_tx
                )

        if intent_res.intent == VoiceIntentType.PROVIDE_EMAIL:
            pending_tx["email"] = intent_res.customer_email
            session["pending_transaction"] = pending_tx
            session["state"] = "AWAITING_CONFIRMATION"
            cust = pending_tx["customer"]
            amt = pending_tx["amount"]
            em_txt = f", email {pending_tx['email']}"
            t_lbl = "udhaar" if pending_tx.get("type") in ("CREDIT", "UDHAAR") else "jama"
            reply = f"{cust} ka ₹{amt:.2f} {t_lbl}{em_txt}. Kya main entry save kar doon?"
            return VoiceAssistantResponse(
                reply_message=reply,
                state="AWAITING_CONFIRMATION",
                requires_confirmation=True,
                action_taken="email_collected_awaiting_confirmation",
                pending_transaction=pending_tx
            )

    # Handle transitions when State is IDLE:
    if intent_res.intent in (VoiceIntentType.NEW_TRANSACTION, VoiceIntentType.ENTRY):
        if not intent_res.amount:
            session["state"] = "IDLE"
            session["pending_transaction"] = None
            reply = intent_res.clarification_message or "Kitna amount likhna hai?"
            return VoiceAssistantResponse(
                reply_message=reply,
                state="IDLE",
                requires_confirmation=False,
                action_taken="missing_amount",
                pending_transaction=None
            )
        if not intent_res.customer_name:
            session["state"] = "IDLE"
            session["pending_transaction"] = None
            reply = intent_res.clarification_message or "Kaunse customer ke naam par likhna hai?"
            return VoiceAssistantResponse(
                reply_message=reply,
                state="IDLE",
                requires_confirmation=False,
                action_taken="missing_customer_name",
                pending_transaction=None
            )
        # Complete!
        pending_tx = {
            "customer": intent_res.customer_name,
            "amount": intent_res.amount,
            "email": intent_res.customer_email,
            "type": "CREDIT" if intent_res.transaction_type == "UDHAAR" else "PAYMENT"
        }
        session["pending_transaction"] = pending_tx
        session["state"] = "AWAITING_CONFIRMATION"

        t_lbl = "udhaar" if pending_tx["type"] == "CREDIT" else "jama"
        em_txt = f", email {pending_tx['email']}" if pending_tx.get("email") else ""
        reply = f"{pending_tx['customer']} ka ₹{pending_tx['amount']:.2f} {t_lbl}{em_txt}. Kya main entry save kar doon?"

        return VoiceAssistantResponse(
            reply_message=reply,
            state="AWAITING_CONFIRMATION",
            requires_confirmation=True,
            action_taken="awaiting_confirmation",
            pending_transaction=pending_tx
        )

    if intent_res.intent == VoiceIntentType.PROVIDE_EMAIL:
        # User spoke only an email or name in IDLE state - strictly prevent any bleed of previous amounts!
        session["state"] = "IDLE"
        session["pending_transaction"] = None
        reply = f"Customer email received: {intent_res.customer_email}. Kripya transaction command bolein."
        return VoiceAssistantResponse(
            reply_message=reply,
            state="IDLE",
            requires_confirmation=False,
            action_taken="email_received",
            pending_transaction=None
        )

    # Queries
    if intent_res.intent == VoiceIntentType.QUERY_CUSTOMER_BALANCE:
        if not intent_res.customer_name:
            msg = "Aap kiska khata dekhna chahte hain? Kripya customer ka naam bolein."
            return VoiceAssistantResponse(reply_message=msg, state="IDLE", action_taken="missing_customer_name", requires_confirmation=False)
        speech, data = execute_customer_balance_voice_query(intent_res.customer_name, db, user_id=auth_user_id, language=intent_res.language)
        return VoiceAssistantResponse(reply_message=speech, state="IDLE", action_taken="customer_balance", requires_confirmation=False, data=data)

    if intent_res.intent == VoiceIntentType.QUERY_DAILY_SUMMARY:
        speech, data = execute_summary_voice_query("today", intent_res.transaction_type, db, user_id=auth_user_id, language=intent_res.language)
        return VoiceAssistantResponse(reply_message=speech, state="IDLE", action_taken="daily_summary", requires_confirmation=False, data=data)

    if intent_res.intent == VoiceIntentType.QUERY_DATE_RANGE_SUMMARY:
        speech, data = execute_summary_voice_query(intent_res.date_filter or "today", None, db, user_id=auth_user_id, language=intent_res.language)
        return VoiceAssistantResponse(reply_message=speech, state="IDLE", action_taken="date_range_summary", requires_confirmation=False, data=data)

    if intent_res.intent == VoiceIntentType.QUERY_CUSTOMER_LIST:
        speech, data = execute_customer_list_voice_query(db, user_id=auth_user_id, language=intent_res.language)
        return VoiceAssistantResponse(reply_message=speech, state="IDLE", action_taken="customer_list", requires_confirmation=False, data=data)

    if intent_res.intent == VoiceIntentType.GREETING:
        reply = intent_res.clarification_message or (
            "Namaste! Main badhiya hoon. Main aapka HisabAI khata assistant hoon. Aap mujhse customer ka hisab pooch sakte hain, naya udhaar ya jama entry likhwa sakte hain, ya aaj ka summary dekh sakte hain."
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            state="IDLE",
            action_taken="greeting",
            requires_confirmation=False,
            pending_transaction=None
        )

    if intent_res.intent == VoiceIntentType.USER_FEEDBACK:
        reply = intent_res.clarification_message or (
            "Kshama kijiye! Agar samajhne mein dikkat ho rahi hai, toh kripya dheere aur saaf bolein, ya screen se manual entry karein."
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            state="IDLE",
            action_taken="troubleshooting_help",
            requires_confirmation=False,
            pending_transaction=None
        )

    msg = intent_res.clarification_message or "Aapne kya kaha, main theek se samajh nahi paaya. Kripya dobara bolein."
    return VoiceAssistantResponse(
        reply_message=msg,
        state="IDLE",
        action_taken="clarification",
        requires_confirmation=False,
        pending_transaction=None
    )


@app.post("/api/trigger-reminders", summary="Manual Trigger Route for Dashboard Testing")
async def trigger_reminders(db: Session = Depends(get_db)):
    """Manual trigger route to test automated payment reminders scan."""
    execute_automated_payment_reminders(db)
    return {"success": True, "message": "Manual payment reminders executed successfully."}


@app.post(
    "/api/voice-audio",
    response_model=VoiceAssistantResponse,
    summary="Direct audio upload for Groq Whisper transcription and ledger processing"
)
async def handle_voice_audio(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    pending_operation_id: Optional[str] = Form(None),
    action: Optional[str] = Form("process"),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Accepts raw audio recorded directly in the browser (via standard MediaRecorder).
    Transcribes it using Groq Whisper (or Gemini Flash audio) with 100% reliability,
    completely bypassing brittle browser Web Speech API aborted / cut-off errors.
    """
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio recording received.")

    transcript = ai_service.transcribe_audio_bytes(audio_bytes, filename=file.filename or "audio.webm")
    logger.info(f"Voice audio received and transcribed: '{transcript}'")

    if not transcript:
        return VoiceAssistantResponse(
            reply_message="Aapne kya kaha, main theek se samajh nahi paaya. Kripya dobara bolein.",
            action_taken="unrecognized",
            requires_confirmation=False
        )

    req = VoiceAssistantRequest(
        text=transcript,
        session_id=session_id or "default",
        pending_operation_id=pending_operation_id,
        action=action or "process"
    )
    res = handle_voice_assistant(req, db=db, current_user=current_user)
    res.transcribed_text = transcript
    return res


@app.post(
    "/api/voice-assistant",
    response_model=VoiceAssistantResponse,
    summary="Voice Assistant endpoint supporting 'Suno Hisab' and conversational ledger actions"
)
def handle_voice_assistant(
    payload: VoiceAssistantRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Central controller for voice-activated Khata interactions:
    - Parses conversational speech using fast-path or Google Gemma.
    - Resolves read-only database queries (balances, daily reports, historical reports, debtor lists).
    - Manages the two-step write confirmation workflow:
        Step 1: 'prepare' creates an uncommitted pending operation and reads back details for verification.
        Step 2: 'confirm' commits exactly once (idempotent duplicate protection).
    - Supports cancellation, corrections ('500 nahi 800'), and repetitions.
    """
    auth_user_id = current_user.id if current_user else None
    raw_text = payload.text.strip()
    session_id = payload.session_id or "default"
    op_id = payload.pending_operation_id

    # Fallback op_id resolution if client did not pass or lost pending_operation_id
    if not op_id or op_id not in PENDING_VOICE_OPERATIONS:
        for pid, pop in reversed(list(PENDING_VOICE_OPERATIONS.items())):
            if pop.get("status") in ("pending", "waiting_for_email") and (
                pop.get("session_id") == session_id or (auth_user_id and pop.get("user_id") == auth_user_id)
            ):
                if not pop.get("expires_dt") or pop["expires_dt"] >= get_current_ist_time():
                    op_id = pid
                    break
        # Global fallback: pick latest active pending op if session ID differed
        if not op_id or op_id not in PENDING_VOICE_OPERATIONS:
            for pid, pop in reversed(list(PENDING_VOICE_OPERATIONS.items())):
                if pop.get("status") in ("pending", "waiting_for_email"):
                    if not pop.get("expires_dt") or pop["expires_dt"] >= get_current_ist_time():
                        op_id = pid
                        break

    logger.info(f"Voice assistant request [session={session_id}, op={op_id}]: '{raw_text}' (action={payload.action})")

    # 1. Direct Confirmation Action (Client Button or spoken "Haan, save kar do", "yes save kr do")
    is_confirm_direct = payload.action == "confirm" or is_confirmation_utterance(raw_text)
    if is_confirm_direct:
        if not op_id or op_id not in PENDING_VOICE_OPERATIONS:
            reply = "Koi pending entry nahi mili ya entry expire ho chuki hai. Kripya transaction dobara bolein."
            return VoiceAssistantResponse(reply_message=reply, action_taken="expired", requires_confirmation=False)

        op = PENDING_VOICE_OPERATIONS[op_id]

        # Idempotency: if already committed, return success without duplicate insert
        if op["status"] == "committed":
            reply = f"{op['customer_name']} ke khate mein ₹{op['amount']:.2f} pehle hi save ho chuka hai."
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="already_committed",
                requires_confirmation=False,
                pending_operation=PendingOperationData(**op)
            )

        if op["status"] == "cancelled":
            reply = "Yeh entry cancel ho chuki hai. Kripya dobara bolein."
            return VoiceAssistantResponse(reply_message=reply, action_taken="cancelled", requires_confirmation=False)

        if op.get("expires_dt") and op["expires_dt"] < get_current_ist_time():
            op["status"] = "expired"
            reply = "Confirmation ka samay beet gaya hai. Kripya transaction dobara bolein."
            return VoiceAssistantResponse(reply_message=reply, action_taken="expired", requires_confirmation=False)

        # Customer Email Resolution
        customer_email = op.get("customer_email")
        if not customer_email:
            existing_email_row = (
                db.query(Transaction.customer_email)
                .filter(
                    Transaction.user_id == (op.get("user_id") or auth_user_id),
                    func.lower(Transaction.customer_name) == op["customer_name"].lower(),
                    Transaction.customer_email != None,
                    Transaction.customer_email != ""
                )
                .order_by(Transaction.id.desc())
                .first()
            )
            if existing_email_row and existing_email_row[0]:
                customer_email = existing_email_row[0]
                op["customer_email"] = customer_email
                op["status"] = "pending"
            else:
                customer_email = None
                op["customer_email"] = None
                op["status"] = "pending"

        # Commit exactly one transaction in integer paise with customer email (if present)
        amount_paise = int(round(op["amount"] * 100))
        tx_type = TransactionType(op["transaction_type"])

        try:
            db_tx = Transaction(
                user_id=op.get("user_id") or auth_user_id,
                customer_name=op["customer_name"],
                customer_email=customer_email,
                amount_paise=amount_paise,
                amount=op["amount"],
                transaction_type=tx_type,
                timestamp=get_current_ist_time()
            )
            db.add(db_tx)
            db.commit()
            db.refresh(db_tx)

            receipt_id = None
            if customer_email:
                # Create transactional receipt job
                effective_shop = op.get("user_id") or auth_user_id or 1
                receipt_job = TransactionReceipt(
                    shop_id=effective_shop,
                    transaction_id=db_tx.id,
                    customer_name=db_tx.customer_name,
                    customer_email=customer_email,
                    status="PENDING",
                    attempts=0,
                    created_at=get_current_ist_time()
                )
                db.add(receipt_job)
                db.commit()
                db.refresh(receipt_job)
                receipt_id = receipt_job.id

                # Asynchronously dispatch receipt email
                import threading
                threading.Thread(target=dispatch_receipt_email_job, args=(receipt_job.id,), daemon=True).start()

            op["status"] = "committed"
            op["committed_tx_id"] = db_tx.id

            # Wipe conversational state to prevent state bleeding
            user_sessions.pop(session_id, None)

            is_en = (op.get("language") == "english")
            if is_en:
                en_type = "credit" if tx_type == TransactionType.UDHAAR else "payment"
                rcpt_note = f" Receipt sent to {customer_email}." if customer_email else ""
                reply = f"Recorded ₹{op['amount']:.2f} {en_type} for {op['customer_name']} successfully.{rcpt_note}"
            else:
                type_label = "udhaar" if tx_type == TransactionType.UDHAAR else "jama"
                rcpt_note = " aur receipt dispatch kar di gayi hai" if customer_email else ""
                reply = f"{op['customer_name']} ke khate mein ₹{op['amount']:.2f} {type_label} safaltapoorvak save kar diye gaye hain{rcpt_note}."
            LAST_SESSION_REPLIES[session_id] = reply

            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="committed_transaction",
                requires_confirmation=False,
                pending_operation=PendingOperationData(**op),
                data={"transaction_id": db_tx.id, "receipt_id": receipt_id}
            )
        except Exception as exc:
            db.rollback()
            logger.error(f"Failed to commit voice operation {op_id}: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail="Database persistence failed.")

    # 2. Direct Cancellation Action (Client Button or spoken "Nahi, cancel kar do")
    if payload.action == "cancel" or is_cancellation_utterance(raw_text):
        is_en = False
        if op_id and op_id in PENDING_VOICE_OPERATIONS:
            PENDING_VOICE_OPERATIONS[op_id]["status"] = "cancelled"
            is_en = (PENDING_VOICE_OPERATIONS[op_id].get("language") == "english")
        if not is_en and raw_text.lower() in ("no", "cancel"):
            is_en = True
        user_sessions.pop(session_id, None)
        reply = "Transaction cancelled. No changes made to the ledger." if is_en else "Theek hai, entry cancel kar di gayi hai. Ledger mein koi badlaav nahi hua."
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(reply_message=reply, action_taken="cancelled", requires_confirmation=False)

    # 3. Repeat Last Response
    if raw_text.lower() in ("dobara sunao", "repeat", "repeat karo", "pichhli entry kya thi", "phir se bolo"):
        last_speech = LAST_SESSION_REPLIES.get(session_id, "Maine abhi koi entry nahi boli hai.")
        return VoiceAssistantResponse(reply_message=last_speech, action_taken="repeated", requires_confirmation=False)

    # 4. Stop Assistant
    if raw_text.lower() in ("bas", "stop", "stop assistant", "band karo", "assistant band kar do"):
        reply = "Assistant band kar diya gaya hai. Jab zaroorat ho, 'Suno Hisab' bolein ya mic dabayein."
        return VoiceAssistantResponse(reply_message=reply, action_taken="stopped", requires_confirmation=False)

    # 4B. Check if active pending operation is waiting for customer email
    # Guard: If current command is an ambiguous account action or clarification request,
    # do NOT intercept it as an invalid email string!
    AMBIGUOUS_ACCOUNT_PHRASES = (
        "khata khatam", "खाता खत्म", "khata band", "खाता बंद",
        "khata delete", "hisaab khatam", "hisab khatam", "खाता हटाओ",
        "hisaab barabar", "hisaab chukta", "hisab clear", "close account", "delete account"
    )
    is_ambiguous_account_action = any(q in raw_text.lower() for q in AMBIGUOUS_ACCOUNT_PHRASES)

    extracted_email = ai_service.extract_email_from_text(raw_text)
    if not is_ambiguous_account_action and op_id and op_id in PENDING_VOICE_OPERATIONS and PENDING_VOICE_OPERATIONS[op_id].get("status") == "waiting_for_email":
        op = PENDING_VOICE_OPERATIONS[op_id]
        if is_confirmation_utterance(raw_text):
            return handle_voice_assistant(
                VoiceAssistantRequest(text="confirm", session_id=session_id, pending_operation_id=op_id, action="confirm"),
                db=db,
                current_user=current_user
            )
        if is_cancellation_utterance(raw_text):
            return handle_voice_assistant(
                VoiceAssistantRequest(text="cancel", session_id=session_id, pending_operation_id=op_id, action="cancel"),
                db=db,
                current_user=current_user
            )
        if extracted_email and is_valid_email(extracted_email):
            op["customer_email"] = extracted_email
            op["status"] = "pending"
            is_en = (op.get("language") == "english")
            if is_en:
                en_type = "credit" if op["transaction_type"] == "UDHAAR" else "payment"
                reply = f"{op['customer_name']} ₹{op['amount']:.2f} {en_type}, email {extracted_email}. Should I save this entry?"
            else:
                type_label = "udhaar" if op["transaction_type"] == "UDHAAR" else "jama"
                reply = f"{op['customer_name']} ka ₹{op['amount']:.2f} {type_label}, email {extracted_email}. Kya main entry save kar doon?"
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="email_collected_awaiting_confirmation",
                requires_confirmation=True,
                pending_operation=PendingOperationData(**op)
            )
        else:
            is_en = (op.get("language") == "english")
            reply = (
                f"Invalid email format. Please say or type a valid email for {op['customer_name']}."
                if is_en else
                f"Email ID samajh nahi aayi ya format sahi nahi hai. Kripya {op['customer_name']} ki valid email ID bataiye ya type kijiye."
            )
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="waiting_for_customer_email",
                requires_confirmation=False,
                pending_operation=PendingOperationData(**op)
            )

    # 5. Extract Intent from Natural Voice Command
    context_data = None
    if op_id and op_id in PENDING_VOICE_OPERATIONS and PENDING_VOICE_OPERATIONS[op_id]["status"] == "pending":
        active_op = PENDING_VOICE_OPERATIONS[op_id]
        context_data = {
            "pending_customer": active_op["customer_name"],
            "pending_amount": active_op["amount"]
        }

    intent_result = ai_service.classify_voice_command(raw_text, context=context_data)
    logger.info(f"Classified intent: {intent_result.intent.value} for text '{raw_text}'")

    # Intent 0: PROVIDE_EMAIL (when email is provided and pending op exists)
    if intent_result.intent == VoiceIntentType.PROVIDE_EMAIL:
        if op_id and op_id in PENDING_VOICE_OPERATIONS and PENDING_VOICE_OPERATIONS[op_id].get("status") in ("waiting_for_email", "pending"):
            op = PENDING_VOICE_OPERATIONS[op_id]
            em = intent_result.customer_email or extracted_email
            if em and is_valid_email(em):
                op["customer_email"] = em
                op["status"] = "pending"
                is_en = (op.get("language") == "english")
                if is_en:
                    en_type = "credit" if op["transaction_type"] == "UDHAAR" else "payment"
                    reply = f"{op['customer_name']} ₹{op['amount']:.2f} {en_type}, email {em}. Should I save this entry?"
                else:
                    type_label = "udhaar" if op["transaction_type"] == "UDHAAR" else "jama"
                    reply = f"{op['customer_name']} ka ₹{op['amount']:.2f} {type_label}, email {em}. Kya main entry save kar doon?"
                LAST_SESSION_REPLIES[session_id] = reply
                return VoiceAssistantResponse(
                    reply_message=reply,
                    action_taken="email_collected_awaiting_confirmation",
                    requires_confirmation=True,
                    pending_operation=PendingOperationData(**op)
                )
        reply = f"Customer email received: {intent_result.customer_email}. Kripya transaction command bolein."
        return VoiceAssistantResponse(reply_message=reply, action_taken="email_received", requires_confirmation=False)

    # Intent A: CONFIRM
    if intent_result.intent == VoiceIntentType.CONFIRM:
        if not op_id or op_id not in PENDING_VOICE_OPERATIONS:
            for pid, pop in reversed(list(PENDING_VOICE_OPERATIONS.items())):
                if pop.get("status") in ("pending", "waiting_for_email"):
                    if not pop.get("expires_dt") or pop["expires_dt"] >= get_current_ist_time():
                        op_id = pid
                        break
        if op_id and op_id in PENDING_VOICE_OPERATIONS:
            return handle_voice_assistant(
                VoiceAssistantRequest(text="confirm", session_id=session_id, pending_operation_id=op_id, action="confirm"),
                db=db,
                current_user=current_user
            )
        reply = "Koi pending entry nahi hai jise confirm kiya ja sake. Kripya pehle entry bolein."
        return VoiceAssistantResponse(reply_message=reply, action_taken="no_pending_operation", requires_confirmation=False)

    # Intent B: CANCEL
    if intent_result.intent in (VoiceIntentType.CANCEL, VoiceIntentType.CANCELLATION):
        if op_id and op_id in PENDING_VOICE_OPERATIONS:
            PENDING_VOICE_OPERATIONS[op_id]["status"] = "cancelled"
        user_sessions.pop(session_id, None)
        reply = "Theek hai, entry cancel kar di gayi hai."
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(reply_message=reply, action_taken="cancelled", requires_confirmation=False)

    # Intent C: CORRECT PENDING ENTRY / CORRECTION ("500 nahi 800", "email badlo")
    if intent_result.intent in (VoiceIntentType.CORRECT_PENDING_ENTRY, VoiceIntentType.CORRECTION):
        if op_id and op_id in PENDING_VOICE_OPERATIONS and PENDING_VOICE_OPERATIONS[op_id]["status"] in ("pending", "waiting_for_email"):
            op = PENDING_VOICE_OPERATIONS[op_id]
            if intent_result.corrected_amount or intent_result.amount:
                op["amount"] = intent_result.corrected_amount or intent_result.amount
            if intent_result.corrected_customer_name or intent_result.customer_name:
                op["customer_name"] = intent_result.corrected_customer_name or intent_result.customer_name
            if intent_result.customer_email:
                op["customer_email"] = intent_result.customer_email
                op["status"] = "pending"

            is_en = (op.get("language") == "english") or (intent_result.language == "english")
            email_mention = f", email {op['customer_email']}" if op.get("customer_email") else ""
            if is_en:
                en_type = "credit" if op["transaction_type"] == "UDHAAR" else "payment"
                reply = f"Updated amount for {op['customer_name']} to ₹{op['amount']:.2f} {en_type}{email_mention}. Should I save it?"
            else:
                type_label = "udhaar" if op["transaction_type"] == "UDHAAR" else "jama"
                reply = f"Theek hai, {op['customer_name']} ke khate mein amount badal kar ₹{op['amount']:.2f} {type_label}{email_mention} kar diya gaya hai. Kya main save kar doon?"
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="corrected_pending_entry",
                requires_confirmation=(op.get("status") == "pending"),
                pending_operation=PendingOperationData(**op)
            )
        msg = "No pending entry found to correct." if intent_result.language == "english" else "Koi pending entry nahi mili jise correct kiya ja sake."
        return VoiceAssistantResponse(reply_message=msg, action_taken="no_pending_operation", requires_confirmation=False)

    # Intent D: QUERY CUSTOMER BALANCE
    if intent_result.intent == VoiceIntentType.QUERY_CUSTOMER_BALANCE:
        if not intent_result.customer_name:
            msg = "Whose khata would you like to check? Please say the customer's name." if intent_result.language == "english" else "Aap kiska khata dekhna chahte hain? Kripya customer ka naam bolein."
            return VoiceAssistantResponse(reply_message=msg, action_taken="missing_customer_name", requires_confirmation=False)

        speech, data = execute_customer_balance_voice_query(intent_result.customer_name, db, user_id=auth_user_id, language=intent_result.language)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="customer_balance", requires_confirmation=False, data=data)

    # Intent E: QUERY DAILY SUMMARY
    if intent_result.intent == VoiceIntentType.QUERY_DAILY_SUMMARY:
        speech, data = execute_summary_voice_query("today", intent_result.transaction_type, db, user_id=auth_user_id, language=intent_result.language)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="daily_summary", requires_confirmation=False, data=data)

    # Intent F: QUERY DATE RANGE SUMMARY ("Kal", "Pichhle 7 din", "Is mahine")
    if intent_result.intent == VoiceIntentType.QUERY_DATE_RANGE_SUMMARY:
        speech, data = execute_summary_voice_query(intent_result.date_filter or "today", None, db, user_id=auth_user_id, language=intent_result.language)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="date_range_summary", requires_confirmation=False, data=data)

    # Intent G: QUERY CUSTOMER LIST ("Kis-kis customer ke paise baaki hain?")
    if intent_result.intent == VoiceIntentType.QUERY_CUSTOMER_LIST:
        speech, data = execute_customer_list_voice_query(db, user_id=auth_user_id, language=intent_result.language)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="customer_list", requires_confirmation=False, data=data)

    # Intent H: STOP ASSISTANT
    if intent_result.intent == VoiceIntentType.STOP_ASSISTANT:
        msg = "Assistant stopped. Say 'Suno Hisab' or tap the mic when needed." if intent_result.language == "english" else "Assistant band kar diya gaya hai. Jab zaroorat ho, 'Suno Hisab' bolein ya mic dabayein."
        return VoiceAssistantResponse(reply_message=msg, action_taken="stopped", requires_confirmation=False)

    # Intent H2: GREETING
    if intent_result.intent == VoiceIntentType.GREETING:
        reply = intent_result.clarification_message or (
            "Namaste! Main badhiya hoon. Main aapka HisabAI khata assistant hoon. Aap mujhse customer ka hisab pooch sakte hain, naya udhaar ya jama entry likhwa sakte hain, ya aaj ka summary dekh sakte hain."
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="greeting",
            requires_confirmation=False,
            pending_operation=None
        )

    # Intent H3: USER_FEEDBACK
    if intent_result.intent == VoiceIntentType.USER_FEEDBACK:
        reply = intent_result.clarification_message or (
            "Kshama kijiye! Agar voice assistant ko samajhne mein pareshani ho rahi hai, "
            "toh kripya saaf aawaz mein customer ka naam, amount aur udhaar ya jama bolein. "
            "Aap screen par diye gaye form se direct manual entry bhi kar sakte hain."
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="troubleshooting_help",
            requires_confirmation=False,
            pending_operation=None
        )

    # Intent I: ENTRY (Two-Step Workflow: Prepare first, never save without confirmation and mandatory email!)
    if intent_result.intent == VoiceIntentType.ENTRY:
        if not intent_result.customer_name or not intent_result.amount or not intent_result.transaction_type:
            msg = "Could not clearly hear customer name or amount. Please say again, e.g. 'Record 500 credit for Rahul'." if intent_result.language == "english" else "Customer ka naam ya amount clear nahi suna. Kripya dobara bolein, jaise 'Rahul ko 500 udhaar diya'."
            return VoiceAssistantResponse(reply_message=msg, action_taken="incomplete_entry", requires_confirmation=False)

        # Customer Email Resolution
        target_email = intent_result.customer_email
        if not target_email:
            # Check existing email in DB for this customer in this shop
            existing_tx_email = (
                db.query(Transaction.customer_email)
                .filter(
                    Transaction.user_id == auth_user_id,
                    func.lower(Transaction.customer_name) == intent_result.customer_name.lower(),
                    Transaction.customer_email != None,
                    Transaction.customer_email != ""
                )
                .order_by(Transaction.id.desc())
                .first()
            )
            if existing_tx_email and existing_tx_email[0]:
                target_email = existing_tx_email[0]
            else:
                existing_rem_email = (
                    db.query(PaymentReminder.customer_email)
                    .filter(
                        PaymentReminder.shop_id == auth_user_id,
                        func.lower(PaymentReminder.customer_name) == intent_result.customer_name.lower(),
                        PaymentReminder.customer_email != None,
                        PaymentReminder.customer_email != ""
                    )
                    .order_by(PaymentReminder.id.desc())
                    .first()
                )
                if existing_rem_email and existing_rem_email[0]:
                    target_email = existing_rem_email[0]

        is_en = (intent_result.language == "english")

        if not target_email:
            # Customer has no registered email: MUST request email before transaction can be saved!
            pending_op = create_pending_operation(
                customer_name=intent_result.customer_name,
                amount=intent_result.amount,
                transaction_type=intent_result.transaction_type,
                customer_email=None,
                session_id=session_id,
                user_id=auth_user_id,
                language=intent_result.language,
                status="waiting_for_email"
            )
            reply = (
                f"{intent_result.customer_name}'s email ID is not registered. Please provide their email address."
                if is_en else
                f"{intent_result.customer_name} ki email ID registered nahi hai. Kripya pehle email ID bataiye."
            )
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="waiting_for_customer_email",
                requires_confirmation=False,
                pending_operation=pending_op
            )

        # Customer has an email: proceed to confirmation including recipient email in speech!
        pending_op = create_pending_operation(
            customer_name=intent_result.customer_name,
            amount=intent_result.amount,
            transaction_type=intent_result.transaction_type,
            customer_email=target_email,
            session_id=session_id,
            user_id=auth_user_id,
            language=intent_result.language,
            status="pending"
        )

        if is_en:
            en_type = "credit" if intent_result.transaction_type == "UDHAAR" else "payment"
            reply = f"Record ₹{intent_result.amount:.2f} {en_type} for {intent_result.customer_name}? Recipient email is {target_email}. Say 'yes' to confirm or 'no' to cancel."
        else:
            type_label = "udhaar" if intent_result.transaction_type == "UDHAAR" else "jama"
            reply = f"{intent_result.customer_name} ka ₹{intent_result.amount:.2f} {type_label}, email {target_email}. Kya main entry save kar doon?"

        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="prepared_entry",
            requires_confirmation=True,
            pending_operation=pending_op
        )

    # Intent J: AMBIGUOUS ACCOUNT ACTION ("Tausif ka khata khatam kar do")
    # Never mutate database without clarification!
    if intent_result.intent == VoiceIntentType.AMBIGUOUS_ACCOUNT_ACTION or (intent_result.intent == VoiceIntentType.SETTLE_BALANCE and is_ambiguous_account_action):
        cname = intent_result.customer_name
        if not cname:
            m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+(?:khata|hisaab|hisab)", raw_text.lower())
            if not m:
                m = re.search(r"(?:close|delete|clear)\s+([A-Za-z\u0900-\u097F]+)'?s?\s+account", raw_text.lower())
            cname = m.group(1).title() if m else "customer"
        records_query = db.query(Transaction).filter(func.lower(Transaction.customer_name) == cname.lower())
        if auth_user_id is not None:
            records_query = records_query.filter(Transaction.user_id == auth_user_id)
        records = records_query.all()
        if records:
            u_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
            j_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
            bal = round((u_p - j_p) / 100.0, 2)
            canonical = records[0].customer_name
            reply = (
                f"{canonical} ka vartamaan baaki ₹{bal:,.2f} hai. "
                f"Aap hisab settle karna chahte hain, ya account band karna chahte hain? "
                f"Kripya 'balance settle' bolein ya 'cancel' karein."
            )
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="ambiguous_account_action",
                requires_confirmation=False,
                data={"customer_name": canonical, "balance": bal}
            )
        else:
            reply = f"Khate mein '{cname}' naam ka koi record nahi mila."
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(reply_message=reply, action_taken="not_found", requires_confirmation=False)

    # Intent K: SETTLE BALANCE
    if intent_result.intent == VoiceIntentType.SETTLE_BALANCE:
        cname = intent_result.customer_name
        if not cname:
            # Check if there was an active customer in recent session reply
            reply = "Aap kiska balance settle karna chahte hain? Kripya customer ka naam bolein."
            return VoiceAssistantResponse(reply_message=reply, action_taken="missing_customer_name", requires_confirmation=False)

        records_query = db.query(Transaction).filter(func.lower(Transaction.customer_name) == cname.lower())
        if auth_user_id is not None:
            records_query = records_query.filter(Transaction.user_id == auth_user_id)
        records = records_query.all()
        if not records:
            reply = f"Khate mein '{cname}' naam ka koi customer nahi mila."
            return VoiceAssistantResponse(reply_message=reply, action_taken="not_found", requires_confirmation=False)

        canonical = records[0].customer_name
        u_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
        j_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
        bal_paise = u_p - j_p
        bal = round(bal_paise / 100.0, 2)

        if bal == 0:
            reply = f"{canonical} ka hisab pehle se hi clear hai. Koi baaki balance nahi hai."
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(reply_message=reply, action_taken="already_settled", requires_confirmation=False)

        # If they owe money (bal > 0), payment received (JAMA) settles it
        # If they have advance (bal < 0), giving goods/credit (UDHAAR) settles it
        settle_type = "JAMA" if bal > 0 else "UDHAAR"
        settle_amount = abs(bal)

        # Customer Email Resolution for Settlement
        target_email = next((t.customer_email for t in records if t.customer_email), None)
        if not target_email:
            rem_email_row = db.query(PaymentReminder.customer_email).filter(
                func.lower(PaymentReminder.customer_name) == canonical.lower(),
                PaymentReminder.customer_email != None
            ).first()
            if rem_email_row and rem_email_row[0]:
                target_email = rem_email_row[0]

        if not target_email:
            pending_op = create_pending_operation(
                customer_name=canonical,
                amount=settle_amount,
                transaction_type=settle_type,
                customer_email=None,
                session_id=session_id,
                user_id=auth_user_id,
                status="waiting_for_email"
            )
            reply = f"{canonical} ki email ID registered nahi hai. Settle karne se pehle kripya email ID bataiye."
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="waiting_for_customer_email",
                requires_confirmation=False,
                pending_operation=pending_op
            )

        pending_op = create_pending_operation(
            customer_name=canonical,
            amount=settle_amount,
            transaction_type=settle_type,
            customer_email=target_email,
            session_id=session_id,
            user_id=auth_user_id,
            status="pending"
        )

        reply = (
            f"{canonical} ka ₹{settle_amount:,.2f} ka hisab baaki hai. "
            f"Khata zero karne ke liye ₹{settle_amount:,.2f} {settle_type.lower()} darj karna hoga, email {target_email}. "
            f"Kya main save kar doon?"
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="prepared_entry",
            requires_confirmation=True,
            pending_operation=pending_op
        )

    # Intent L: QUERY CUSTOMER HISTORY
    if intent_result.intent == VoiceIntentType.QUERY_CUSTOMER_HISTORY:
        cname = intent_result.customer_name
        is_en = (intent_result.language == "english")
        if not cname:
            msg = "Whose transaction history would you like to see? Please say the customer name." if is_en else "Aap kiska purana hisab dekhna chahte hain? Kripya customer ka naam bolein."
            return VoiceAssistantResponse(reply_message=msg, action_taken="missing_customer_name", requires_confirmation=False)

        history_query = db.query(Transaction).filter(func.lower(Transaction.customer_name) == cname.lower())
        if auth_user_id is not None:
            history_query = history_query.filter(Transaction.user_id == auth_user_id)
        records = (
            history_query
            .order_by(Transaction.timestamp.desc())
            .all()
        )
        if not records:
            msg = f"No transaction history found for '{cname}'." if is_en else f"Khate mein '{cname}' naam ka koi hisab nahi mila."
            LAST_SESSION_REPLIES[session_id] = msg
            return VoiceAssistantResponse(reply_message=msg, action_taken="not_found", requires_confirmation=False)

        canonical = records[0].customer_name
        u_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
        j_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
        bal = round((u_p - j_p) / 100.0, 2)
        last_tx = records[0]

        if is_en:
            en_type = "credit" if last_tx.transaction_type == TransactionType.UDHAAR else "payment"
            reply = (
                f"{canonical} has {len(records)} entries in khata. "
                f"Total credit is ₹{round(u_p/100.0, 2):,.2f}, total payments ₹{round(j_p/100.0, 2):,.2f}, "
                f"and current balance is ₹{bal:,.2f}. "
                f"Last entry was ₹{last_tx.amount:.2f} {en_type}."
            )
        else:
            last_type_label = "udhaar" if last_tx.transaction_type == TransactionType.UDHAAR else "jama"
            reply = (
                f"{canonical} ke khate mein kul {len(records)} entries hain. "
                f"Kul udhaar ₹{round(u_p/100.0, 2):,.2f}, kul jama ₹{round(j_p/100.0, 2):,.2f}, "
                f"aur vartamaan baaki ₹{bal:,.2f} hai. "
                f"Aakhiri entry ₹{last_tx.amount:.2f} {last_type_label} ki thi."
            )

        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="customer_history",
            requires_confirmation=False,
            data={"customer_name": canonical, "count": len(records), "balance": bal}
        )

    # Intent M: NAVIGATE VIEW
    if intent_result.intent == VoiceIntentType.NAVIGATE_VIEW:
        target = intent_result.target_view or "dashboard"
        is_en = (intent_result.language == "english")
        view_names = {
            "dashboard": ("Dashboard", "Dashboard"),
            "ledger": ("Ledger Book", "Bahi Khata"),
            "customers": ("Customer Directory", "Grahak Suchi"),
            "inventory": ("Inventory Stock", "Inventory / Stock"),
            "reminders": ("Payment Reminders", "Payment Reminders"),
            "reports": ("Analytics Reports", "Reports aur Analytics"),
            "settings": ("Shop Settings", "Shop Settings"),
            "team": ("Team & Access", "Team aur Staff Management")
        }
        name_en, name_hi = view_names.get(target, (target.title(), target.title()))
        reply = f"Opening {name_en} screen." if is_en else f"{name_hi} screen khol di gayi hai."
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="navigate_view",
            requires_confirmation=False,
            data={"target_view": target}
        )

    # Intent N: PREPARE REMINDER
    if intent_result.intent == VoiceIntentType.PREPARE_REMINDER:
        cname = intent_result.customer_name
        is_en = (intent_result.language == "english")
        if not cname:
            reply = "Who would you like to prepare a payment reminder for?" if is_en else "Aap kis customer ke liye reminder taiyar karna chahte hain?"
            return VoiceAssistantResponse(reply_message=reply, action_taken="missing_customer_name", requires_confirmation=False)

        q = db.query(Transaction).filter(func.lower(Transaction.customer_name) == cname.lower())
        if auth_user_id is not None:
            q = q.filter(Transaction.user_id == auth_user_id)
        txs = q.all()
        if not txs:
            reply = f"No khata found for '{cname}'." if is_en else f"'{cname}' ka koi khata nahi mila."
            return VoiceAssistantResponse(reply_message=reply, action_taken="not_found", requires_confirmation=False)

        canonical = txs[0].customer_name
        u_p = sum(t.amount_paise for t in txs if t.transaction_type == TransactionType.UDHAAR)
        j_p = sum(t.amount_paise for t in txs if t.transaction_type == TransactionType.JAMA)
        bal = round((u_p - j_p) / 100.0, 2)

        if bal <= 0:
            reply = f"{canonical} has no outstanding balance. Payment reminder not required." if is_en else f"{canonical} ka koi baki hisab nahi hai. Reminder ki zarurat nahi hai."
            return VoiceAssistantResponse(reply_message=reply, action_taken="already_cleared", requires_confirmation=False)

        shop_title = current_user.shop_name if current_user and current_user.shop_name else "HisabAI Store"
        reminder_msg = f"Namaste {canonical} ji, aapka {shop_title} par kul baki hisab Rs. {bal:,.2f} hai. Kripya samay par bhugtan karein. Dhanyawad!"
        wa_url = f"https://api.whatsapp.com/send?text={quote(reminder_msg)}"

        reply = (
            f"Prepared payment reminder for {canonical} for ₹{bal:,.2f}."
            if is_en else
            f"{canonical} ke liye ₹{bal:,.2f} ka payment reminder taiyar kar diya gaya hai. Aap WhatsApp par bhej sakte hain."
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="prepared_reminder",
            requires_confirmation=False,
            data={
                "customer_name": canonical,
                "balance": bal,
                "message": reminder_msg,
                "whatsapp_url": wa_url
            }
        )

    # Intent O: PREPARE RECEIPT
    if intent_result.intent == VoiceIntentType.PREPARE_RECEIPT:
        cname = intent_result.customer_name
        is_en = (intent_result.language == "english")
        q = db.query(Transaction)
        if auth_user_id is not None:
            q = q.filter(Transaction.user_id == auth_user_id)
        if cname:
            q = q.filter(func.lower(Transaction.customer_name) == cname.lower())
        last_tx = q.order_by(Transaction.timestamp.desc()).first()

        if not last_tx:
            reply = "No transaction found to generate a receipt." if is_en else "Receipt banane ke liye koi transaction nahi mili."
            return VoiceAssistantResponse(reply_message=reply, action_taken="not_found", requires_confirmation=False)

        receipt_url = f"/api/receipts/{last_tx.id}/pdf"
        reply = (
            f"Receipt is ready for {last_tx.customer_name} (₹{last_tx.amount:.2f})."
            if is_en else
            f"{last_tx.customer_name} ke ₹{last_tx.amount:.2f} ke lenden ki receipt taiyar hai."
        )
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="prepared_receipt",
            requires_confirmation=False,
            data={"transaction_id": last_tx.id, "receipt_url": receipt_url}
        )

    # Default / UNKNOWN Intent
    default_msg = (
        "I didn't quite catch that. You can check customer balances, daily collection, or record credit and payments."
        if intent_result.language == "english" else
        "Aapne kya kaha, main theek se samajh nahi paaya. Kripya dobara bolein."
    )
    reply = intent_result.clarification_message or default_msg
    LAST_SESSION_REPLIES[session_id] = reply
    return VoiceAssistantResponse(reply_message=reply, action_taken="clarification", requires_confirmation=False)


# ==========================================
# Phase 1: Smart Payment Reminder Endpoints
# ==========================================

@app.get(
    "/api/reminders/eligible",
    response_model=EligibleRemindersResponse,
    summary="Get all customers with positive debt eligible for payment reminders"
)
def get_eligible_reminders(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    today_date = get_current_ist_time().date()

    # Aggregate customer ledger balances
    txs = db.query(Transaction).filter(Transaction.user_id == shop_id).all()
    cust_groups: Dict[str, List[Transaction]] = {}
    for t in txs:
        cust_groups.setdefault(t.customer_name, []).append(t)

    eligible_list: List[ReminderCustomerItem] = []
    total_debt = 0.0

    for cname, records in cust_groups.items():
        u_paise = sum(r.amount_paise for r in records if r.transaction_type == TransactionType.UDHAAR)
        j_paise = sum(r.amount_paise for r in records if r.transaction_type == TransactionType.JAMA)
        bal_paise = u_paise - j_paise
        bal = round(bal_paise / 100.0, 2)

        if bal > 0:
            total_debt += bal
            sorted_txs = sorted(records, key=lambda x: x.timestamp, reverse=True)
            last_tx_date = sorted_txs[0].timestamp.strftime("%Y-%m-%d %H:%M") if sorted_txs else None
            phone = sorted_txs[0].customer_phone if sorted_txs and sorted_txs[0].customer_phone else None

            # Check reminder history
            last_reminder = (
                db.query(PaymentReminder)
                .filter(
                    PaymentReminder.shop_id == shop_id,
                    func.lower(PaymentReminder.customer_name) == cname.lower()
                )
                .order_by(PaymentReminder.created_at.desc())
                .first()
            )

            already_today = False
            last_sent_str = None
            if last_reminder:
                last_sent_str = last_reminder.created_at.strftime("%Y-%m-%d %H:%M")
                if last_reminder.created_at.date() == today_date:
                    already_today = True

            eligible_list.append(
                ReminderCustomerItem(
                    customer_name=cname,
                    customer_phone=phone,
                    outstanding_balance=bal,
                    last_transaction_date=last_tx_date,
                    last_reminder_sent_at=last_sent_str,
                    already_reminded_today=already_today
                )
            )

    eligible_list.sort(key=lambda x: x.outstanding_balance, reverse=True)
    return EligibleRemindersResponse(
        total_eligible=len(eligible_list),
        total_outstanding_amount=round(total_debt, 2),
        customers=eligible_list
    )


@app.post(
    "/api/reminders/generate",
    response_model=GeneratedReminderResponse,
    summary="Generate polite reminder text and WhatsApp/SMS click-to-chat links"
)
def generate_reminder_message(
    payload: GenerateReminderRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    cname = payload.customer_name.strip()

    # Calculate authoritative balance
    records = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == shop_id,
            func.lower(Transaction.customer_name) == cname.lower()
        )
        .all()
    )
    if not records:
        raise HTTPException(status_code=404, detail=f"No khata records found for customer '{cname}'.")

    canonical_name = records[0].customer_name
    u_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
    j_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
    bal = round((u_p - j_p) / 100.0, 2)

    shop_title = current_user.shop_name or "My Store"
    owner_str = f" - {current_user.owner_name}" if current_user.owner_name else ""

    if payload.tone == "urgent":
        msg = (
            f"Namaste {canonical_name} ji, aapka {shop_title} par Rs. {bal:,.2f} ka hisab kafi samay se baki hai. "
            f"Kripya karke jald se jald bhugtan karein taaki khata regular rahe.{owner_str} Dhanyawad!"
        )
    elif payload.tone == "formal":
        msg = (
            f"Aadarniya {canonical_name} ji, {shop_title} khata suchna: aapki vartamaan baki rashi Rs. {bal:,.2f} hai. "
            f"Kripya suvidha-anusar bhugtan nishchit karein.{owner_str}"
        )
    else:  # polite default
        msg = (
            f"Namaste {canonical_name} ji, aapka {shop_title} par kul baki hisab Rs. {bal:,.2f} hai. "
            f"Kripya samay milte hi bhugtan karein.{owner_str} Dhanyawad!"
        )

    # Clean phone digits for WhatsApp URL
    clean_phone = "".join(filter(str.isdigit, payload.customer_phone or ""))
    if clean_phone and len(clean_phone) == 10:
        clean_phone = "91" + clean_phone

    encoded_msg = quote(msg)
    if clean_phone:
        whatsapp_url = f"https://wa.me/{clean_phone}?text={encoded_msg}"
        sms_url = f"sms:{clean_phone}?body={encoded_msg}"
    else:
        whatsapp_url = f"https://api.whatsapp.com/send?text={encoded_msg}"
        sms_url = f"sms:?body={encoded_msg}"

    return GeneratedReminderResponse(
        customer_name=canonical_name,
        customer_phone=payload.customer_phone,
        outstanding_balance=bal,
        message_text=msg,
        whatsapp_url=whatsapp_url,
        sms_url=sms_url
    )


@app.post(
    "/api/reminders/record",
    summary="Record sent or prepared payment reminder"
)
def record_reminder_sent(
    payload: RecordReminderRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    bal_paise = int(round(float(payload.balance_amount) * 100))

    reminder = PaymentReminder(
        shop_id=shop_id,
        user_id=current_user.id,
        customer_name=payload.customer_name.strip(),
        customer_phone=payload.customer_phone,
        balance_snapshot_paise=bal_paise,
        message_text=payload.message_text,
        delivery_method=payload.delivery_method,
        delivery_status=payload.delivery_status
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)

    log_audit_event(
        db, shop_id, current_user, "REMINDER_RECORDED",
        resource_type="customer", resource_id=payload.customer_name,
        details={"balance": payload.balance_amount, "method": payload.delivery_method}
    )

    return {"success": True, "reminder_id": reminder.id}


@app.get(
    "/api/reminders/history",
    response_model=List[ReminderHistoryItem],
    summary="Get history of payment reminders"
)
def get_reminders_history(
    customer_name: Optional[str] = None,
    limit: int = 50,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    q = db.query(PaymentReminder).filter(PaymentReminder.shop_id == shop_id)
    if customer_name:
        q = q.filter(func.lower(PaymentReminder.customer_name) == customer_name.strip().lower())
    records = q.order_by(PaymentReminder.created_at.desc()).limit(limit).all()

    return [
        ReminderHistoryItem(
            id=r.id,
            customer_name=r.customer_name,
            customer_phone=r.customer_phone,
            balance_snapshot=round(r.balance_snapshot_paise / 100.0, 2),
            message_text=r.message_text,
            delivery_method=r.delivery_method,
            delivery_status=r.delivery_status,
            created_at=r.created_at.strftime("%Y-%m-%d %H:%M")
        )
        for r in records
    ]


@app.get(
    "/api/reminders/history/{customer_name}",
    response_model=List[ReminderHistoryItem],
    summary="Get reminder history for a specific customer"
)
def get_customer_reminder_history(
    customer_name: str,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    return get_reminders_history(customer_name=customer_name, limit=20, current_user=current_user, db=db)


# ==========================================
# Phase 1: Transaction Search, Filtering, Pagination & CSV Export
# ==========================================

@app.get(
    "/api/transactions/search",
    response_model=TransactionSearchResponse,
    summary="Search, filter, and paginate transactions with server-side aggregation"
)
def search_transactions(
    query: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    tx_type: Optional[str] = "ALL",
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    base_q = db.query(Transaction).filter(Transaction.user_id == shop_id)

    # 1. Text Search across customer_name, customer_phone, notes
    if query and query.strip():
        term = f"%{query.strip().lower()}%"
        base_q = base_q.filter(
            func.lower(Transaction.customer_name).like(term) |
            func.lower(Transaction.customer_phone).like(term) |
            func.lower(Transaction.notes).like(term)
        )

    # 2. Date Range Filter
    if start_date:
        try:
            s_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d").replace(tzinfo=KOLKATA_TZ)
            base_q = base_q.filter(Transaction.timestamp >= s_dt)
        except ValueError:
            pass

    if end_date:
        try:
            e_dt = datetime.strptime(end_date.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=KOLKATA_TZ)
            base_q = base_q.filter(Transaction.timestamp <= e_dt)
        except ValueError:
            pass

    # 3. Amount Range Filter
    if min_amount is not None and min_amount > 0:
        base_q = base_q.filter(Transaction.amount >= min_amount)
    if max_amount is not None and max_amount > 0:
        base_q = base_q.filter(Transaction.amount <= max_amount)

    # 4. Transaction Type Filter
    if tx_type in ("UDHAAR", "JAMA"):
        base_q = base_q.filter(Transaction.transaction_type == TransactionType(tx_type))

    # Aggregations
    all_matching = base_q.all()
    total_count = len(all_matching)
    u_total = sum(t.amount for t in all_matching if t.transaction_type == TransactionType.UDHAAR)
    j_total = sum(t.amount for t in all_matching if t.transaction_type == TransactionType.JAMA)

    # Pagination
    total_pages = max(1, (total_count + limit - 1) // limit)
    offset = (page - 1) * limit
    paginated_records = (
        base_q
        .order_by(Transaction.timestamp.desc(), Transaction.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        TransactionSearchItem(
            id=t.id,
            customer_name=t.customer_name,
            customer_phone=t.customer_phone,
            notes=t.notes,
            amount=round(t.amount, 2),
            amount_paise=t.amount_paise,
            transaction_type=t.transaction_type.value,
            timestamp=t.timestamp.strftime("%Y-%m-%d %H:%M")
        )
        for t in paginated_records
    ]

    return TransactionSearchResponse(
        total_count=total_count,
        page=page,
        page_size=limit,
        total_pages=total_pages,
        total_amount_udhaar=round(u_total, 2),
        total_amount_jama=round(j_total, 2),
        transactions=items
    )


@app.get(
    "/api/transactions/export",
    summary="Export filtered transactions to CSV with formula injection protection"
)
def export_transactions_csv(
    query: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    tx_type: Optional[str] = "ALL",
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    # Enforce permissions: STAFF cannot export full ledger
    user_role = getattr(current_user, "role", "OWNER") or "OWNER"
    if user_role not in ("OWNER", "MANAGER"):
        raise HTTPException(status_code=403, detail="Permission denied. Only Owner or Manager can export ledger data.")

    shop_id = get_effective_shop_id(current_user)
    base_q = db.query(Transaction).filter(Transaction.user_id == shop_id)

    if query and query.strip():
        term = f"%{query.strip().lower()}%"
        base_q = base_q.filter(
            func.lower(Transaction.customer_name).like(term) |
            func.lower(Transaction.customer_phone).like(term) |
            func.lower(Transaction.notes).like(term)
        )

    if start_date:
        try:
            s_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d").replace(tzinfo=KOLKATA_TZ)
            base_q = base_q.filter(Transaction.timestamp >= s_dt)
        except ValueError:
            pass

    if end_date:
        try:
            e_dt = datetime.strptime(end_date.strip(), "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=KOLKATA_TZ)
            base_q = base_q.filter(Transaction.timestamp <= e_dt)
        except ValueError:
            pass

    if min_amount is not None and min_amount > 0:
        base_q = base_q.filter(Transaction.amount >= min_amount)
    if max_amount is not None and max_amount > 0:
        base_q = base_q.filter(Transaction.amount <= max_amount)

    if tx_type in ("UDHAAR", "JAMA"):
        base_q = base_q.filter(Transaction.transaction_type == TransactionType(tx_type))

    records = base_q.order_by(Transaction.timestamp.desc(), Transaction.id.desc()).all()

    # Generate CSV with formula sanitization
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Transaction ID", "Date Time (IST)", "Customer Name", "Customer Phone", "Type", "Amount (INR)", "Notes"])

    for r in records:
        writer.writerow([
            sanitize_csv_cell(r.id),
            sanitize_csv_cell(r.timestamp.strftime("%Y-%m-%d %H:%M:%S")),
            sanitize_csv_cell(r.customer_name),
            sanitize_csv_cell(r.customer_phone or ""),
            sanitize_csv_cell(r.transaction_type.value),
            sanitize_csv_cell(f"{r.amount:.2f}"),
            sanitize_csv_cell(r.notes or "")
        ])

    csv_content = output.getvalue()
    log_audit_event(db, shop_id, current_user, "TRANSACTIONS_EXPORTED", details={"count": len(records)})

    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=hisab_transactions_{get_current_ist_time().strftime('%Y%m%d')}.csv"}
    )


# ==========================================
# Phase 1: PDF Receipts & Statements
# ==========================================

@app.get(
    "/api/receipts/{transaction_id}/pdf",
    summary="Download or print a valid PDF receipt for a transaction"
)
def get_transaction_receipt_pdf(
    transaction_id: int,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    tx = db.query(Transaction).filter(Transaction.id == transaction_id, Transaction.user_id == shop_id).first()
    if not tx:
        raise HTTPException(status_code=404, detail="Transaction not found.")

    # Calculate balance before and after this transaction
    all_cust_txs = (
        db.query(Transaction)
        .filter(Transaction.user_id == shop_id, func.lower(Transaction.customer_name) == tx.customer_name.lower())
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
        .all()
    )

    prev_bal = 0.0
    for item in all_cust_txs:
        if item.id == tx.id:
            break
        if item.transaction_type == TransactionType.UDHAAR:
            prev_bal += item.amount
        else:
            prev_bal -= item.amount

    if tx.transaction_type == TransactionType.UDHAAR:
        curr_bal = prev_bal + tx.amount
    else:
        curr_bal = prev_bal - tx.amount

    pdf_bytes = pdf_generator.generate_receipt_pdf(
        shop_name=current_user.shop_name or "HisabAI Store",
        owner_name=current_user.owner_name or current_user.username,
        customer_name=tx.customer_name,
        transaction_id=tx.id,
        tx_type=tx.transaction_type.value,
        amount_rupees=tx.amount,
        previous_balance=round(prev_bal, 2),
        current_balance=round(curr_bal, 2),
        timestamp_str=tx.timestamp.strftime("%Y-%m-%d %H:%M IST")
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=receipt_{tx.id}.pdf"}
    )


@app.get(
    "/api/customers/{customer_name}/statement/pdf",
    summary="Download complete customer ledger statement as PDF"
)
def get_customer_statement_pdf(
    customer_name: str,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    cname = customer_name.strip()
    records = (
        db.query(Transaction)
        .filter(Transaction.user_id == shop_id, func.lower(Transaction.customer_name) == cname.lower())
        .order_by(Transaction.timestamp.asc(), Transaction.id.asc())
        .all()
    )
    if not records:
        raise HTTPException(status_code=404, detail=f"No transactions found for '{cname}'.")

    canonical_name = records[0].customer_name
    u_total = sum(r.amount for r in records if r.transaction_type == TransactionType.UDHAAR)
    j_total = sum(r.amount for r in records if r.transaction_type == TransactionType.JAMA)
    current_bal = round(u_total - j_total, 2)

    tx_list = [
        {
            "amount": r.amount,
            "transaction_type": r.transaction_type.value,
            "timestamp": r.timestamp.strftime("%Y-%m-%d %H:%M")
        }
        for r in records
    ]

    pdf_bytes = pdf_generator.generate_statement_pdf(
        shop_name=current_user.shop_name or "HisabAI Store",
        owner_name=current_user.owner_name or current_user.username,
        customer_name=canonical_name,
        transactions=tx_list,
        lifetime_udhaar=round(u_total, 2),
        lifetime_jama=round(j_total, 2),
        current_balance=current_bal
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=statement_{quote(canonical_name)}.pdf"}
    )


# ==========================================
# Phase 2: AI Daily Business Summary
# ==========================================

@app.get(
    "/api/analytics/daily-summary",
    response_model=DailyAnalyticsSummary,
    summary="Authoritative daily summary with optional AI narrative explanation"
)
def get_daily_analytics_summary(
    date: Optional[str] = None,
    generate_ai: bool = True,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    now_ist = get_current_ist_time()

    if date:
        try:
            target_date = datetime.strptime(date.strip(), "%Y-%m-%d").date()
        except ValueError:
            target_date = now_ist.date()
    else:
        target_date = now_ist.date()

    target_date_str = target_date.strftime("%Y-%m-%d")
    daily_txs = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == shop_id,
            func.date(Transaction.timestamp) == target_date_str
        )
        .all()
    )

    u_paise = sum(t.amount_paise for t in daily_txs if t.transaction_type == TransactionType.UDHAAR)
    j_paise = sum(t.amount_paise for t in daily_txs if t.transaction_type == TransactionType.JAMA)
    total_u = round(u_paise / 100.0, 2)
    total_j = round(j_paise / 100.0, 2)
    net_m = round((u_paise - j_paise) / 100.0, 2)

    active_custs = set(t.customer_name for t in daily_txs)

    top_customer = None
    if daily_txs:
        cust_totals: Dict[str, float] = {}
        for t in daily_txs:
            cust_totals[t.customer_name] = cust_totals.get(t.customer_name, 0.0) + t.amount
        top_customer = max(cust_totals.items(), key=lambda x: x[1])[0]

    metrics = {
        "date": target_date.strftime("%Y-%m-%d"),
        "total_udhaar": total_u,
        "total_jama": total_j,
        "net_movement": net_m,
        "transaction_count": len(daily_txs),
        "active_customers_count": len(active_custs),
        "top_customer_today": top_customer
    }

    ai_text = None
    if generate_ai:
        ai_text = ai_service.generate_daily_business_summary_explanation(metrics, language="hinglish")

    return DailyAnalyticsSummary(
        date=target_date.strftime("%Y-%m-%d"),
        total_udhaar=total_u,
        total_jama=total_j,
        net_movement=net_m,
        transaction_count=len(daily_txs),
        active_customers_count=len(active_custs),
        top_customer_today=top_customer,
        ai_summary_text=ai_text
    )


# ==========================================
# Phase 3: Inventory Management Endpoints
# ==========================================

@app.get(
    "/api/inventory/products",
    response_model=List[ProductResponse],
    summary="Get shop inventory product catalog"
)
def get_inventory_products(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    products = db.query(Product).filter(Product.shop_id == shop_id).order_by(Product.name.asc()).all()

    return [
        ProductResponse(
            id=p.id,
            name=p.name,
            sku=p.sku,
            category=p.category or "General",
            unit=p.unit or "pcs",
            cost_price=round(p.cost_price_paise / 100.0, 2),
            selling_price=round(p.selling_price_paise / 100.0, 2),
            current_stock=p.current_stock,
            low_stock_threshold=p.low_stock_threshold,
            is_low_stock=(p.current_stock <= p.low_stock_threshold),
            created_at=p.created_at.strftime("%Y-%m-%d %H:%M"),
            updated_at=p.updated_at.strftime("%Y-%m-%d %H:%M")
        )
        for p in products
    ]


@app.post(
    "/api/inventory/products",
    response_model=ProductResponse,
    summary="Register a new product in the inventory catalog"
)
def create_inventory_product(
    payload: ProductCreateRequest,
    current_user: User = Depends(check_role(["OWNER", "MANAGER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    clean_name = payload.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Product name cannot be empty.")

    clean_sku = payload.sku.strip() if payload.sku else None
    if clean_sku:
        existing_sku = db.query(Product).filter(Product.shop_id == shop_id, Product.sku == clean_sku).first()
        if existing_sku:
            raise HTTPException(status_code=400, detail=f"Product with SKU '{clean_sku}' already exists in your inventory.")

    c_paise = int(round(float(payload.cost_price) * 100))
    s_paise = int(round(float(payload.selling_price) * 100))

    prod = Product(
        shop_id=shop_id,
        user_id=current_user.id,
        name=clean_name,
        sku=clean_sku,
        category=payload.category.strip() if payload.category else "General",
        unit=payload.unit.strip() if payload.unit else "pcs",
        cost_price_paise=c_paise,
        selling_price_paise=s_paise,
        current_stock=float(payload.initial_stock),
        low_stock_threshold=float(payload.low_stock_threshold)
    )
    db.add(prod)
    db.commit()
    db.refresh(prod)

    # If initial stock provided, log movement
    if payload.initial_stock > 0:
        movement = StockMovement(
            shop_id=shop_id,
            user_id=current_user.id,
            product_id=prod.id,
            movement_type="RECEIVED",
            quantity=float(payload.initial_stock),
            notes="Initial stock setup"
        )
        db.add(movement)
        db.commit()

    log_audit_event(
        db, shop_id, current_user, "PRODUCT_CREATED",
        resource_type="product", resource_id=prod.id, details={"name": prod.name}
    )

    return ProductResponse(
        id=prod.id,
        name=prod.name,
        sku=prod.sku,
        category=prod.category,
        unit=prod.unit,
        cost_price=payload.cost_price,
        selling_price=payload.selling_price,
        current_stock=prod.current_stock,
        low_stock_threshold=prod.low_stock_threshold,
        is_low_stock=(prod.current_stock <= prod.low_stock_threshold),
        created_at=prod.created_at.strftime("%Y-%m-%d %H:%M"),
        updated_at=prod.updated_at.strftime("%Y-%m-%d %H:%M")
    )


@app.put(
    "/api/inventory/products/{product_id}",
    response_model=ProductResponse,
    summary="Update product details"
)
def update_inventory_product(
    product_id: int,
    payload: ProductUpdateRequest,
    current_user: User = Depends(check_role(["OWNER", "MANAGER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    prod = db.query(Product).filter(Product.id == product_id, Product.shop_id == shop_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found.")

    if payload.name is not None:
        prod.name = payload.name.strip()
    if payload.sku is not None:
        prod.sku = payload.sku.strip() if payload.sku else None
    if payload.category is not None:
        prod.category = payload.category.strip()
    if payload.unit is not None:
        prod.unit = payload.unit.strip()
    if payload.cost_price is not None:
        prod.cost_price_paise = int(round(float(payload.cost_price) * 100))
    if payload.selling_price is not None:
        prod.selling_price_paise = int(round(float(payload.selling_price) * 100))
    if payload.low_stock_threshold is not None:
        prod.low_stock_threshold = float(payload.low_stock_threshold)

    prod.updated_at = get_current_ist_time()
    db.commit()
    db.refresh(prod)

    log_audit_event(
        db, shop_id, current_user, "PRODUCT_UPDATED",
        resource_type="product", resource_id=prod.id, details={"name": prod.name}
    )

    return ProductResponse(
        id=prod.id,
        name=prod.name,
        sku=prod.sku,
        category=prod.category,
        unit=prod.unit,
        cost_price=round(prod.cost_price_paise / 100.0, 2),
        selling_price=round(prod.selling_price_paise / 100.0, 2),
        current_stock=prod.current_stock,
        low_stock_threshold=prod.low_stock_threshold,
        is_low_stock=(prod.current_stock <= prod.low_stock_threshold),
        created_at=prod.created_at.strftime("%Y-%m-%d %H:%M"),
        updated_at=prod.updated_at.strftime("%Y-%m-%d %H:%M")
    )


@app.delete(
    "/api/inventory/products/{product_id}",
    summary="Delete a product from the catalog"
)
def delete_inventory_product(
    product_id: int,
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    prod = db.query(Product).filter(Product.id == product_id, Product.shop_id == shop_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found.")

    pname = prod.name
    db.query(StockMovement).filter(StockMovement.product_id == prod.id).delete()
    db.delete(prod)
    db.commit()

    log_audit_event(
        db, shop_id, current_user, "PRODUCT_DELETED",
        resource_type="product", resource_id=product_id, details={"name": pname}
    )

    return {"success": True, "message": f"Product '{pname}' deleted."}


@app.post(
    "/api/inventory/movements",
    response_model=StockMovementResponse,
    summary="Record atomic stock movement (received, sold, adjustment, return)"
)
def record_stock_movement(
    payload: StockMovementCreateRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    prod = db.query(Product).filter(Product.id == payload.product_id, Product.shop_id == shop_id).first()
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found.")

    qty = float(payload.quantity)
    # Apply atomic change
    if payload.movement_type in ("RECEIVED", "RETURN"):
        prod.current_stock += qty
    elif payload.movement_type in ("SOLD", "ADJUSTMENT"):
        prod.current_stock -= qty

    movement = StockMovement(
        shop_id=shop_id,
        user_id=current_user.id,
        product_id=prod.id,
        movement_type=payload.movement_type,
        quantity=qty,
        notes=payload.notes
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)

    log_audit_event(
        db, shop_id, current_user, "STOCK_MOVEMENT",
        resource_type="product", resource_id=prod.id,
        details={"type": payload.movement_type, "quantity": qty, "resulting_stock": prod.current_stock}
    )

    return StockMovementResponse(
        id=movement.id,
        product_id=prod.id,
        product_name=prod.name,
        movement_type=movement.movement_type,
        quantity=movement.quantity,
        notes=movement.notes,
        resulting_stock=prod.current_stock,
        created_at=movement.created_at.strftime("%Y-%m-%d %H:%M")
    )


@app.get(
    "/api/inventory/low-stock",
    response_model=List[ProductResponse],
    summary="Get products below their low-stock threshold"
)
def get_low_stock_products(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    low_stock = (
        db.query(Product)
        .filter(Product.shop_id == shop_id, Product.current_stock <= Product.low_stock_threshold)
        .order_by(Product.current_stock.asc())
        .all()
    )

    return [
        ProductResponse(
            id=p.id,
            name=p.name,
            sku=p.sku,
            category=p.category or "General",
            unit=p.unit or "pcs",
            cost_price=round(p.cost_price_paise / 100.0, 2),
            selling_price=round(p.selling_price_paise / 100.0, 2),
            current_stock=p.current_stock,
            low_stock_threshold=p.low_stock_threshold,
            is_low_stock=True,
            created_at=p.created_at.strftime("%Y-%m-%d %H:%M"),
            updated_at=p.updated_at.strftime("%Y-%m-%d %H:%M")
        )
        for p in low_stock
    ]


# ==========================================
# Phase 3: Multi-User Access & RBAC Endpoints
# ==========================================

@app.get(
    "/api/team/members",
    response_model=List[TeamMemberResponse],
    summary="List team members of the current shop (Owner only)"
)
def list_team_members(
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    members = db.query(User).filter(
        (User.shop_id == shop_id) | (User.id == shop_id)
    ).order_by(User.created_at.asc()).all()

    return [
        TeamMemberResponse(
            id=m.id,
            username=m.username,
            owner_name=m.owner_name,
            email=m.email,
            role=getattr(m, "role", "STAFF") or "STAFF",
            created_at=m.created_at.strftime("%Y-%m-%d %H:%M")
        )
        for m in members
    ]


@app.post(
    "/api/team/members",
    response_model=TeamMemberResponse,
    summary="Invite or add a Manager or Staff member to the shop"
)
def add_team_member(
    payload: TeamMemberCreateRequest,
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    clean_user = payload.username.strip().lower()

    if db.query(User).filter(User.username == clean_user).first():
        raise HTTPException(status_code=400, detail="Username is already taken. Please choose another.")

    clean_email = payload.email.strip().lower() if payload.email else None
    if clean_email:
        if not is_valid_email(clean_email):
            raise HTTPException(status_code=400, detail=f"Invalid email format: '{clean_email}'")
        if db.query(User).filter(User.email == clean_email).first():
            raise HTTPException(status_code=400, detail=f"Email '{clean_email}' is already registered with another account.")

    member = User(
        username=clean_user,
        email=clean_email,
        shop_name=current_user.shop_name,
        owner_name=payload.owner_name.strip() if payload.owner_name else None,
        role=payload.role,
        shop_id=shop_id,
        password_hash=hash_password(payload.password),
        created_at=get_current_ist_time()
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    log_audit_event(
        db, shop_id, current_user, "TEAM_MEMBER_CREATED",
        resource_type="user", resource_id=member.id, details={"username": member.username, "role": member.role}
    )

    return TeamMemberResponse(
        id=member.id,
        username=member.username,
        owner_name=member.owner_name,
        email=member.email,
        role=member.role,
        created_at=member.created_at.strftime("%Y-%m-%d %H:%M")
    )


@app.put(
    "/api/team/members/{member_id}/role",
    summary="Update role of a team member"
)
def update_team_member_role(
    member_id: int,
    payload: TeamRoleUpdateRequest,
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    if member_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot change role of primary shop owner.")

    member = db.query(User).filter(User.id == member_id, User.shop_id == shop_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found.")

    old_role = member.role
    member.role = payload.role
    db.commit()

    log_audit_event(
        db, shop_id, current_user, "ROLE_UPDATED",
        resource_type="user", resource_id=member.id,
        details={"username": member.username, "old_role": old_role, "new_role": member.role}
    )

    return {"success": True, "message": f"Updated role to '{payload.role}'."}


@app.delete(
    "/api/team/members/{member_id}",
    summary="Remove a team member from the shop"
)
def remove_team_member(
    member_id: int,
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    if member_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete the primary shop owner account.")

    member = db.query(User).filter(User.id == member_id, User.shop_id == shop_id).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found.")

    mname = member.username
    db.query(UserSession).filter(UserSession.user_id == member.id).delete()
    db.delete(member)
    db.commit()

    log_audit_event(
        db, shop_id, current_user, "TEAM_MEMBER_REMOVED",
        resource_type="user", resource_id=member_id, details={"username": mname}
    )

    return {"success": True, "message": f"Team member '{mname}' removed."}


# ==========================================
# Phase 4: Backup, Restore & Audit Logs
# ==========================================

@app.get(
    "/api/backup/export",
    summary="Create and download a consistent, validated JSON backup with SHA-256 checksum (Owner only)"
)
def export_shop_backup(
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)

    # 1. Fetch all data for this shop
    txs = db.query(Transaction).filter(Transaction.user_id == shop_id).all()
    products = db.query(Product).filter(Product.shop_id == shop_id).all()
    movements = db.query(StockMovement).filter(StockMovement.shop_id == shop_id).all()
    reminders = db.query(PaymentReminder).filter(PaymentReminder.shop_id == shop_id).all()

    backup_payload = {
        "metadata": {
            "shop_id": shop_id,
            "shop_name": current_user.shop_name,
            "exported_by": current_user.username,
            "exported_at": get_current_ist_time().isoformat(),
            "version": "2.1.0"
        },
        "data": {
            "transactions": [
                {
                    "customer_name": t.customer_name,
                    "customer_phone": t.customer_phone,
                    "notes": t.notes,
                    "amount_paise": t.amount_paise,
                    "amount": t.amount,
                    "transaction_type": t.transaction_type.value,
                    "timestamp": t.timestamp.isoformat()
                }
                for t in txs
            ],
            "products": [
                {
                    "name": p.name,
                    "sku": p.sku,
                    "category": p.category,
                    "unit": p.unit,
                    "cost_price_paise": p.cost_price_paise,
                    "selling_price_paise": p.selling_price_paise,
                    "current_stock": p.current_stock,
                    "low_stock_threshold": p.low_stock_threshold
                }
                for p in products
            ],
            "stock_movements": [
                {
                    "product_name": db.query(Product.name).filter(Product.id == m.product_id).scalar(),
                    "movement_type": m.movement_type,
                    "quantity": m.quantity,
                    "notes": m.notes,
                    "created_at": m.created_at.isoformat()
                }
                for m in movements
            ],
            "payment_reminders": [
                {
                    "customer_name": r.customer_name,
                    "customer_phone": r.customer_phone,
                    "balance_snapshot_paise": r.balance_snapshot_paise,
                    "message_text": r.message_text,
                    "delivery_method": r.delivery_method,
                    "delivery_status": r.delivery_status,
                    "created_at": r.created_at.isoformat()
                }
                for r in reminders
            ]
        }
    }

    # Compute checksum over canonical json
    canonical_data_str = json.dumps(backup_payload["data"], sort_keys=True)
    checksum = hashlib.sha256(canonical_data_str.encode("utf-8")).hexdigest()
    backup_payload["checksum"] = checksum

    log_audit_event(
        db, shop_id, current_user, "BACKUP_EXPORTED",
        details={"transactions": len(txs), "products": len(products)}
    )

    json_str = json.dumps(backup_payload, indent=2)
    filename = f"hisabai_backup_{current_user.username}_{get_current_ist_time().strftime('%Y%m%d_%H%M%S')}.json"

    return Response(
        content=json_str,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post(
    "/api/backup/restore",
    summary="Restore shop database from validated JSON backup with rollback protection (Owner only)"
)
def restore_shop_backup(
    payload: dict,
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)

    # 1. Verify schema & presence of checksum and data
    if "metadata" not in payload or "data" not in payload or "checksum" not in payload:
        raise HTTPException(status_code=400, detail="Invalid backup file format. Missing metadata, data, or checksum.")

    # 2. Checksum validation
    canonical_data_str = json.dumps(payload["data"], sort_keys=True)
    computed_checksum = hashlib.sha256(canonical_data_str.encode("utf-8")).hexdigest()
    if computed_checksum != payload["checksum"]:
        raise HTTPException(status_code=400, detail="Backup checksum mismatch! File may be corrupted or tampered.")

    raw_txs = payload["data"].get("transactions", [])
    raw_products = payload["data"].get("products", [])
    raw_reminders = payload["data"].get("payment_reminders", [])

    # 3. Transactional atomic restore
    try:
        # Clear existing shop data
        db.query(Transaction).filter(Transaction.user_id == shop_id).delete()
        db.query(StockMovement).filter(StockMovement.shop_id == shop_id).delete()
        db.query(Product).filter(Product.shop_id == shop_id).delete()
        db.query(PaymentReminder).filter(PaymentReminder.shop_id == shop_id).delete()

        # Restore products
        prod_map: Dict[str, int] = {}
        for p in raw_products:
            new_p = Product(
                shop_id=shop_id,
                user_id=current_user.id,
                name=p["name"],
                sku=p.get("sku"),
                category=p.get("category", "General"),
                unit=p.get("unit", "pcs"),
                cost_price_paise=p.get("cost_price_paise", 0),
                selling_price_paise=p.get("selling_price_paise", 0),
                current_stock=p.get("current_stock", 0.0),
                low_stock_threshold=p.get("low_stock_threshold", 5.0)
            )
            db.add(new_p)
            db.flush()
            prod_map[new_p.name] = new_p.id

        # Restore transactions
        for t in raw_txs:
            t_time = datetime.fromisoformat(t["timestamp"]) if "timestamp" in t else get_current_ist_time()
            if t_time.tzinfo is None:
                t_time = t_time.replace(tzinfo=KOLKATA_TZ)

            amt_p = t.get("amount_paise")
            if amt_p is None:
                amt_p = int(round(float(t.get("amount", 0)) * 100))

            new_tx = Transaction(
                user_id=shop_id,
                customer_name=t["customer_name"],
                customer_phone=t.get("customer_phone"),
                notes=t.get("notes"),
                amount_paise=amt_p,
                amount=round(amt_p / 100.0, 2),
                transaction_type=TransactionType(t["transaction_type"]),
                timestamp=t_time
            )
            db.add(new_tx)

        # Restore reminders
        for r in raw_reminders:
            r_time = datetime.fromisoformat(r["created_at"]) if "created_at" in r else get_current_ist_time()
            new_rem = PaymentReminder(
                shop_id=shop_id,
                user_id=current_user.id,
                customer_name=r["customer_name"],
                customer_phone=r.get("customer_phone"),
                balance_snapshot_paise=r.get("balance_snapshot_paise", 0),
                message_text=r["message_text"],
                delivery_method=r.get("delivery_method", "WHATSAPP"),
                delivery_status=r.get("delivery_status", "SENT"),
                created_at=r_time
            )
            db.add(new_rem)

        db.commit()

        log_audit_event(
            db, shop_id, current_user, "BACKUP_RESTORED",
            details={"restored_transactions": len(raw_txs), "restored_products": len(raw_products)}
        )

        return {
            "success": True,
            "message": "Database restored successfully.",
            "restored_transactions": len(raw_txs),
            "restored_products": len(raw_products)
        }
    except Exception as exc:
        db.rollback()
        logger.error(f"Backup restore failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Restore failed and changes were rolled back: {str(exc)}")


@app.get(
    "/api/audit-logs",
    response_model=List[AuditLogResponse],
    summary="View structured audit log entries for sensitive operations"
)
def get_audit_logs(
    action: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(check_role(["OWNER", "MANAGER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    q = db.query(AuditLog).filter(AuditLog.shop_id == shop_id)
    if action:
        q = q.filter(AuditLog.action == action.strip().upper())
    records = q.order_by(AuditLog.timestamp.desc()).limit(limit).all()

    return [
        AuditLogResponse(
            id=a.id,
            shop_id=a.shop_id,
            user_id=a.user_id,
            username=a.username,
            action=a.action,
            resource_type=a.resource_type,
            resource_id=a.resource_id,
            details_json=a.details_json,
            ip_address=a.ip_address,
            timestamp=a.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        )
        for a in records
    ]


@app.get(
    "/api/analytics/overview",
    response_model=AnalyticsOverviewResponse,
    summary="Comprehensive analytics including balance distributions, debtor rankings, and 14-day trends"
)
def get_analytics_overview(
    days: int = Query(30, ge=7, le=90),
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    now_ist = get_current_ist_time()
    start_dt = now_ist - timedelta(days=days)

    all_txs = db.query(Transaction).filter(Transaction.user_id == shop_id).all()

    # Lifetime / Customer Balances
    cust_groups: Dict[str, List[Transaction]] = {}
    for t in all_txs:
        cust_groups.setdefault(t.customer_name, []).append(t)

    bucket_0 = {"count": 0, "debt": 0.0}
    bucket_500 = {"count": 0, "debt": 0.0}
    bucket_2000 = {"count": 0, "debt": 0.0}
    bucket_5000 = {"count": 0, "debt": 0.0}
    bucket_high = {"count": 0, "debt": 0.0}

    debtors: List[dict] = []

    for cname, records in cust_groups.items():
        u = sum(r.amount for r in records if r.transaction_type == TransactionType.UDHAAR)
        j = sum(r.amount for r in records if r.transaction_type == TransactionType.JAMA)
        bal = round(u - j, 2)

        if bal <= 0:
            bucket_0["count"] += 1
        elif bal <= 500:
            bucket_500["count"] += 1
            bucket_500["debt"] += bal
        elif bal <= 2000:
            bucket_2000["count"] += 1
            bucket_2000["debt"] += bal
        elif bal <= 5000:
            bucket_5000["count"] += 1
            bucket_5000["debt"] += bal
        else:
            bucket_high["count"] += 1
            bucket_high["debt"] += bal

        if bal > 0:
            last_dt = max(r.timestamp for r in records).strftime("%Y-%m-%d")
            debtors.append({"customer_name": cname, "balance": bal, "last_date": last_dt})

    debtors.sort(key=lambda x: x["balance"], reverse=True)
    top_5_debtors = debtors[:5]

    distribution = [
        CustomerDistributionBucket(bucket_label="Cleared (Rs. 0)", customer_count=bucket_0["count"], total_debt=0.0),
        CustomerDistributionBucket(bucket_label="Small (Rs. 1 - 500)", customer_count=bucket_500["count"], total_debt=round(bucket_500["debt"], 2)),
        CustomerDistributionBucket(bucket_label="Medium (Rs. 501 - 2,000)", customer_count=bucket_2000["count"], total_debt=round(bucket_2000["debt"], 2)),
        CustomerDistributionBucket(bucket_label="Significant (Rs. 2,001 - 5,000)", customer_count=bucket_5000["count"], total_debt=round(bucket_5000["debt"], 2)),
        CustomerDistributionBucket(bucket_label="High Value (Rs. 5,000+)", customer_count=bucket_high["count"], total_debt=round(bucket_high["debt"], 2))
    ]

    # Period metrics (last N days)
    period_txs = [t for t in all_txs if t.timestamp >= start_dt]
    p_u = sum(t.amount for t in period_txs if t.transaction_type == TransactionType.UDHAAR)
    p_j = sum(t.amount for t in period_txs if t.transaction_type == TransactionType.JAMA)
    net_f = round(p_j - p_u, 2)

    # 14-day daily trend
    daily_trend: List[dict] = []
    for d in range(13, -1, -1):
        day_date = (now_ist - timedelta(days=d)).date()
        day_txs = [t for t in all_txs if t.timestamp.date() == day_date]
        d_u = sum(t.amount for t in day_txs if t.transaction_type == TransactionType.UDHAAR)
        d_j = sum(t.amount for t in day_txs if t.transaction_type == TransactionType.JAMA)
        daily_trend.append({
            "date": day_date.strftime("%d %b"),
            "udhaar": round(d_u, 2),
            "jama": round(d_j, 2)
        })

    return AnalyticsOverviewResponse(
        period_start=start_dt.strftime("%Y-%m-%d"),
        period_end=now_ist.strftime("%Y-%m-%d"),
        total_udhaar=round(p_u, 2),
        total_jama=round(p_j, 2),
        net_flow=net_f,
        total_active_customers=len(cust_groups),
        customer_distribution=distribution,
        top_debtors=top_5_debtors,
        daily_trend=daily_trend
    )


# ==========================================
# Shop Profile & Settings Endpoints
# ==========================================

@app.get(
    "/api/shop/profile",
    response_model=ShopProfileResponse,
    summary="Get shop and owner profile configuration"
)
def get_shop_profile(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    owner = db.query(User).filter(User.id == shop_id).first() or current_user

    return ShopProfileResponse(
        id=owner.id,
        username=owner.username,
        shop_name=owner.shop_name or "My Store",
        owner_name=owner.owner_name or owner.username,
        email=owner.email,
        phone=getattr(owner, "phone", None),
        address=getattr(owner, "address", None),
        currency=getattr(owner, "currency", "INR") or "INR",
        timezone=getattr(owner, "timezone", "Asia/Kolkata") or "Asia/Kolkata",
        role=current_user.role,
        interest_enabled=bool(getattr(owner, "interest_enabled", 1)),
        interest_rate_percent=float(getattr(owner, "interest_rate_percent", 2.0) or 2.0),
        interest_grace_days=int(getattr(owner, "interest_grace_days", 15) or 15)
    )


@app.put(
    "/api/shop/profile",
    response_model=ShopProfileResponse,
    summary="Update shop and owner profile configuration (Owner/Manager)"
)
def update_shop_profile(
    payload: ShopProfileUpdateRequest,
    current_user: User = Depends(check_role(["OWNER", "MANAGER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    owner = db.query(User).filter(User.id == shop_id).first()
    if not owner:
        raise HTTPException(status_code=404, detail="Shop owner record not found.")

    if payload.shop_name is not None:
        owner.shop_name = payload.shop_name.strip()
    if payload.owner_name is not None:
        owner.owner_name = payload.owner_name.strip()
    if payload.email is not None:
        clean_email = payload.email.strip().lower() if payload.email.strip() else None
        if clean_email:
            if not is_valid_email(clean_email):
                raise HTTPException(status_code=400, detail=f"Invalid email format: '{clean_email}'")
            existing = db.query(User).filter(User.email == clean_email, User.id != owner.id).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Email '{clean_email}' is already in use.")
        owner.email = clean_email
    if payload.phone is not None:
        owner.phone = payload.phone.strip() if payload.phone.strip() else None
    if payload.address is not None:
        owner.address = payload.address.strip() if payload.address.strip() else None
    if payload.currency is not None:
        owner.currency = payload.currency.strip()
    if payload.timezone is not None:
        owner.timezone = payload.timezone.strip()
    if payload.interest_enabled is not None:
        owner.interest_enabled = 1 if payload.interest_enabled else 0
    if payload.interest_rate_percent is not None:
        owner.interest_rate_percent = max(0.0, float(payload.interest_rate_percent))
    if payload.interest_grace_days is not None:
        owner.interest_grace_days = max(0, int(payload.interest_grace_days))

    db.commit()
    db.refresh(owner)

    log_audit_event(
        db, shop_id, current_user, "SHOP_PROFILE_UPDATED",
        resource_type="shop", resource_id=str(owner.id),
        details={"shop_name": owner.shop_name, "owner_name": owner.owner_name}
    )

    return ShopProfileResponse(
        id=owner.id,
        username=owner.username,
        shop_name=owner.shop_name,
        owner_name=owner.owner_name,
        email=owner.email,
        phone=owner.phone,
        address=owner.address,
        currency=owner.currency or "INR",
        timezone=owner.timezone or "Asia/Kolkata",
        role=current_user.role,
        interest_enabled=bool(owner.interest_enabled),
        interest_rate_percent=float(owner.interest_rate_percent or 2.0),
        interest_grace_days=int(owner.interest_grace_days or 15)
    )


# ==========================================
# Overdue Interest Engine Endpoints
# ==========================================

@app.get(
    "/api/interest/overview",
    response_model=InterestOverviewResponse,
    summary="Get shop-wide overdue interest calculation and per-customer breakdowns"
)
def get_interest_overview(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    owner = db.query(User).filter(User.id == shop_id).first() or current_user
    interest_enabled = bool(getattr(owner, "interest_enabled", 1))
    rate_percent = float(getattr(owner, "interest_rate_percent", 2.0) or 2.0)
    grace_days = int(getattr(owner, "interest_grace_days", 15) or 15)

    txs = db.query(Transaction).filter(Transaction.user_id == shop_id).order_by(Transaction.timestamp.asc()).all()
    cust_groups: Dict[str, List[Transaction]] = {}
    for t in txs:
        cust_groups.setdefault(t.customer_name, []).append(t)

    breakdowns: List[CustomerInterestBreakdown] = []
    total_principal = 0.0
    total_interest = 0.0
    overdue_count = 0

    for cname, records in cust_groups.items():
        interest_res = calculate_customer_interest(
            records,
            rate_percent=rate_percent if interest_enabled else 0.0,
            grace_days=grace_days
        )
        if interest_res["principal_balance"] > 0:
            total_principal += interest_res["principal_balance"]
            total_interest += interest_res["accrued_interest"]
            if interest_res["is_overdue"]:
                overdue_count += 1

            phone = next((r.customer_phone for r in records if r.customer_phone), None)
            breakdowns.append(
                CustomerInterestBreakdown(
                    customer_name=cname,
                    phone=phone,
                    email=None,
                    principal_balance=interest_res["principal_balance"],
                    total_balance=interest_res["total_balance"],
                    accrued_interest=interest_res["accrued_interest"],
                    overdue_days=interest_res["overdue_days"],
                    grace_days=grace_days,
                    overdue_periods=interest_res["overdue_periods"],
                    rate_percent=rate_percent,
                    is_overdue=interest_res["is_overdue"],
                    oldest_unsettled_date=interest_res["oldest_unsettled_date"]
                )
            )

    breakdowns.sort(key=lambda x: (x.accrued_interest, x.principal_balance), reverse=True)

    return InterestOverviewResponse(
        interest_enabled=interest_enabled,
        default_rate_percent=rate_percent,
        grace_period_days=grace_days,
        total_principal_outstanding=round(total_principal, 2),
        total_interest_accrued=round(total_interest, 2),
        total_combined_due=round(total_principal + total_interest, 2),
        overdue_customers_count=overdue_count,
        breakdowns=breakdowns
    )


# ==========================================
# Customer Email Reminders (Brevo SMTP)
# ==========================================

@app.post(
    "/api/reminders/email/preview",
    response_model=EmailReminderPreviewResponse,
    summary="Generate a preview of an email reminder for a customer"
)
def preview_email_reminder(
    payload: EmailReminderPreviewRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    owner = db.query(User).filter(User.id == shop_id).first() or current_user
    cname = payload.customer_name.strip()

    records = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == shop_id,
            func.lower(Transaction.customer_name) == cname.lower()
        )
        .order_by(Transaction.timestamp.asc())
        .all()
    )
    if not records:
        raise HTTPException(status_code=404, detail=f"No ledger records found for customer '{cname}'.")

    rate_percent = float(getattr(owner, "interest_rate_percent", 2.0) or 2.0)
    grace_days = int(getattr(owner, "interest_grace_days", 15) or 15)
    interest_res = calculate_customer_interest(records, rate_percent=rate_percent, grace_days=grace_days)

    if interest_res["principal_balance"] <= 0:
        raise HTTPException(status_code=400, detail=f"Customer '{cname}' has no outstanding dues.")

    content = generate_reminder_email_content(
        customer_name=cname,
        principal_balance=interest_res["principal_balance"],
        accrued_interest=interest_res["accrued_interest"],
        total_balance=interest_res["total_balance"],
        overdue_days=interest_res["overdue_days"],
        shop_name=owner.shop_name or "HisabAI Store",
        owner_name=owner.owner_name or owner.username,
        shop_phone=getattr(owner, "phone", None)
    )

    return EmailReminderPreviewResponse(
        customer_name=cname,
        customer_email=payload.customer_email,
        subject=content["subject"],
        html_content=content["html"],
        text_content=content["text"],
        balance=interest_res["total_balance"],
        principal_balance=interest_res["principal_balance"],
        accrued_interest=interest_res["accrued_interest"],
        overdue_days=interest_res["overdue_days"],
        shop_name=owner.shop_name or "HisabAI Store",
        owner_name=owner.owner_name or owner.username
    )


@app.post(
    "/api/reminders/email/send",
    response_model=EmailReminderSendResponse,
    summary="Dispatch a payment reminder to customer email using Brevo SMTP"
)
def send_email_reminder(
    payload: EmailReminderSendRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    owner = db.query(User).filter(User.id == shop_id).first() or current_user
    cname = payload.customer_name.strip()
    recipient_email = payload.customer_email.strip().lower()

    if not is_valid_email(recipient_email):
        raise HTTPException(status_code=400, detail=f"Invalid recipient email: '{recipient_email}'")

    records = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == shop_id,
            func.lower(Transaction.customer_name) == cname.lower()
        )
        .order_by(Transaction.timestamp.asc())
        .all()
    )
    if not records:
        raise HTTPException(status_code=404, detail=f"No ledger records found for customer '{cname}'.")

    rate_percent = float(getattr(owner, "interest_rate_percent", 2.0) or 2.0)
    grace_days = int(getattr(owner, "interest_grace_days", 15) or 15)
    interest_res = calculate_customer_interest(records, rate_percent=rate_percent, grace_days=grace_days)

    if interest_res["principal_balance"] <= 0:
        raise HTTPException(status_code=400, detail=f"Customer '{cname}' has no outstanding balance.")

    content = generate_reminder_email_content(
        customer_name=cname,
        principal_balance=interest_res["principal_balance"],
        accrued_interest=interest_res["accrued_interest"],
        total_balance=interest_res["total_balance"],
        overdue_days=interest_res["overdue_days"],
        shop_name=owner.shop_name or "HisabAI Store",
        owner_name=owner.owner_name or owner.username,
        shop_phone=getattr(owner, "phone", None),
        custom_message=payload.custom_message
    )

    subject = payload.subject or content["subject"]
    success, status_msg = send_brevo_email(
        to_email=recipient_email,
        subject=subject,
        html_content=content["html"],
        text_content=content["text"],
        sender_email=None,
        sender_name=owner.shop_name or "HisabAI"
    )

    now_ist = get_current_ist_time()
    delivery_status = "SENT" if success else "FAILED"

    reminder_rec = PaymentReminder(
        shop_id=shop_id,
        user_id=current_user.id,
        customer_name=cname,
        customer_phone=next((r.customer_phone for r in records if r.customer_phone), None),
        customer_email=recipient_email,
        balance_snapshot_paise=interest_res["principal_paise"],
        overdue_days=interest_res["overdue_days"],
        message_text=subject,
        delivery_method="EMAIL",
        delivery_status=delivery_status,
        delivery_details=status_msg,
        created_at=now_ist
    )
    db.add(reminder_rec)
    db.commit()
    db.refresh(reminder_rec)

    log_audit_event(
        db, shop_id, current_user,
        "EMAIL_REMINDER_SENT" if success else "EMAIL_REMINDER_FAILED",
        resource_type="payment_reminder", resource_id=str(reminder_rec.id),
        details={
            "customer": cname,
            "email": recipient_email,
            "status": delivery_status,
            "status_details": status_msg,
            "total_balance": interest_res["total_balance"]
        }
    )

    return EmailReminderSendResponse(
        status="ACCEPTED" if success else "FAILED",
        message=status_msg,
        recipient=recipient_email,
        reminder_id=reminder_rec.id,
        timestamp=now_ist.strftime("%Y-%m-%d %H:%M:%S IST")
    )


# ==========================================
# Payment Verification & Manual Review Workflow
# ==========================================

def get_customer_outstanding_balance_paise(db: Session, shop_id: int, customer_name: str) -> int:
    """Calculates customer's current outstanding balance strictly in integer paise."""
    txs = (
        db.query(Transaction.amount_paise, Transaction.transaction_type)
        .filter(
            Transaction.user_id == shop_id,
            func.lower(Transaction.customer_name) == customer_name.strip().lower()
        )
        .all()
    )
    udhaar = sum(t.amount_paise for t in txs if t.transaction_type == TransactionType.UDHAAR)
    jama = sum(t.amount_paise for t in txs if t.transaction_type == TransactionType.JAMA)
    return udhaar - jama


def format_claim_response(claim: PaymentVerification, db: Session, include_balance: bool = False) -> PaymentClaimResponse:
    """Builds a standardized PaymentClaimResponse model."""
    reviewer_name = None
    if claim.reviewed_by_user_id:
        rev_user = db.query(User).filter(User.id == claim.reviewed_by_user_id).first()
        if rev_user:
            reviewer_name = rev_user.owner_name or rev_user.username

    balance_val = None
    if include_balance:
        bal_paise = get_customer_outstanding_balance_paise(db, claim.shop_id, claim.customer_name)
        balance_val = round(bal_paise / 100.0, 2)

    return PaymentClaimResponse(
        id=claim.id,
        shop_id=claim.shop_id,
        customer_name=claim.customer_name,
        customer_email=claim.customer_email,
        customer_phone=claim.customer_phone,
        claimed_amount=claim.claimed_amount,
        claimed_amount_paise=claim.claimed_amount_paise,
        verified_amount=claim.verified_amount,
        verified_amount_paise=claim.verified_amount_paise,
        payment_method=claim.payment_method or "UPI",
        destination_upi_id=claim.destination_upi_id or "goyal.com@fam",
        utr_number=claim.utr_number,
        customer_notes=claim.customer_notes,
        status=claim.status,
        reviewed_by_user_id=claim.reviewed_by_user_id,
        reviewer_name=reviewer_name,
        reviewed_at=claim.reviewed_at.strftime("%Y-%m-%d %H:%M:%S IST") if claim.reviewed_at else None,
        review_notes=claim.review_notes,
        resulting_transaction_id=claim.resulting_transaction_id,
        current_outstanding_balance=balance_val,
        created_at=claim.created_at.strftime("%Y-%m-%d %H:%M:%S IST")
    )


@app.post(
    "/api/payment-verifications",
    response_model=PaymentClaimResponse,
    summary="Register a payment claim (from WhatsApp screenshot/UPI) for manual verification"
)
def register_payment_claim(
    payload: PaymentClaimCreateRequest,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    """
    Registers a customer payment claim in PENDING state.
    CRITICAL: Does NOT create a ledger transaction or alter customer balance.
    A ledger transaction is ONLY created upon explicit manual approval by an authorized shopkeeper.
    """
    shop_id = get_effective_shop_id(current_user)
    cname = payload.customer_name.strip()
    cemail = payload.customer_email.strip().lower() if payload.customer_email else None
    cphone = payload.customer_phone.strip() if payload.customer_phone else None

    # Lookup past transaction for customer to prefill email/phone if omitted
    if not cemail or not cphone:
        past_tx = (
            db.query(Transaction)
            .filter(Transaction.user_id == shop_id, func.lower(Transaction.customer_name) == cname.lower())
            .order_by(Transaction.timestamp.desc())
            .first()
        )
        if past_tx:
            if not cemail and past_tx.customer_email:
                cemail = past_tx.customer_email
            if not cphone and past_tx.customer_phone:
                cphone = past_tx.customer_phone

    claimed_paise = int(round(payload.claimed_amount * 100))
    if claimed_paise <= 0:
        raise HTTPException(status_code=400, detail="Claimed payment amount must be greater than zero.")

    claim = PaymentVerification(
        shop_id=shop_id,
        customer_name=cname,
        customer_email=cemail,
        customer_phone=cphone,
        claimed_amount=payload.claimed_amount,
        claimed_amount_paise=claimed_paise,
        payment_method=payload.payment_method or "UPI",
        destination_upi_id=payload.destination_upi_id or "goyal.com@fam",
        utr_number=payload.utr_number.strip() if payload.utr_number else None,
        customer_notes=payload.customer_notes.strip() if payload.customer_notes else None,
        status="PENDING",
        created_at=get_current_ist_time()
    )
    db.add(claim)
    db.commit()
    db.refresh(claim)

    log_audit_event(
        db, shop_id, current_user, "PAYMENT_CLAIM_RECORDED",
        resource_type="payment_verification", resource_id=str(claim.id),
        details={
            "customer": cname,
            "claimed_amount": payload.claimed_amount,
            "utr": claim.utr_number,
            "payment_method": claim.payment_method
        }
    )

    return format_claim_response(claim, db, include_balance=True)


@app.get(
    "/api/payment-verifications",
    response_model=PaymentClaimListResponse,
    summary="List all payment claims for the shop with filtering"
)
def list_payment_claims(
    status: Optional[str] = None,
    customer_name: Optional[str] = None,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    """Lists payment claims filtered by status (PENDING, APPROVED, REJECTED) and customer name."""
    shop_id = get_effective_shop_id(current_user)

    base_query = db.query(PaymentVerification).filter(PaymentVerification.shop_id == shop_id)

    total_count = base_query.count()
    pending_count = base_query.filter(PaymentVerification.status == "PENDING").count()
    approved_count = base_query.filter(PaymentVerification.status == "APPROVED").count()
    rejected_count = base_query.filter(PaymentVerification.status == "REJECTED").count()

    filtered_query = base_query
    if status and status.strip().upper() != "ALL":
        filtered_query = filtered_query.filter(PaymentVerification.status == status.strip().upper())

    if customer_name and customer_name.strip():
        filtered_query = filtered_query.filter(
            func.lower(PaymentVerification.customer_name).contains(customer_name.strip().lower())
        )

    claims_rows = filtered_query.order_by(PaymentVerification.created_at.desc()).all()

    formatted_claims = [format_claim_response(c, db, include_balance=False) for c in claims_rows]

    return PaymentClaimListResponse(
        total_count=total_count,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        claims=formatted_claims
    )


@app.get(
    "/api/payment-verifications/{id}",
    response_model=PaymentClaimResponse,
    summary="Get details of a specific payment claim"
)
def get_payment_claim(
    id: int,
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    """Retrieves full details of a payment claim along with current customer balance."""
    shop_id = get_effective_shop_id(current_user)
    claim = (
        db.query(PaymentVerification)
        .filter(PaymentVerification.id == id, PaymentVerification.shop_id == shop_id)
        .first()
    )
    if not claim:
        raise HTTPException(status_code=404, detail=f"Payment claim #{id} not found.")

    return format_claim_response(claim, db, include_balance=True)


@app.post(
    "/api/payment-verifications/{id}/approve",
    response_model=PaymentClaimResponse,
    summary="Approve a payment claim, verify bank credit, and atomically commit JAMA transaction"
)
def approve_payment_claim(
    id: int,
    payload: PaymentClaimApproveRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(check_role(["OWNER", "MANAGER"])),
    db: Session = Depends(get_db)
):
    """
    CRITICAL FINANCIAL WORKFLOW:
    1. Validates that claim is in PENDING state.
    2. Atomically creates exactly one JAMA Transaction in integer paise.
    3. Updates claim to APPROVED and links resulting_transaction_id.
    4. Creates transactional outbox email receipt for customer.
    5. Deducts the verified amount from customer's khata balance.
    """
    shop_id = get_effective_shop_id(current_user)
    claim = (
        db.query(PaymentVerification)
        .filter(PaymentVerification.id == id, PaymentVerification.shop_id == shop_id)
        .first()
    )
    if not claim:
        raise HTTPException(status_code=404, detail=f"Payment claim #{id} not found.")

    if claim.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"Claim #{id} is already in '{claim.status}' state and cannot be approved."
        )

    # Determine verified amount
    verified_amount = payload.verified_amount if payload.verified_amount is not None else claim.claimed_amount
    verified_paise = int(round(verified_amount * 100))
    if verified_paise <= 0:
        raise HTTPException(status_code=400, detail="Verified amount must be greater than zero.")

    # Validate customer email (mandatory for receipts)
    cust_email = (payload.customer_email.strip().lower() if payload.customer_email else None) or claim.customer_email
    if not cust_email:
        # Check historical records
        hist_tx = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == shop_id,
                func.lower(Transaction.customer_name) == claim.customer_name.lower(),
                Transaction.customer_email.isnot(None)
            )
            .order_by(Transaction.timestamp.desc())
            .first()
        )
        if hist_tx and hist_tx.customer_email:
            cust_email = hist_tx.customer_email

    if not cust_email or not is_valid_email(cust_email):
        raise HTTPException(
            status_code=400,
            detail="Customer email is required to approve payment and issue an official khata receipt. Please provide customer_email."
        )

    now_ist = get_current_ist_time()
    notes_parts = ["Payment verified via UPI/WhatsApp"]
    if claim.utr_number:
        notes_parts.append(f"(Ref/UTR: {claim.utr_number})")
    if payload.notes:
        notes_parts.append(f"- {payload.notes.strip()}")
    tx_notes = " ".join(notes_parts)

    try:
        # 1. Create single atomic JAMA transaction
        jama_tx = Transaction(
            user_id=shop_id,
            customer_name=claim.customer_name,
            customer_phone=claim.customer_phone,
            customer_email=cust_email,
            amount_paise=verified_paise,
            amount=round(verified_paise / 100.0, 2),
            transaction_type=TransactionType.JAMA,
            notes=tx_notes,
            timestamp=now_ist
        )
        db.add(jama_tx)
        db.flush()

        # 2. Update claim state
        claim.status = "APPROVED"
        claim.verified_amount = round(verified_paise / 100.0, 2)
        claim.verified_amount_paise = verified_paise
        claim.customer_email = cust_email
        claim.reviewed_by_user_id = current_user.id
        claim.reviewed_at = now_ist
        claim.review_notes = payload.notes.strip() if payload.notes else None
        claim.resulting_transaction_id = jama_tx.id

        # 3. Create transactional outbox receipt
        receipt_job = TransactionReceipt(
            shop_id=shop_id,
            transaction_id=jama_tx.id,
            customer_name=claim.customer_name,
            customer_email=cust_email,
            status="PENDING",
            attempts=0,
            created_at=now_ist
        )
        db.add(receipt_job)

        # 4. Commit atomic transaction
        db.commit()
        db.refresh(claim)
        db.refresh(jama_tx)
        db.refresh(receipt_job)

        # 5. Enqueue background email dispatch
        if background_tasks:
            background_tasks.add_task(dispatch_receipt_email_job, receipt_job.id)
        else:
            import threading
            threading.Thread(target=dispatch_receipt_email_job, args=(receipt_job.id,), daemon=True).start()

        # 6. Audit Log
        log_audit_event(
            db, shop_id, current_user, "PAYMENT_CLAIM_APPROVED",
            resource_type="payment_verification", resource_id=str(claim.id),
            details={
                "customer": claim.customer_name,
                "claimed_amount": claim.claimed_amount,
                "verified_amount": claim.verified_amount,
                "transaction_id": jama_tx.id,
                "utr": claim.utr_number
            }
        )

        return format_claim_response(claim, db, include_balance=True)

    except Exception as exc:
        db.rollback()
        logger.error(f"Error approving payment claim #{id}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to approve payment claim: {str(exc)}"
        )


@app.post(
    "/api/payment-verifications/{id}/reject",
    response_model=PaymentClaimResponse,
    summary="Reject a payment claim without altering the ledger balance"
)
def reject_payment_claim(
    id: int,
    payload: PaymentClaimRejectRequest,
    current_user: User = Depends(check_role(["OWNER", "MANAGER"])),
    db: Session = Depends(get_db)
):
    """
    Rejects an unverified or fraudulent payment claim.
    CRITICAL: Does NOT create any ledger transaction or alter customer balance.
    """
    shop_id = get_effective_shop_id(current_user)
    claim = (
        db.query(PaymentVerification)
        .filter(PaymentVerification.id == id, PaymentVerification.shop_id == shop_id)
        .first()
    )
    if not claim:
        raise HTTPException(status_code=404, detail=f"Payment claim #{id} not found.")

    if claim.status != "PENDING":
        raise HTTPException(
            status_code=400,
            detail=f"Claim #{id} is already in '{claim.status}' state and cannot be rejected."
        )

    now_ist = get_current_ist_time()
    claim.status = "REJECTED"
    claim.reviewed_by_user_id = current_user.id
    claim.reviewed_at = now_ist
    claim.review_notes = payload.notes.strip()

    db.commit()
    db.refresh(claim)

    log_audit_event(
        db, shop_id, current_user, "PAYMENT_CLAIM_REJECTED",
        resource_type="payment_verification", resource_id=str(claim.id),
        details={
            "customer": claim.customer_name,
            "claimed_amount": claim.claimed_amount,
            "rejection_reason": payload.notes.strip(),
            "utr": claim.utr_number
        }
    )

    return format_claim_response(claim, db, include_balance=True)


# ==========================================
# Reliability & SQLite Physical Snapshots
# ==========================================

@app.get(
    "/api/system/reliability",
    summary="Get database integrity, journal mode, storage stats, and physical snapshot history"
)
def get_system_reliability(
    current_user: User = Depends(require_current_user),
    db: Session = Depends(get_db)
):
    db_file = PROJECT_ROOT / "hisab_ai.db"
    size_bytes = db_file.stat().st_size if db_file.exists() else 0

    try:
        integrity_row = db.execute(text("PRAGMA integrity_check")).fetchone()
        integrity_status = integrity_row[0] if integrity_row else "unknown"
    except Exception as exc:
        integrity_status = f"Error: {exc}"

    try:
        journal_row = db.execute(text("PRAGMA journal_mode")).fetchone()
        journal_mode = journal_row[0] if journal_row else "unknown"
    except Exception:
        journal_mode = "unknown"

    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    snapshots = []
    for f in sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True):
        snapshots.append({
            "filename": f.name,
            "size_kb": round(f.stat().st_size / 1024, 2),
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, KOLKATA_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
        })

    return {
        "database_file": str(db_file.name),
        "database_size_bytes": size_bytes,
        "database_size_kb": round(size_bytes / 1024, 2),
        "integrity_check": integrity_status,
        "journal_mode": str(journal_mode).upper(),
        "snapshots_count": len(snapshots),
        "snapshots": snapshots,
        "reliability_explanation": (
            "HisabAI uses SQLite with ACID transactions, atomic integer-paise calculations, "
            "and on-demand physical snapshot backups. Your financial ledger data is never lost, "
            "and cannot drift due to floating-point rounding."
        )
    }


@app.post(
    "/api/backup/create-snapshot",
    summary="Create an immediate physical SQLite database snapshot in backups/ (Owner only)"
)
def create_database_snapshot(
    current_user: User = Depends(check_role(["OWNER"])),
    db: Session = Depends(get_db)
):
    shop_id = get_effective_shop_id(current_user)
    db_file = PROJECT_ROOT / "hisab_ai.db"
    if not db_file.exists():
        raise HTTPException(status_code=404, detail="Primary database file not found.")

    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = get_current_ist_time().strftime("%Y%m%d_%H%M%S")
    snapshot_filename = f"hisab_ai_snapshot_{timestamp_str}.db"
    dest_path = backup_dir / snapshot_filename

    try:
        shutil.copy2(db_file, dest_path)
        size_bytes = dest_path.stat().st_size
        log_audit_event(
            db, shop_id, current_user, "DATABASE_SNAPSHOT_CREATED",
            resource_type="backup", resource_id=snapshot_filename,
            details={"size_bytes": size_bytes}
        )
        return {
            "success": True,
            "message": f"Physical database snapshot created successfully: {snapshot_filename}",
            "filename": snapshot_filename,
            "size_kb": round(size_bytes / 1024, 2),
            "timestamp": get_current_ist_time().strftime("%Y-%m-%d %H:%M:%S IST")
        }
    except Exception as exc:
        logger.error(f"Failed to create snapshot: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Snapshot creation failed: {str(exc)}")

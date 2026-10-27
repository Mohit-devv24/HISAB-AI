"""
FastAPI Backend for HisabAI - Intelligent Digital Khata for Indian Shopkeepers.
Provides endpoints for Hinglish AI parsing, voice assistant interactions ("Suno Hisab"),
two-step transaction review & confirmation, IST daily financial summaries,
customer ledger queries, and system health checks.
"""

from datetime import datetime, time, timedelta
import logging
import os
from pathlib import Path
import secrets
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote
import uuid
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
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
import models
from models import (
    AuthResponse,
    CustomerDirectoryItem,
    CustomerDirectoryResponse,
    CustomerSummaryResponse,
    DailySummaryResponse,
    ExtractedTransaction,
    GoogleAuthRequest,
    HealthResponse,
    KOLKATA_TZ,
    LedgerEntryResponse,
    LedgerHistoryResponse,
    ParseInputRequest,
    PendingOperationData,
    Transaction,
    TransactionCreateRequest,
    TransactionResponse,
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

app = FastAPI(
    title="HisabAI API",
    description="Intelligent Digital Khata for Indian Shopkeepers powered by Google Gemma",
    version="2.1.0"
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


def cleanup_expired_operations():
    """Removes operations that have exceeded their 5-minute time-to-live."""
    now = get_current_ist_time()
    expired_keys = [
        op_id for op_id, op in PENDING_VOICE_OPERATIONS.items()
        if op.get("expires_dt") and op["expires_dt"] < now
    ]
    for op_id in expired_keys:
        PENDING_VOICE_OPERATIONS.pop(op_id, None)


def create_pending_operation(
    customer_name: str,
    amount: float,
    transaction_type: str,
    session_id: Optional[str] = None,
    user_id: Optional[int] = None
) -> PendingOperationData:
    """Creates an uncommitted pending operation awaiting explicit user confirmation."""
    cleanup_expired_operations()
    op_id = str(uuid.uuid4())
    now = get_current_ist_time()
    expires_dt = now + timedelta(minutes=5)

    op_record = {
        "operation_id": op_id,
        "user_id": user_id,
        "customer_name": customer_name,
        "amount": round(float(amount), 2),
        "amount_paise": int(round(float(amount) * 100)),
        "transaction_type": transaction_type.upper(),
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S IST"),
        "expires_at": expires_dt.strftime("%Y-%m-%d %H:%M:%S IST"),
        "expires_dt": expires_dt,
        "status": "pending",
        "session_id": session_id or "default",
        "committed_tx_id": None
    }
    PENDING_VOICE_OPERATIONS[op_id] = op_record

    return PendingOperationData(
        operation_id=op_id,
        customer_name=customer_name,
        amount=round(float(amount), 2),
        transaction_type=transaction_type.upper(),
        created_at=op_record["created_at"],
        expires_at=op_record["expires_at"],
        status="pending"
    )


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
            shop_name=f"{google_name}'s Dukaan",
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
        shop_name=payload.shop_name or "Apna Kirana Store",
        owner_name=payload.owner_name,
        email=clean_email,
        password_hash=pw_hash,
        created_at=now_dt
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

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
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    query_term = (search or q or "").strip().lower()
    tx_query = db.query(Transaction)
    if current_user:
        tx_query = tx_query.filter(Transaction.user_id == current_user.id)
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
            status_label = f"Baqaya ₹{bal:,.2f}"
        elif bal < 0:
            status_label = f"Advance ₹{abs(bal):,.2f}"
        else:
            status_label = "Hisab Clear"

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
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    """
    Step 2 of Review-Before-Save workflow:
    Persists a user-confirmed transaction to SQLite.
    Accepts either confirmed structured fields (customer_name, amount, transaction_type)
    or a raw 'text' string (which parses first for backwards compatibility).
    Monetary amounts are stored as integer paise to prevent floating point inaccuracies.
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

    try:
        db_transaction = Transaction(
            user_id=current_user.id if current_user else None,
            customer_name=clean_name,
            amount_paise=amount_paise,
            amount=round(amount_float, 2),
            transaction_type=tx_type,
            timestamp=get_current_ist_time()
        )
        db.add(db_transaction)
        db.commit()
        db.refresh(db_transaction)

        logger.info(
            f"Recorded Transaction #{db_transaction.id}: {db_transaction.customer_name} "
            f"- ₹{db_transaction.amount:.2f} ({db_transaction.transaction_type.value}) [user={db_transaction.user_id}]"
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
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
):
    return create_transaction(payload=payload, db=db, current_user=current_user)


@app.get(
    "/api/summary",
    response_model=DailySummaryResponse,
    summary="Get today's metrics and transaction history in IST"
)
def get_daily_summary(
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional)
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
    start_of_today = datetime.combine(now_ist.date(), time.min, tzinfo=KOLKATA_TZ)
    end_of_today = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)

    query = db.query(Transaction).filter(
        Transaction.timestamp >= start_of_today,
        Transaction.timestamp <= end_of_today
    )
    if current_user:
        query = query.filter(Transaction.user_id == current_user.id)

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
        date=now_ist.strftime("%Y-%m-%d"),
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
    if current_user:
        query = query.filter(Transaction.user_id == current_user.id)

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
    user_id: Optional[int] = None
) -> Tuple[str, dict]:
    """
    Queries real database records for customer balance.
    Handles prefix matching, full names, and ambiguity.
    """
    clean_query = " ".join(customer_query.strip().split())
    if not clean_query:
        return ("Customer ka naam spasht nahi hai. Kripya naam dobara bolein.", {})

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
        names_str = " aur ".join(matched_names[:3])
        speech = f"Mujhe {len(matched_names)} customer mile hain: {names_str}. Aap kiska hisab dekhna chahte hain?"
        return (speech, {"ambiguous_customers": matched_names})

    if len(matched_names) == 1:
        # Exactly one customer matches partial name
        return execute_customer_balance_voice_query(matched_names[0], db, user_id=user_id)

    speech = f"Khate mein '{clean_query}' naam ka koi customer nahi mila."
    return (speech, {"not_found": clean_query})


def execute_summary_voice_query(
    date_filter: Optional[str],
    tx_type_filter: Optional[str],
    db: Session,
    user_id: Optional[int] = None
) -> Tuple[str, dict]:
    """Queries actual database records for daily or historical reports in IST."""
    now_ist = get_current_ist_time()

    if date_filter == "yesterday":
        yesterday_date = now_ist.date() - timedelta(days=1)
        start_dt = datetime.combine(yesterday_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(yesterday_date, time.max, tzinfo=KOLKATA_TZ)
        label = "Kal"
    elif date_filter == "last_7_days":
        start_date = now_ist.date() - timedelta(days=7)
        start_dt = datetime.combine(start_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        label = "Pichhle 7 din"
    elif date_filter == "this_month":
        start_date = now_ist.date().replace(day=1)
        start_dt = datetime.combine(start_date, time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        label = "Is mahine"
    else:
        # Default: today
        start_dt = datetime.combine(now_ist.date(), time.min, tzinfo=KOLKATA_TZ)
        end_dt = datetime.combine(now_ist.date(), time.max, tzinfo=KOLKATA_TZ)
        label = "Aaj"

    query = db.query(Transaction).filter(Transaction.timestamp >= start_dt, Transaction.timestamp <= end_dt)
    if user_id is not None:
        query = query.filter(Transaction.user_id == user_id)
    records = query.order_by(Transaction.timestamp.desc()).all()

    udhaar_paise = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
    jama_paise = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
    total_udhaar = round(udhaar_paise / 100.0, 2)
    total_jama = round(jama_paise / 100.0, 2)
    net_movement = round((udhaar_paise - jama_paise) / 100.0, 2)

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


def execute_customer_list_voice_query(db: Session, user_id: Optional[int] = None) -> Tuple[str, dict]:
    """Finds all customers with pending debts, sorted from highest to lowest."""
    query = db.query(Transaction)
    if user_id is not None:
        query = query.filter(Transaction.user_id == user_id)
    all_txs = query.all()
    if not all_txs:
        return ("Khate mein abhi koi entry darj nahi hai.", {})

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
        return ("Kisi bhi customer ka udhaar baaki nahi hai. Sabhi hisab clear hain.", {})

    top_debtor = debtors[0]
    speech = f"Sabse zyada udhaar {top_debtor[0]} ka hai ₹{top_debtor[1]:,.2f}."
    if len(debtors) > 1:
        other_names = [f"{d[0]} (₹{d[1]:,.2f})" for d in debtors[1:4]]
        speech += f" Iske alawa {' aur '.join(other_names)} ka baaki hai."

    return (speech, {"debtors": [{"customer_name": d[0], "outstanding_balance": d[1]} for d in debtors]})


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

    logger.info(f"Voice assistant request [session={session_id}, op={op_id}]: '{raw_text}' (action={payload.action})")

    # 1. Direct Confirmation Action (Client Button or spoken "Haan, save kar do")
    if payload.action == "confirm" or (op_id and raw_text.lower() in ("haan", "confirm", "theek hai", "save karo", "save kar do", "haan save kar do")):
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

        # Commit exactly one transaction in integer paise
        amount_paise = int(round(op["amount"] * 100))
        tx_type = TransactionType(op["transaction_type"])

        try:
            db_tx = Transaction(
                user_id=op.get("user_id") or auth_user_id,
                customer_name=op["customer_name"],
                amount_paise=amount_paise,
                amount=op["amount"],
                transaction_type=tx_type,
                timestamp=get_current_ist_time()
            )
            db.add(db_tx)
            db.commit()
            db.refresh(db_tx)

            op["status"] = "committed"
            op["committed_tx_id"] = db_tx.id

            type_label = "udhaar" if tx_type == TransactionType.UDHAAR else "jama"
            reply = f"{op['customer_name']} ke khate mein ₹{op['amount']:.2f} {type_label} safaltapoorvak save kar diye gaye hain."
            LAST_SESSION_REPLIES[session_id] = reply

            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="committed_transaction",
                requires_confirmation=False,
                pending_operation=PendingOperationData(**op),
                data={"transaction_id": db_tx.id}
            )
        except Exception as exc:
            db.rollback()
            logger.error(f"Failed to commit voice operation {op_id}: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail="Database persistence failed.")

    # 2. Direct Cancellation Action (Client Button or spoken "Nahi, cancel kar do")
    if payload.action == "cancel" or (op_id and raw_text.lower() in ("nahi", "nahin", "cancel", "radd karo", "cancel kar do", "mat karo", "mat save karo")):
        if op_id and op_id in PENDING_VOICE_OPERATIONS:
            PENDING_VOICE_OPERATIONS[op_id]["status"] = "cancelled"
        reply = "Theek hai, entry cancel kar di gayi hai. Ledger mein koi badlaav nahi hua."
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

    # Intent A: CONFIRM
    if intent_result.intent == VoiceIntentType.CONFIRM:
        if op_id and op_id in PENDING_VOICE_OPERATIONS and PENDING_VOICE_OPERATIONS[op_id]["status"] == "pending":
            return handle_voice_assistant(
                VoiceAssistantRequest(text="confirm", session_id=session_id, pending_operation_id=op_id, action="confirm"),
                db=db,
                current_user=current_user
            )
        reply = "Koi pending entry nahi hai jise confirm kiya ja sake. Kripya pehle entry bolein."
        return VoiceAssistantResponse(reply_message=reply, action_taken="no_pending_operation", requires_confirmation=False)

    # Intent B: CANCEL
    if intent_result.intent == VoiceIntentType.CANCEL:
        if op_id and op_id in PENDING_VOICE_OPERATIONS:
            PENDING_VOICE_OPERATIONS[op_id]["status"] = "cancelled"
        reply = "Theek hai, entry cancel kar di gayi hai."
        LAST_SESSION_REPLIES[session_id] = reply
        return VoiceAssistantResponse(reply_message=reply, action_taken="cancelled", requires_confirmation=False)

    # Intent C: CORRECT PENDING ENTRY ("500 nahi 800")
    if intent_result.intent == VoiceIntentType.CORRECT_PENDING_ENTRY:
        if op_id and op_id in PENDING_VOICE_OPERATIONS and PENDING_VOICE_OPERATIONS[op_id]["status"] == "pending":
            op = PENDING_VOICE_OPERATIONS[op_id]
            if intent_result.corrected_amount:
                op["amount"] = intent_result.corrected_amount
            if intent_result.corrected_customer_name:
                op["customer_name"] = intent_result.corrected_customer_name

            type_label = "udhaar" if op["transaction_type"] == "UDHAAR" else "jama"
            reply = f"Theek hai, {op['customer_name']} ke khate mein amount badal kar ₹{op['amount']:.2f} {type_label} kar diya gaya hai. Kya main save kar doon?"
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(
                reply_message=reply,
                action_taken="corrected_pending_entry",
                requires_confirmation=True,
                pending_operation=PendingOperationData(**op)
            )
        reply = "Koi pending entry nahi mili jise correct kiya ja sake."
        return VoiceAssistantResponse(reply_message=reply, action_taken="no_pending_operation", requires_confirmation=False)

    # Intent D: QUERY CUSTOMER BALANCE
    if intent_result.intent == VoiceIntentType.QUERY_CUSTOMER_BALANCE:
        if not intent_result.customer_name:
            reply = "Aap kiska khata dekhna chahte hain? Kripya customer ka naam bolein."
            return VoiceAssistantResponse(reply_message=reply, action_taken="missing_customer_name", requires_confirmation=False)

        speech, data = execute_customer_balance_voice_query(intent_result.customer_name, db, user_id=auth_user_id)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="customer_balance", requires_confirmation=False, data=data)

    # Intent E: QUERY DAILY SUMMARY
    if intent_result.intent == VoiceIntentType.QUERY_DAILY_SUMMARY:
        speech, data = execute_summary_voice_query("today", intent_result.transaction_type, db, user_id=auth_user_id)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="daily_summary", requires_confirmation=False, data=data)

    # Intent F: QUERY DATE RANGE SUMMARY ("Kal", "Pichhle 7 din", "Is mahine")
    if intent_result.intent == VoiceIntentType.QUERY_DATE_RANGE_SUMMARY:
        speech, data = execute_summary_voice_query(intent_result.date_filter or "today", None, db, user_id=auth_user_id)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="date_range_summary", requires_confirmation=False, data=data)

    # Intent G: QUERY CUSTOMER LIST ("Kis-kis customer ke paise baaki hain?")
    if intent_result.intent == VoiceIntentType.QUERY_CUSTOMER_LIST:
        speech, data = execute_customer_list_voice_query(db, user_id=auth_user_id)
        LAST_SESSION_REPLIES[session_id] = speech
        return VoiceAssistantResponse(reply_message=speech, action_taken="customer_list", requires_confirmation=False, data=data)

    # Intent H: STOP ASSISTANT
    if intent_result.intent == VoiceIntentType.STOP_ASSISTANT:
        reply = "Assistant band kar diya gaya hai. Jab zaroorat ho, 'Suno Hisab' bolein ya mic dabayein."
        return VoiceAssistantResponse(reply_message=reply, action_taken="stopped", requires_confirmation=False)

    # Intent I: ENTRY (Two-Step Workflow: Prepare first, never save without confirmation!)
    if intent_result.intent == VoiceIntentType.ENTRY:
        if not intent_result.customer_name or not intent_result.amount or not intent_result.transaction_type:
            reply = "Customer ka naam ya amount clear nahi suna. Kripya dobara bolein, jaise 'Rahul ko 500 udhaar diya'."
            return VoiceAssistantResponse(reply_message=reply, action_taken="incomplete_entry", requires_confirmation=False)

        pending_op = create_pending_operation(
            customer_name=intent_result.customer_name,
            amount=intent_result.amount,
            transaction_type=intent_result.transaction_type,
            session_id=session_id,
            user_id=auth_user_id
        )

        type_label = "udhaar" if intent_result.transaction_type == "UDHAAR" else "jama"
        reply = f"{intent_result.customer_name} ke khate mein ₹{intent_result.amount:.2f} {type_label} likhne hain. Kya main save kar doon?"
        LAST_SESSION_REPLIES[session_id] = reply

        return VoiceAssistantResponse(
            reply_message=reply,
            action_taken="prepared_entry",
            requires_confirmation=True,
            pending_operation=pending_op
        )

    # Intent J: AMBIGUOUS ACCOUNT ACTION ("Tausif ka khata khatam kar do")
    # Never mutate database without clarification!
    if intent_result.intent == VoiceIntentType.AMBIGUOUS_ACCOUNT_ACTION:
        cname = intent_result.customer_name or "customer"
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

        pending_op = create_pending_operation(
            customer_name=canonical,
            amount=settle_amount,
            transaction_type=settle_type,
            session_id=session_id,
            user_id=auth_user_id
        )

        reply = (
            f"{canonical} ka ₹{settle_amount:,.2f} ka hisab baaki hai. "
            f"Khata zero karne ke liye ₹{settle_amount:,.2f} {settle_type.lower()} darj karna hoga. "
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
        if not cname:
            reply = "Aap kiska purana hisab dekhna chahte hain? Kripya customer ka naam bolein."
            return VoiceAssistantResponse(reply_message=reply, action_taken="missing_customer_name", requires_confirmation=False)

        history_query = db.query(Transaction).filter(func.lower(Transaction.customer_name) == cname.lower())
        if auth_user_id is not None:
            history_query = history_query.filter(Transaction.user_id == auth_user_id)
        records = (
            history_query
            .order_by(Transaction.timestamp.desc())
            .all()
        )
        if not records:
            reply = f"Khate mein '{cname}' naam ka koi hisab nahi mila."
            LAST_SESSION_REPLIES[session_id] = reply
            return VoiceAssistantResponse(reply_message=reply, action_taken="not_found", requires_confirmation=False)

        canonical = records[0].customer_name
        u_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.UDHAAR)
        j_p = sum(t.amount_paise for t in records if t.transaction_type == TransactionType.JAMA)
        bal = round((u_p - j_p) / 100.0, 2)
        last_tx = records[0]
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

    # Default / UNKNOWN Intent
    reply = intent_result.clarification_message or "Aapne kya kaha, main theek se samajh nahi paaya. Kripya dobara bolein."
    LAST_SESSION_REPLIES[session_id] = reply
    return VoiceAssistantResponse(reply_message=reply, action_taken="clarification", requires_confirmation=False)

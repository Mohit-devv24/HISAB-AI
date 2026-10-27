"""
SQLAlchemy ORM models and Pydantic v2 validation schemas for HisabAI.
Implements exact integer-paise monetary precision to eliminate floating-point drift.
All timestamps and business boundaries strictly respect Asia/Kolkata (IST).
"""

from datetime import datetime, timezone
import enum
import hashlib
import secrets
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Column, DateTime, Enum as SQLEnum, Float, ForeignKey, Integer, String
from sqlalchemy.ext.hybrid import hybrid_property
from database import Base

# Strict Indian Standard Time (IST) Zone
KOLKATA_TZ = ZoneInfo("Asia/Kolkata")


def get_current_ist_time() -> datetime:
    """Returns the current timezone-aware timestamp in Asia/Kolkata (IST)."""
    return datetime.now(KOLKATA_TZ)


def hash_password(password: str) -> str:
    """Hashes password using scrypt with random salt (RFC 7914)."""
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}:{derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verifies plain password against scrypt salt:hash string."""
    try:
        salt_hex, derived_hex = hashed.split(":")
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(derived_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1)
        return secrets.compare_digest(actual, expected)
    except Exception:
        return False


class TransactionType(str, enum.Enum):
    """Strict transaction classification: UDHAAR (credit given) or JAMA (payment received)."""
    UDHAAR = "UDHAAR"
    JAMA = "JAMA"


class User(Base):
    """
    SQLAlchemy model representing an authenticated shop owner.
    Supports email, owner name, password hash, and Google OAuth identities.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(60), unique=True, nullable=False, index=True)
    shop_name = Column(String(120), nullable=False, default="Apna Kirana Store")
    owner_name = Column(String(120), nullable=True)
    email = Column(String(120), unique=True, nullable=True, index=True)
    google_id = Column(String(120), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False
    )


class UserSession(Base):
    """
    SQLAlchemy model representing active authenticated sessions.
    """
    __tablename__ = "user_sessions"

    token = Column(String(64), primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)


class Transaction(Base):
    """
    SQLAlchemy model representing a khata ledger transaction.
    Authoritative monetary storage is integer paise (1 Rupee = 100 Paise).
    Bound to an authenticated shop owner (user_id).
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    customer_name = Column(String(120), nullable=False, index=True)
    # Primary authoritative monetary storage is integer paise (1 Rupee = 100 Paise)
    amount_paise = Column(Integer, nullable=False)
    # Kept in sync for backwards compatibility and direct display
    amount = Column(Float, nullable=True)
    transaction_type = Column(SQLEnum(TransactionType), nullable=False)
    timestamp = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False,
        index=True
    )

    def __init__(self, **kwargs):
        if "amount" in kwargs and "amount_paise" not in kwargs:
            kwargs["amount_paise"] = int(round(float(kwargs["amount"]) * 100))
        elif "amount_paise" in kwargs and "amount" not in kwargs:
            kwargs["amount"] = round(float(kwargs["amount_paise"]) / 100.0, 2)
        elif "amount" in kwargs and "amount_paise" in kwargs:
            kwargs["amount"] = round(float(kwargs["amount"]), 2)
        super().__init__(**kwargs)

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} customer='{self.customer_name}' "
            f"amount=₹{self.amount:.2f} type='{self.transaction_type}'>"
        )


# ==========================================
# Pydantic v2 Schemas
# ==========================================

class ParseInputRequest(BaseModel):
    """Input payload containing Hinglish phrase to parse without saving."""
    text: str = Field(..., min_length=2, max_length=500, description="Hinglish voice or text phrase")

    @field_validator("text")
    @classmethod
    def clean_text(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 2:
            raise ValueError("Input phrase must be at least 2 characters long.")
        return cleaned


class ExtractedTransaction(BaseModel):
    """Schema for AI extraction results or pre-confirmation review."""
    customer_name: str = Field(..., min_length=1, max_length=120, description="Customer name or identifier")
    amount: float = Field(..., gt=0, le=10_000_000, description="Monetary amount in Rupees")
    transaction_type: Literal["UDHAAR", "JAMA"] = Field(..., description="UDHAAR (credit) or JAMA (received)")

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be strictly greater than zero.")
        # Ensure standard 2-decimal precision
        return round(float(v), 2)

    @field_validator("customer_name")
    @classmethod
    def validate_customer_name(cls, v: str) -> str:
        cleaned = " ".join(v.strip().split())
        if not cleaned:
            raise ValueError("Customer name cannot be empty.")
        return cleaned


class TransactionCreateRequest(BaseModel):
    """
    Payload to confirm and persist a transaction.
    Supports either confirmed structured fields or raw text fallback.
    """
    customer_name: Optional[str] = Field(None, min_length=1, max_length=120)
    amount: Optional[float] = Field(None, gt=0, le=10_000_000)
    transaction_type: Optional[Literal["UDHAAR", "JAMA"]] = None
    text: Optional[str] = Field(None, min_length=2, max_length=500)

    @model_validator(mode="after")
    def check_payload(self):
        has_structured = bool(self.customer_name and self.amount and self.transaction_type)
        has_text = bool(self.text and self.text.strip())
        if not has_structured and not has_text:
            raise ValueError(
                "Either structured transaction fields (customer_name, amount, transaction_type) "
                "or a Hinglish 'text' phrase must be provided."
            )
        if self.amount is not None:
            if self.amount <= 0:
                raise ValueError("Amount must be strictly greater than zero.")
            self.amount = round(float(self.amount), 2)
        if self.customer_name is not None:
            self.customer_name = " ".join(self.customer_name.strip().split())
        return self


class TransactionResponse(BaseModel):
    """Standard serialized representation of a saved transaction."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_name: str
    amount: float
    transaction_type: TransactionType
    timestamp: datetime

    @field_validator("amount", mode="before")
    @classmethod
    def ensure_amount_float(cls, v, info) -> float:
        # If passed an integer paise or float
        if isinstance(v, (int, float)):
            return round(float(v), 2)
        return float(v)


class DailySummaryResponse(BaseModel):
    """Daily summary metrics in Asia/Kolkata timezone with transaction count."""
    date: str
    total_udhaar_today: float
    total_jama_today: float
    net_today: float
    transaction_count: int
    transactions: List[TransactionResponse]


class CustomerSummaryResponse(BaseModel):
    """Customer-specific ledger balance summary with transaction history."""
    customer_name: str
    lifetime_udhaar: float
    lifetime_jama: float
    outstanding_balance: float
    status_label: str
    transaction_count: int
    transaction_history: List[TransactionResponse]


class HealthResponse(BaseModel):
    """System health check payload."""
    status: str
    database: str
    ai_configured: bool
    ai_model: str
    server_time_ist: str


# ==========================================
# Voice Assistant Schemas
# ==========================================

class VoiceIntentType(str, enum.Enum):
    """Classification of voice command intents."""
    ENTRY = "ENTRY"
    QUERY_CUSTOMER_BALANCE = "QUERY_CUSTOMER_BALANCE"
    QUERY_CUSTOMER_HISTORY = "QUERY_CUSTOMER_HISTORY"
    QUERY_DAILY_SUMMARY = "QUERY_DAILY_SUMMARY"
    QUERY_DATE_RANGE_SUMMARY = "QUERY_DATE_RANGE_SUMMARY"
    QUERY_CUSTOMER_LIST = "QUERY_CUSTOMER_LIST"
    AMBIGUOUS_ACCOUNT_ACTION = "AMBIGUOUS_ACCOUNT_ACTION"
    SETTLE_BALANCE = "SETTLE_BALANCE"
    CONFIRM = "CONFIRM"
    CANCEL = "CANCEL"
    CORRECT_PENDING_ENTRY = "CORRECT_PENDING_ENTRY"
    REPEAT_LAST_RESPONSE = "REPEAT_LAST_RESPONSE"
    STOP_ASSISTANT = "STOP_ASSISTANT"
    UNKNOWN = "UNKNOWN"


class VoiceIntentResult(BaseModel):
    """Structured intent & entity extraction from natural speech."""
    intent: VoiceIntentType
    customer_name: Optional[str] = None
    amount: Optional[float] = None
    transaction_type: Optional[Literal["UDHAAR", "JAMA"]] = None
    date_filter: Optional[str] = None  # e.g., "today", "yesterday", "last_7_days", "this_month"
    corrected_amount: Optional[float] = None
    corrected_customer_name: Optional[str] = None
    clarification_message: Optional[str] = None

    @field_validator("amount", "corrected_amount")
    @classmethod
    def round_amount_field(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if v <= 0:
                raise ValueError("Amount must be strictly positive.")
            return round(float(v), 2)
        return None

    @field_validator("customer_name", "corrected_customer_name")
    @classmethod
    def clean_name_field(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = " ".join(v.strip().split())
            return cleaned if cleaned else None
        return None


class PendingOperationData(BaseModel):
    """Details of an uncommitted transaction awaiting explicit user confirmation."""
    operation_id: str
    customer_name: str
    amount: float
    transaction_type: Literal["UDHAAR", "JAMA"]
    created_at: str
    expires_at: str
    status: Literal["pending", "committed", "cancelled", "expired"] = "pending"


class VoiceAssistantRequest(BaseModel):
    """Input payload for voice interaction endpoint."""
    text: str = Field(..., min_length=1, max_length=1000, description="Spoken voice transcript or command")
    session_id: Optional[str] = Field(None, description="Client session identifier")
    pending_operation_id: Optional[str] = Field(None, description="Active pending operation identifier if any")
    action: Optional[Literal["process", "confirm", "cancel", "correct"]] = Field("process", description="Direct client action override")


class VoiceAssistantResponse(BaseModel):
    """Spoken and visual response from HisabAI voice assistant."""
    reply_message: str = Field(..., description="Natural Hindi/Hinglish text for SpeechSynthesis")
    action_taken: str = Field(..., description="Action category identifier")
    requires_confirmation: bool = Field(False, description="True if waiting for user confirmation")
    pending_operation: Optional[PendingOperationData] = None
    data: Optional[dict] = Field(None, description="Optional payload for UI cards or charts")
    status: Optional[str] = Field("success", description="Status string for compatibility")
    intent: Optional[str] = Field(None, description="Intent classification string for compatibility")
    spoken_text: Optional[str] = Field(None, description="Spoken speech transcript for compatibility")

    @model_validator(mode="after")
    def populate_compatibility_fields(self):
        if not self.spoken_text:
            self.spoken_text = self.reply_message
        if not self.intent:
            self.intent = self.action_taken
        return self


# ==========================================
# Authentication & User Schemas
# ==========================================

class UserSignupRequest(BaseModel):
    """Payload to register a new shopkeeper account."""
    shop_name: Optional[str] = Field("Apna Kirana Store", max_length=120, description="Shop name")
    owner_name: Optional[str] = Field(None, max_length=120, description="Shopkeeper name")
    email: Optional[str] = Field(None, max_length=120, description="Email address")
    username: Optional[str] = Field(None, min_length=3, max_length=60, description="Unique username")
    password: str = Field(..., min_length=6, max_length=100, description="Account password")
    confirm_password: Optional[str] = Field(None, min_length=6, max_length=100, description="Confirm password")

    @model_validator(mode="after")
    def validate_signup(self):
        # 1. Password confirmation check
        if self.confirm_password is not None and self.password != self.confirm_password:
            raise ValueError("Password and Confirm Password must match exactly.")
        # 2. Email format check if provided
        if self.email:
            em = self.email.strip().lower()
            if "@" not in em or "." not in em.split("@")[-1]:
                raise ValueError("Invalid email address format.")
            self.email = em
            if not self.username:
                self.username = em.split("@")[0].strip().lower()
        # 3. Ensure a valid username exists
        if not self.username or len(self.username.strip()) < 3:
            raise ValueError("Username must be at least 3 characters long.")
        self.username = self.username.strip().lower()
        if self.shop_name:
            self.shop_name = self.shop_name.strip()
        return self


class UserLoginRequest(BaseModel):
    """Payload to log in with username or email."""
    username: str = Field(..., min_length=1, description="Username or email")
    password: str = Field(..., min_length=1, description="Password")


class GoogleAuthRequest(BaseModel):
    """Payload for genuine Google OAuth authentication."""
    credential: str = Field(..., min_length=10, description="Google ID Token / Credential JWT from Google Identity Services")


class UserResponse(BaseModel):
    """Public user profile data."""
    id: int
    username: str
    shop_name: str
    owner_name: Optional[str] = None
    email: Optional[str] = None
    created_at: str


class AuthResponse(BaseModel):
    """Successful authentication response containing session token."""
    success: bool
    message: str
    token: str
    user: UserResponse


# ==========================================
# Customer Directory & Historical Ledger Schemas
# ==========================================

class CustomerDirectoryItem(BaseModel):
    """Summary item for the shop customer directory."""
    customer_name: str
    outstanding_balance: float
    lifetime_udhaar: float
    lifetime_jama: float
    total_transactions: int
    last_transaction_date: Optional[str] = None
    last_transaction_type: Optional[str] = None
    status_label: str


class CustomerDirectoryResponse(BaseModel):
    """Full customer directory response."""
    total_customers: int
    total_outstanding_debt: float
    customers: List[CustomerDirectoryItem]


class LedgerEntryResponse(BaseModel):
    """Historical ledger record with running balance."""
    id: int
    customer_name: str
    amount: float
    amount_paise: int
    transaction_type: str
    timestamp: str
    running_balance: float


class LedgerHistoryResponse(BaseModel):
    """Filtered, paginated ledger history response."""
    period: str
    total_records: int
    total_udhaar: float
    total_jama: float
    net_balance: float
    page: int
    page_size: int
    transactions: List[LedgerEntryResponse]



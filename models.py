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
    shop_name = Column(String(120), nullable=False, default="My Store")
    owner_name = Column(String(120), nullable=True)
    email = Column(String(120), unique=True, nullable=True, index=True)
    google_id = Column(String(120), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    role = Column(String(20), nullable=False, default="OWNER")  # OWNER, MANAGER, STAFF
    shop_id = Column(Integer, nullable=True, index=True)
    phone = Column(String(20), nullable=True)
    address = Column(String(255), nullable=True)
    currency = Column(String(10), nullable=False, default="INR")
    timezone = Column(String(40), nullable=False, default="Asia/Kolkata")
    interest_enabled = Column(Integer, nullable=False, default=1)
    interest_rate_percent = Column(Float, nullable=False, default=2.0)
    interest_grace_days = Column(Integer, nullable=False, default=15)
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
    customer_phone = Column(String(20), nullable=True)
    notes = Column(String(255), nullable=True)
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


class PaymentReminder(Base):
    """
    SQLAlchemy model recording sent / prepared payment reminders.
    Supports duplicate check, idempotency, and reminder logs.
    """
    __tablename__ = "payment_reminders"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shop_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    customer_name = Column(String(120), nullable=False, index=True)
    customer_phone = Column(String(20), nullable=True)
    balance_snapshot_paise = Column(Integer, nullable=False)
    message_text = Column(String(1000), nullable=False)
    delivery_method = Column(String(30), default="WHATSAPP")  # WHATSAPP, SMS, COPY, EMAIL
    delivery_status = Column(String(30), default="PREPARED")  # PREPARED, SENT, FAILED
    customer_email = Column(String(120), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    overdue_days = Column(Integer, default=0)
    delivery_details = Column(String(500), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False,
        index=True
    )


class Product(Base):
    """
    SQLAlchemy model representing an inventory item/catalog entry.
    Separate from the financial ledger.
    """
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shop_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    name = Column(String(150), nullable=False, index=True)
    sku = Column(String(60), nullable=True, index=True)
    category = Column(String(80), nullable=True, default="General")
    unit = Column(String(30), default="pcs")  # pcs, kg, litre, packet, meter
    cost_price_paise = Column(Integer, default=0)
    selling_price_paise = Column(Integer, default=0)
    current_stock = Column(Float, default=0.0)
    low_stock_threshold = Column(Float, default=5.0)
    created_at = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        onupdate=get_current_ist_time,
        nullable=False
    )


class StockMovement(Base):
    """
    SQLAlchemy model recording every atomic stock change (received, sold, adjustment, return).
    """
    __tablename__ = "stock_movements"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shop_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    movement_type = Column(String(30), nullable=False)  # RECEIVED, SOLD, ADJUSTMENT, RETURN
    quantity = Column(Float, nullable=False)  # positive or negative delta
    notes = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False,
        index=True
    )


class AuditLog(Base):
    """
    SQLAlchemy model providing an append-only audit trail of sensitive actions.
    """
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    shop_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(60), nullable=True)
    action = Column(String(60), nullable=False, index=True)
    resource_type = Column(String(60), nullable=True)
    resource_id = Column(String(60), nullable=True)
    details_json = Column(String(2000), nullable=True)
    ip_address = Column(String(45), nullable=True)
    timestamp = Column(
        DateTime(timezone=True),
        default=get_current_ist_time,
        nullable=False,
        index=True
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
    NAVIGATE_VIEW = "NAVIGATE_VIEW"
    PREPARE_REMINDER = "PREPARE_REMINDER"
    PREPARE_RECEIPT = "PREPARE_RECEIPT"
    UNKNOWN = "UNKNOWN"


class VoiceIntentResult(BaseModel):
    """Structured intent & entity extraction from natural speech."""
    intent: VoiceIntentType
    customer_name: Optional[str] = None
    amount: Optional[float] = None
    transaction_type: Optional[Literal["UDHAAR", "JAMA"]] = None
    date_filter: Optional[str] = None  # e.g., "today", "yesterday", "last_7_days", "this_month"
    target_view: Optional[str] = None  # e.g., "dashboard", "ledger", "customers", "inventory", "reports", "reminders", "team", "settings"
    corrected_amount: Optional[float] = None
    corrected_customer_name: Optional[str] = None
    clarification_message: Optional[str] = None
    language: Optional[str] = "hinglish"  # "hindi", "hinglish", "english"

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
    shop_name: Optional[str] = Field("My Store", max_length=120, description="Shop name")
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
    role: Optional[str] = "OWNER"
    shop_id: Optional[int] = None
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


# ==========================================
# Phase 1: Smart Reminders & Transaction Search Schemas
# ==========================================

class ReminderCustomerItem(BaseModel):
    """Customer eligible for payment reminder."""
    customer_name: str
    customer_phone: Optional[str] = None
    outstanding_balance: float
    last_transaction_date: Optional[str] = None
    last_reminder_sent_at: Optional[str] = None
    already_reminded_today: bool = False


class EligibleRemindersResponse(BaseModel):
    """List of all customers eligible for payment reminder."""
    total_eligible: int
    total_outstanding_amount: float
    customers: List[ReminderCustomerItem]


class GenerateReminderRequest(BaseModel):
    """Request to generate a polite payment reminder text."""
    customer_name: str
    customer_phone: Optional[str] = None
    tone: Optional[Literal["polite", "urgent", "formal"]] = "polite"


class GeneratedReminderResponse(BaseModel):
    """Generated reminder text with ready-to-use WhatsApp and SMS URLs."""
    customer_name: str
    customer_phone: Optional[str] = None
    outstanding_balance: float
    message_text: str
    whatsapp_url: str
    sms_url: str


class RecordReminderRequest(BaseModel):
    """Request to record a sent or prepared reminder."""
    customer_name: str
    customer_phone: Optional[str] = None
    balance_amount: float
    message_text: str
    delivery_method: Literal["WHATSAPP", "SMS", "COPY"] = "WHATSAPP"
    delivery_status: Literal["PREPARED", "SENT", "FAILED"] = "SENT"


class ReminderHistoryItem(BaseModel):
    """Record of a past payment reminder."""
    id: int
    customer_name: str
    customer_phone: Optional[str] = None
    balance_snapshot: float
    message_text: str
    delivery_method: str
    delivery_status: str
    created_at: str


class TransactionSearchItem(BaseModel):
    """Individual transaction in search/filtered list."""
    id: int
    customer_name: str
    customer_phone: Optional[str] = None
    notes: Optional[str] = None
    amount: float
    amount_paise: int
    transaction_type: str
    timestamp: str


class TransactionSearchResponse(BaseModel):
    """Paginated transaction search results."""
    total_count: int
    page: int
    page_size: int
    total_pages: int
    total_amount_udhaar: float
    total_amount_jama: float
    transactions: List[TransactionSearchItem]


# ==========================================
# Phase 3: Inventory & Multi-User Access Schemas
# ==========================================

class ProductCreateRequest(BaseModel):
    """Payload to register a new product in the inventory catalog."""
    name: str = Field(..., min_length=1, max_length=150)
    sku: Optional[str] = Field(None, max_length=60)
    category: Optional[str] = Field("General", max_length=80)
    unit: Optional[str] = Field("pcs", max_length=30)
    cost_price: float = Field(0.0, ge=0)
    selling_price: float = Field(0.0, ge=0)
    initial_stock: float = Field(0.0, ge=0)
    low_stock_threshold: float = Field(5.0, ge=0)


class ProductUpdateRequest(BaseModel):
    """Payload to update an inventory product."""
    name: Optional[str] = Field(None, min_length=1, max_length=150)
    sku: Optional[str] = Field(None, max_length=60)
    category: Optional[str] = Field(None, max_length=80)
    unit: Optional[str] = Field(None, max_length=30)
    cost_price: Optional[float] = Field(None, ge=0)
    selling_price: Optional[float] = Field(None, ge=0)
    low_stock_threshold: Optional[float] = Field(None, ge=0)


class ProductResponse(BaseModel):
    """Serialized inventory product item."""
    id: int
    name: str
    sku: Optional[str] = None
    category: str
    unit: str
    cost_price: float
    selling_price: float
    current_stock: float
    low_stock_threshold: float
    is_low_stock: bool
    created_at: str
    updated_at: str


class StockMovementCreateRequest(BaseModel):
    """Payload to record stock movement (received, sold, adjustment, return)."""
    product_id: int
    movement_type: Literal["RECEIVED", "SOLD", "ADJUSTMENT", "RETURN"]
    quantity: float = Field(..., gt=0, description="Absolute quantity to add or remove")
    notes: Optional[str] = Field(None, max_length=255)


class StockMovementResponse(BaseModel):
    """Serialized stock movement log entry."""
    id: int
    product_id: int
    product_name: str
    movement_type: str
    quantity: float
    notes: Optional[str] = None
    resulting_stock: float
    created_at: str


class TeamMemberCreateRequest(BaseModel):
    """Payload for OWNER to invite/create a team member."""
    username: str = Field(..., min_length=3, max_length=60)
    password: str = Field(..., min_length=6, max_length=100)
    owner_name: Optional[str] = Field(None, max_length=120)
    email: Optional[str] = Field(None, max_length=120)
    role: Literal["MANAGER", "STAFF"] = "STAFF"


class TeamRoleUpdateRequest(BaseModel):
    """Payload to modify team member role."""
    role: Literal["MANAGER", "STAFF"]


class TeamMemberResponse(BaseModel):
    """Team member details."""
    id: int
    username: str
    owner_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    created_at: str


# ==========================================
# Phase 4: Audit Logs & Analytics Schemas
# ==========================================

class AuditLogResponse(BaseModel):
    """Serialized audit log entry."""
    id: int
    shop_id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    resource_type: Optional[str] = None
    resource_id: Optional[str] = None
    details_json: Optional[str] = None
    ip_address: Optional[str] = None
    timestamp: str


class DailyAnalyticsSummary(BaseModel):
    """Aggregated metrics for a given date with optional AI explanation."""
    date: str
    total_udhaar: float
    total_jama: float
    net_movement: float
    transaction_count: int
    active_customers_count: int
    top_customer_today: Optional[str] = None
    ai_summary_text: Optional[str] = None


class CustomerDistributionBucket(BaseModel):
    """Distribution bucket for customer debt breakdown."""
    bucket_label: str
    customer_count: int
    total_debt: float


class AnalyticsOverviewResponse(BaseModel):
    """Comprehensive analytics metrics including trends & balance distribution."""
    period_start: str
    period_end: str
    total_udhaar: float
    total_jama: float
    net_flow: float
    total_active_customers: int
    customer_distribution: List[CustomerDistributionBucket]
    top_debtors: List[dict]
    daily_trend: List[dict]


# ==========================================
# Audit & Extended Settings / Email Schemas
# ==========================================

class ShopProfileUpdateRequest(BaseModel):
    """Payload to update shop and owner profile configuration."""
    shop_name: Optional[str] = Field(None, min_length=1, max_length=120)
    owner_name: Optional[str] = Field(None, min_length=1, max_length=120)
    email: Optional[str] = Field(None, max_length=120)
    phone: Optional[str] = Field(None, max_length=20)
    address: Optional[str] = Field(None, max_length=255)
    currency: Optional[str] = Field("INR", max_length=10)
    timezone: Optional[str] = Field("Asia/Kolkata", max_length=40)
    interest_enabled: Optional[bool] = True
    interest_rate_percent: Optional[float] = 2.0
    interest_grace_days: Optional[int] = 15


class ShopProfileResponse(BaseModel):
    """Shop and owner profile information."""
    id: int
    username: str
    shop_name: str
    owner_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    currency: str
    timezone: str
    role: str
    interest_enabled: bool
    interest_rate_percent: float
    interest_grace_days: int


class EmailReminderPreviewRequest(BaseModel):
    """Request to preview an email reminder for a customer."""
    customer_name: str = Field(..., min_length=1, max_length=120)
    customer_email: Optional[str] = Field(None, max_length=120)


class EmailReminderPreviewResponse(BaseModel):
    """Preview of generated email reminder including subject, HTML body, and ledger stats."""
    customer_name: str
    customer_email: Optional[str] = None
    subject: str
    html_content: str
    text_content: str
    balance: float
    principal_balance: float
    accrued_interest: float
    overdue_days: int
    shop_name: str
    owner_name: Optional[str] = None


class EmailReminderSendRequest(BaseModel):
    """Request to dispatch a customer email payment reminder."""
    customer_name: str = Field(..., min_length=1, max_length=120)
    customer_email: str = Field(..., min_length=5, max_length=120)
    subject: Optional[str] = None
    custom_message: Optional[str] = None


class EmailReminderSendResponse(BaseModel):
    """Result of sending an email reminder."""
    status: str  # "ACCEPTED", "FAILED"
    message: str
    recipient: str
    reminder_id: Optional[int] = None
    timestamp: str


class CustomerInterestBreakdown(BaseModel):
    """Overdue interest breakdown for a specific customer."""
    customer_name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    principal_balance: float
    total_balance: float
    accrued_interest: float
    overdue_days: int
    grace_days: int
    overdue_periods: int
    rate_percent: float
    is_overdue: bool
    oldest_unsettled_date: Optional[str] = None


class InterestOverviewResponse(BaseModel):
    """Shop-wide overdue interest summary and customer breakdowns."""
    interest_enabled: bool
    default_rate_percent: float
    grace_period_days: int
    total_principal_outstanding: float
    total_interest_accrued: float
    total_combined_due: float
    overdue_customers_count: int
    breakdowns: List[CustomerInterestBreakdown]

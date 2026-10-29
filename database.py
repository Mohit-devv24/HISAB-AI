"""
Database configuration, session management, and non-destructive schema migrations
for HisabAI using SQLite and SQLAlchemy 2.x.
"""

import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./hisab_ai.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """
    FastAPI dependency that yields a SQLAlchemy database session per request
    and guarantees its closure upon request termination.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(target_engine=None):
    """
    Initializes database tables safely and performs non-destructive schema migration.
    Creates users, user_sessions, and transactions tables with proper ownership
    foreign keys and Google OAuth attributes. No default demo accounts are seeded.
    """
    import models  # Ensures all ORM models are registered with Base.metadata
    eng = target_engine or engine
    Base.metadata.create_all(bind=eng)

    with eng.connect() as conn:
        try:
            # 1. Transactions migration
            result = inspect(conn).get_columns("transactions")
            existing_columns = [column["name"] for column in result]
            if existing_columns and "amount_paise" not in existing_columns:
                conn.execute(text("ALTER TABLE transactions ADD COLUMN amount_paise INTEGER"))
                if "amount" in existing_columns:
                    conn.execute(
                        text("UPDATE transactions SET amount_paise = CAST(ROUND(amount * 100) AS INTEGER) WHERE amount_paise IS NULL")
                    )
            if existing_columns and "user_id" not in existing_columns:
                conn.execute(text("ALTER TABLE transactions ADD COLUMN user_id INTEGER"))
            if existing_columns and "customer_phone" not in existing_columns:
                conn.execute(text("ALTER TABLE transactions ADD COLUMN customer_phone VARCHAR(20)"))
            if existing_columns and "customer_email" not in existing_columns:
                conn.execute(text("ALTER TABLE transactions ADD COLUMN customer_email VARCHAR(120)"))
            if existing_columns and "notes" not in existing_columns:
                conn.execute(text("ALTER TABLE transactions ADD COLUMN notes VARCHAR(255)"))

            # 2. Users table migration for Google OAuth, Owner Profile & Roles & Settings
            user_result = inspect(conn).get_columns("users")
            existing_user_cols = [column["name"] for column in user_result]
            if existing_user_cols and "owner_name" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN owner_name VARCHAR(120)"))
            if existing_user_cols and "email" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN email VARCHAR(120)"))
            if existing_user_cols and "google_id" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN google_id VARCHAR(120)"))
            if existing_user_cols and "role" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'OWNER'"))
            if existing_user_cols and "shop_id" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN shop_id INTEGER"))
            if existing_user_cols and "phone" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN phone VARCHAR(20)"))
            if existing_user_cols and "address" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN address VARCHAR(255)"))
            if existing_user_cols and "currency" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN currency VARCHAR(10) DEFAULT 'INR'"))
            if existing_user_cols and "timezone" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN timezone VARCHAR(40) DEFAULT 'Asia/Kolkata'"))
            if existing_user_cols and "interest_enabled" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN interest_enabled INTEGER DEFAULT 1"))
            if existing_user_cols and "interest_rate_percent" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN interest_rate_percent FLOAT DEFAULT 2.0"))
            if existing_user_cols and "interest_grace_days" not in existing_user_cols:
                conn.execute(text("ALTER TABLE users ADD COLUMN interest_grace_days INTEGER DEFAULT 15"))

            # 3. Payment Reminders table migration
            reminder_result = inspect(conn).get_columns("payment_reminders")
            existing_reminder_cols = [column["name"] for column in reminder_result]
            if existing_reminder_cols and "customer_email" not in existing_reminder_cols:
                conn.execute(text("ALTER TABLE payment_reminders ADD COLUMN customer_email VARCHAR(120)"))
            if existing_reminder_cols and "due_date" not in existing_reminder_cols:
                conn.execute(text("ALTER TABLE payment_reminders ADD COLUMN due_date TIMESTAMP"))
            if existing_reminder_cols and "overdue_days" not in existing_reminder_cols:
                conn.execute(text("ALTER TABLE payment_reminders ADD COLUMN overdue_days INTEGER DEFAULT 0"))
            if existing_reminder_cols and "delivery_details" not in existing_reminder_cols:
                conn.execute(text("ALTER TABLE payment_reminders ADD COLUMN delivery_details VARCHAR(500)"))

            # 4. Payment Verifications table migration
            pv_result = inspect(conn).get_columns("payment_verifications")
            existing_pv_cols = [column["name"] for column in pv_result]
            if existing_pv_cols and "verified_amount_paise" not in existing_pv_cols:
                conn.execute(text("ALTER TABLE payment_verifications ADD COLUMN verified_amount_paise INTEGER"))
            if existing_pv_cols and "verified_amount" not in existing_pv_cols:
                conn.execute(text("ALTER TABLE payment_verifications ADD COLUMN verified_amount FLOAT"))
            if existing_pv_cols and "destination_upi_id" not in existing_pv_cols:
                conn.execute(text("ALTER TABLE payment_verifications ADD COLUMN destination_upi_id VARCHAR(120) DEFAULT 'goyal.com@fam'"))
            if existing_pv_cols and "customer_phone" not in existing_pv_cols:
                conn.execute(text("ALTER TABLE payment_verifications ADD COLUMN customer_phone VARCHAR(20)"))

            conn.commit()
        except Exception:
            conn.rollback()
            raise

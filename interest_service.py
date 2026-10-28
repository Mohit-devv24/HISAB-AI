"""
Overdue Interest Calculation Engine for HisabAI.
Implements the shopkeeper-friendly 15-day grace period rule:
- 0 to 15 days: 0% interest
- For D > 15 days: N = (D - 1) // 15 overdue periods
- Accrued Interest = round(Principal * rate * N, 2)
Uses FIFO ledger matching to determine the aging of unsettled principal.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from models import KOLKATA_TZ, Transaction, TransactionType


def calculate_customer_interest(
    transactions: List[Transaction],
    current_time: Optional[datetime] = None,
    rate_percent: float = 2.0,
    grace_days: int = 15,
) -> Dict[str, any]:
    """
    Calculates outstanding principal, overdue days, overdue periods,
    and accrued interest for a customer's transaction list using FIFO matching.

    Parameters:
      transactions: List of Transaction ORM objects for the customer (sorted or unsorted).
      current_time: Timezone-aware reference datetime in Asia/Kolkata (defaults to now).
      rate_percent: Periodic interest percentage (e.g. 2.0 for 2%).
      grace_days: Grace period before interest begins accruing (e.g. 15 days).

    Returns a dict with:
      - principal_balance: float (₹)
      - principal_paise: int
      - overdue_days: int
      - overdue_periods: int
      - rate_percent: float
      - accrued_interest: float (₹)
      - total_balance: float (₹)
      - is_overdue: bool
      - oldest_unsettled_date: Optional[str] (YYYY-MM-DD)
    """
    if current_time is None:
        current_time = datetime.now(KOLKATA_TZ)
    elif current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=KOLKATA_TZ)
    else:
        current_time = current_time.astimezone(KOLKATA_TZ)

    # Sort transactions chronologically
    sorted_txs = sorted(transactions, key=lambda t: t.timestamp or datetime.min)

    # Calculate net principal balance in paise
    total_udhaar_paise = sum(
        t.amount_paise for t in sorted_txs if t.transaction_type == TransactionType.UDHAAR
    )
    total_jama_paise = sum(
        t.amount_paise for t in sorted_txs if t.transaction_type == TransactionType.JAMA
    )
    net_principal_paise = total_udhaar_paise - total_jama_paise

    if net_principal_paise <= 0:
        return {
            "principal_balance": round(net_principal_paise / 100.0, 2),
            "principal_paise": net_principal_paise,
            "overdue_days": 0,
            "overdue_periods": 0,
            "rate_percent": rate_percent,
            "accrued_interest": 0.0,
            "total_balance": round(net_principal_paise / 100.0, 2),
            "is_overdue": False,
            "oldest_unsettled_date": None,
        }

    # FIFO Matching to find the oldest unsettled UDHAAR transaction
    remaining_jama = total_jama_paise
    oldest_unsettled_tx = None

    for tx in sorted_txs:
        if tx.transaction_type == TransactionType.UDHAAR:
            if remaining_jama >= tx.amount_paise:
                remaining_jama -= tx.amount_paise
            else:
                # This transaction is partially or fully unpaid
                oldest_unsettled_tx = tx
                break

    if oldest_unsettled_tx and oldest_unsettled_tx.timestamp:
        tx_dt = oldest_unsettled_tx.timestamp
        if tx_dt.tzinfo is None:
            tx_dt = tx_dt.replace(tzinfo=KOLKATA_TZ)
        else:
            tx_dt = tx_dt.astimezone(KOLKATA_TZ)

        diff = current_time - tx_dt
        overdue_days = max(0, diff.days)
        oldest_unsettled_date = tx_dt.strftime("%Y-%m-%d")
    else:
        overdue_days = 0
        oldest_unsettled_date = None

    # Apply the 15-day grace period rule:
    # If D <= grace_days: N = 0
    # If D > grace_days: N = (D - 1) // grace_days
    if overdue_days <= grace_days:
        overdue_periods = 0
    else:
        overdue_periods = (overdue_days - 1) // grace_days

    principal_float = round(net_principal_paise / 100.0, 2)
    rate_factor = rate_percent / 100.0
    accrued_interest = round(principal_float * rate_factor * overdue_periods, 2)
    total_balance = round(principal_float + accrued_interest, 2)

    return {
        "principal_balance": principal_float,
        "principal_paise": net_principal_paise,
        "overdue_days": overdue_days,
        "overdue_periods": overdue_periods,
        "rate_percent": rate_percent,
        "accrued_interest": accrued_interest,
        "total_balance": total_balance,
        "is_overdue": overdue_periods > 0,
        "oldest_unsettled_date": oldest_unsettled_date,
    }

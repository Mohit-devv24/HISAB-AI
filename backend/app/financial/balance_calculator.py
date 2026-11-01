from sqlalchemy.orm import Session
from models import Transaction, TransactionType

def calculate_customer_balance(db: Session, customer_name: str, user_id: int) -> int:
    txs = db.query(Transaction).filter(
        Transaction.customer_name == customer_name,
        Transaction.user_id == user_id
    ).all()
    balance_paise = 0
    for tx in txs:
        if tx.type == TransactionType.UDHAAR:
            balance_paise += tx.amount_paise
        else:
            balance_paise -= tx.amount_paise
    return balance_paise

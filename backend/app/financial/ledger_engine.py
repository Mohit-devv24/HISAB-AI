# Pure ledger engine for debit/credit operations with ACID guarantees
from models import Transaction, TransactionType

def record_entry(db, user_id, customer_name, customer_email, amount_paise, tx_type, notes=""):
    tx = Transaction(
        user_id=user_id,
        customer_name=customer_name,
        customer_email=customer_email,
        amount_paise=amount_paise,
        type=tx_type,
        notes=notes
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx

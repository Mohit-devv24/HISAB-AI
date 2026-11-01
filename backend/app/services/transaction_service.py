from models import Transaction

class TransactionService:
    @staticmethod
    def get_history(db, user_id, limit=50, offset=0):
        return db.query(Transaction).filter(Transaction.user_id == user_id).order_by(Transaction.id.desc()).offset(offset).limit(limit).all()

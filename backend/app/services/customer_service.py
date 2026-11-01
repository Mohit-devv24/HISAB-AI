from models import Transaction

class CustomerService:
    @staticmethod
    def list_customers(db, user_id):
        records = db.query(Transaction.customer_name).filter(Transaction.user_id == user_id).distinct().all()
        return [r[0] for r in records if r[0]]

from models import PaymentVerification
class PaymentService:
    @staticmethod
    def list_claims(db, user_id):
        return db.query(PaymentVerification).filter(PaymentVerification.user_id == user_id).all()

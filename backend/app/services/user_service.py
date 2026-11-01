from models import User
class UserService:
    @staticmethod
    def get_by_id(db, user_id):
        return db.query(User).filter(User.id == user_id).first()

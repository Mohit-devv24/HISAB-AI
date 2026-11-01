from models import User
from core.security import hash_password, verify_password

class AuthService:
    @staticmethod
    def authenticate(db, email, password):
        user = db.query(User).filter(User.email == email.strip().lower()).first()
        if not user or not user.hashed_password:
            return None
        if verify_password(password, user.hashed_password):
            return user
        return None

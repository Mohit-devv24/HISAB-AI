import hashlib
import secrets

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    pw_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return f"{salt}"

def verify_password(password: str, hashed_password: str) -> bool:
    try:
        salt, pw_hash = hashed_password.split("$", 1)
        expected_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
        return secrets.compare_digest(pw_hash, expected_hash)
    except Exception:
        return False

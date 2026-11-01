# Idempotency token manager
_token_cache = set()

def check_idempotency_token(token: str) -> bool:
    if not token:
        return True
    if token in _token_cache:
        return False
    _token_cache.add(token)
    return True

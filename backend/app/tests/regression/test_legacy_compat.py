from database import get_db

def test_legacy_get_db():
    db = next(get_db())
    assert db is not None
    db.close()

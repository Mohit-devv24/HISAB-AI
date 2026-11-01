from models import AuditLog
class AuditService:
    @staticmethod
    def record(db, user_id, action, entity_type, entity_id, details=None):
        log = AuditLog(user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id, details=details)
        db.add(log)
        db.commit()

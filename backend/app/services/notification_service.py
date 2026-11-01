from models import Reminder
class NotificationService:
    @staticmethod
    def get_pending_reminders(db, user_id):
        return db.query(Reminder).filter(Reminder.user_id == user_id).all()

class DashboardService:
    @staticmethod
    def get_overview(db, user_id):
        return {"status": "ok", "user_id": user_id}

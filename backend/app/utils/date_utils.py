from datetime import datetime
from zoneinfo import ZoneInfo

KOLKATA_TZ = ZoneInfo("Asia/Kolkata")

def get_current_ist_time() -> datetime:
    return datetime.now(KOLKATA_TZ)

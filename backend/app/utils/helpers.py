import re
import html

def sanitize_input(text: str) -> str:
    if not text:
        return ""
    # Strip potential CSV injection triggers
    cleaned = text.strip()
    if cleaned and cleaned[0] in ('=', '+', '-', '@'):
        cleaned = "'" + cleaned
    return html.escape(cleaned)

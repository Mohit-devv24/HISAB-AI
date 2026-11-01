# Speech synthesis response generator
def format_tts_acknowledgement(customer: str, amount: float, tx_type: str) -> str:
    return f"{customer} ke {amount} rupaye {tx_type.lower()} note kar liye gaye hain."

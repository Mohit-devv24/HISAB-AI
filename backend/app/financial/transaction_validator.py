def validate_financial_limits(amount_paise: int):
    if amount_paise <= 0:
        raise ValueError("Transaction amount must be strictly greater than zero.")
    if amount_paise > 100_00_00_000:
        raise ValueError("Transaction amount exceeds single entry limit (₹1,00,00,000).")

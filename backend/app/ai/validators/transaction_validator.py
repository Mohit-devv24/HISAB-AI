def validate_extracted_transaction(data):
    if not data.customer_name:
        raise ValueError("Customer name is required.")
    if data.amount_paise <= 0:
        raise ValueError("Amount must be positive.")
    return True

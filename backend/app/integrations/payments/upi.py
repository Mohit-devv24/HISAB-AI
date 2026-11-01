def generate_upi_payment_link(upi_id: str, amount: float, payee_name: str) -> str:
    return f"upi://pay?pa={upi_id}&pn={payee_name}&am={amount:.2f}&cu=INR"

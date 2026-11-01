def paise_to_rupees(paise: int) -> float:
    return round(paise / 100.0, 2)

def rupees_to_paise(rupees: float) -> int:
    return int(round(float(rupees) * 100))

def format_inr(rupees: float) -> str:
    return f"₹{rupees:,.2f}"

from models import PaymentVerification

def verify_payment_claim(claim: PaymentVerification, approved_amount_paise: int):
    claim.status = "approved"
    claim.approved_amount_paise = approved_amount_paise
    return claim

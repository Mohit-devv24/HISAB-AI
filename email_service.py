"""
Email notification service using Brevo SMTP for HisabAI payment reminders.
Handles SMTP connection, STARTTLS authentication, template generation,
and robust error reporting.
"""

from email.message import EmailMessage
import html
import os
import re
import smtplib
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv

load_dotenv()

# Brevo SMTP Configuration
BREVO_SMTP_HOST = os.getenv("BREVO_SMTP_HOST", "smtp-relay.brevo.com")
BREVO_SMTP_PORT = int(os.getenv("BREVO_SMTP_PORT", "587"))
BREVO_SMTP_LOGIN = os.getenv("BREVO_SMTP_LOGIN", "")
BREVO_SMTP_KEY = os.getenv("BREVO_SMTP_KEY", "")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "notifications@hisabai.com")

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def is_valid_email(email: Optional[str]) -> bool:
    """Validates email format using regex."""
    if not email:
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


def generate_reminder_email_content(
    customer_name: str,
    principal_balance: float,
    accrued_interest: float,
    total_balance: float,
    overdue_days: int,
    shop_name: str,
    owner_name: Optional[str] = None,
    shop_phone: Optional[str] = None,
    custom_message: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generates structured HTML and plain-text email reminder content with
    bilingual English/Hindi instructions and transparent ledger breakdown.
    """
    owner_display = owner_name or "Store Owner"
    phone_display = f" | Phone: {shop_phone}" if shop_phone else ""
    subject = f"Payment Reminder: Outstanding balance at {shop_name} - ₹{total_balance:.2f}"

    interest_row = ""
    interest_text = ""
    if accrued_interest > 0:
        interest_row = f"""
        <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #4a5568;">Overdue Interest ({overdue_days} days):</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; color: #b91c1c; text-align: right;">+ ₹{accrued_interest:.2f}</td>
        </tr>
        """
        interest_text = f"Accrued Overdue Interest ({overdue_days} days): +₹{accrued_interest:.2f}\n"

    custom_note_html = ""
    custom_note_text = ""
    if custom_message:
        custom_note_html = f"""
        <div style="background-color: #f7fafc; border-left: 4px solid #5B0E14; padding: 12px 16px; margin: 20px 0; border-radius: 4px;">
            <p style="margin: 0; color: #2d3748; font-style: italic;">"{custom_message}"</p>
        </div>
        """
        custom_note_text = f"\nShopkeeper Note: {custom_message}\n"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{subject}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
  <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="background-color: #5B0E14; padding: 24px; text-align: center; color: #ffffff;">
      <h1 style="margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">{shop_name}</h1>
      <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.9;">Digital Khata Payment Reminder</p>
    </div>
    
    <div style="padding: 24px 28px;">
      <p style="font-size: 15px; margin-top: 0;">Dear <strong>{customer_name}</strong>,</p>
      
      <p style="font-size: 14px; line-height: 1.6; color: #475569;">
        This is a friendly reminder regarding your outstanding khata balance with <strong>{shop_name}</strong>.
        Please review the account details below:
      </p>

      <div style="background-color: #f7fafc; border-left: 4px solid #5B0E14; padding: 12px 16px; margin: 20px 0; border-radius: 4px;"><p style="margin: 0; color: #2d3748; font-weight: 600;">Payment Reminder</p><p style="margin: 6px 0 0; color: #475569;">Please clear your outstanding balance at your earliest convenience. If you have already paid, please contact the store.</p></div>

      <table style="width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px; background: #fdfdfe; border-radius: 8px; border: 1px solid #edf2f7;">
        <tbody>
          <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #4a5568;">Principal Udhaar:</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; text-align: right;">₹{principal_balance:.2f}</td>
          </tr>
          {interest_row}
          <tr style="background-color: #f8fafc;">
            <td style="padding: 12px 14px; font-weight: 700; color: #0f172a; font-size: 15px;">Total Payable Amount:</td>
            <td style="padding: 12px 14px; font-weight: 800; color: #5B0E14; text-align: right; font-size: 17px;">₹{total_balance:.2f}</td>
          </tr>
        </tbody>
      </table>

      <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 14px; margin: 20px 0; text-align: center;">
        <p style="margin: 0; color: #166534; font-size: 13px; font-weight: 500;">
          कृपया शीघ्रातिशीघ्र बकाया राशि का भुगतान करें। धन्यवाद!
        </p>
      </div>

      <p style="font-size: 13px; color: #64748b; line-height: 1.5;">
        You can settle this amount via UPI, cash, or direct store visit.
        If you have already made the payment, please contact us to update your records.
      </p>

      <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0 16px;">
      
      <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">
        {shop_name} &bull; {owner_display}{phone_display}<br>
        Powered by HisabAI &bull; Intelligent Kirana Ledger
      </p>
    </div>
  </div>
</body>
</html>"""

    text_content = (
        f"Payment Reminder from {shop_name}\n"
        f"Dear {customer_name},\n\n"
        f"This is a reminder regarding your khata balance with {shop_name}.\n"
        f"{custom_note_text}\n"
        f"Principal Udhaar: ₹{principal_balance:.2f}\n"
        f"{interest_text}"
        f"Total Payable Amount: ₹{total_balance:.2f}\n\n"
        f"कृपया शीघ्रातिशीघ्र बकाया राशि का भुगतान करें।\n\n"
        f"Store: {shop_name}\n"
        f"Owner: {owner_display}{phone_display}\n"
        f"Powered by HisabAI"
    )

    return {
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }


def generate_transaction_receipt_email_content(
    customer_name: str,
    amount: float,
    transaction_type: str,
    transaction_id: int,
    timestamp_str: str,
    shop_name: str,
    owner_name: Optional[str] = None,
    shop_phone: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, str]:
    """
    Generates structured HTML and plain-text transaction receipt email content
    with bilingual Hindi/English clarity, transaction reference ID, and HisabAI branding (#5B0E14).
    """
    safe_customer = html.escape(customer_name)
    safe_shop = html.escape(shop_name)
    safe_owner = html.escape(owner_name or "Store Owner")
    safe_notes = html.escape(notes) if notes else ""
    phone_display = f" | Phone: {html.escape(shop_phone)}" if shop_phone else ""

    is_udhaar = transaction_type.upper() == "UDHAAR"
    type_label_en = "Credit (Udhaar Given)" if is_udhaar else "Payment Received (Jama)"
    type_label_hi = "उधार दिया गया" if is_udhaar else "जमा प्राप्त हुआ"
    badge_bg = "#fef2f2" if is_udhaar else "#f0fdf4"
    badge_color = "#b91c1c" if is_udhaar else "#166534"
    badge_border = "#fecaca" if is_udhaar else "#bbf7d0"

    subject = f"Receipt: ₹{amount:.2f} ({'Credit' if is_udhaar else 'Payment'}) at {shop_name} [Ref #TXN-{transaction_id:06d}]"

    notes_html = ""
    notes_text = ""
    if safe_notes:
        notes_html = f"""
        <tr>
          <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #4a5568;">Notes / Items:</td>
          <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 500; text-align: right;">{safe_notes}</td>
        </tr>
        """
        notes_text = f"Notes: {notes}\n"

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{html.escape(subject)}</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
  <div style="max-width: 560px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e2e8f0; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="background-color: #5B0E14; padding: 24px; text-align: center; color: #ffffff;">
      <h1 style="margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">{safe_shop}</h1>
      <p style="margin: 4px 0 0; font-size: 13px; opacity: 0.9;">Official Transaction Receipt</p>
    </div>

    <div style="padding: 24px 28px;">
      <p style="font-size: 15px; margin-top: 0;">Dear <strong>{safe_customer}</strong>,</p>

      <p style="font-size: 14px; line-height: 1.6; color: #475569;">
        This is an official receipt for the transaction recorded in your khata account at <strong>{safe_shop}</strong>.
      </p>

      <div style="background: {badge_bg}; border: 1px solid {badge_border}; border-radius: 8px; padding: 12px 16px; margin: 16px 0; display: flex; justify-content: space-between; align-items: center;">
        <span style="font-size: 14px; font-weight: 600; color: {badge_color};">{type_label_en} ({type_label_hi})</span>
        <span style="font-size: 18px; font-weight: 800; color: {badge_color};">₹{amount:.2f}</span>
      </div>

      <table style="width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px; background: #fdfdfe; border-radius: 8px; border: 1px solid #edf2f7;">
        <tbody>
          <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #4a5568;">Transaction Ref:</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; text-align: right; font-family: monospace;">TXN-{transaction_id:06d}</td>
          </tr>
          <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #4a5568;">Date & Time:</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 500; text-align: right;">{html.escape(timestamp_str)}</td>
          </tr>
          <tr>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; color: #4a5568;">Transaction Type:</td>
            <td style="padding: 10px 14px; border-bottom: 1px solid #e2e8f0; font-weight: 600; text-align: right; color: {badge_color};">{type_label_en}</td>
          </tr>
          {notes_html}
          <tr style="background-color: #f8fafc;">
            <td style="padding: 12px 14px; font-weight: 700; color: #0f172a; font-size: 15px;">Recorded Amount:</td>
            <td style="padding: 12px 14px; font-weight: 800; color: #5B0E14; text-align: right; font-size: 17px;">₹{amount:.2f}</td>
          </tr>
        </tbody>
      </table>

      <div style="background-color: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 14px; margin: 20px 0; text-align: center;">
        <p style="margin: 0; color: #166534; font-size: 13px; font-weight: 500;">
          आपके खाते में यह लेनदेन सफलतापूर्वक दर्ज कर लिया गया है। धन्यवाद!
        </p>
      </div>

      <p style="font-size: 12px; color: #64748b; line-height: 1.5;">
        If you have any questions or dispute regarding this entry, please contact the store owner directly.
      </p>

      <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 24px 0 16px;">

      <p style="font-size: 12px; color: #94a3b8; text-align: center; margin: 0;">
        {safe_shop} &bull; {safe_owner}{phone_display}<br>
        Powered by HisabAI &bull; Intelligent Digital Khata
      </p>
    </div>
  </div>
</body>
</html>"""

    text_content = (
        f"Transaction Receipt from {shop_name}\n"
        f"Dear {customer_name},\n\n"
        f"Transaction Ref: TXN-{transaction_id:06d}\n"
        f"Date: {timestamp_str}\n"
        f"Type: {type_label_en}\n"
        f"Amount: ₹{amount:.2f}\n"
        f"{notes_text}\n"
        f"आपके खाते में यह लेनदेन सफलतापूर्वक दर्ज कर लिया गया है।\n\n"
        f"Store: {shop_name}\n"
        f"Owner: {owner_name or 'Store Owner'}{f' | Phone: {shop_phone}' if shop_phone else ''}\n"
        f"Powered by HisabAI"
    )

    return {
        "subject": subject,
        "html": html_content,
        "text": text_content,
    }


def send_brevo_email(
    to_email: str,
    subject: str,
    html_content: str,
    text_content: str,
    sender_email: Optional[str] = None,
    sender_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Transmits an email via Brevo SMTP over TLS.
    Returns (success: bool, status_message: str).
    """
    if not is_valid_email(to_email):
        return False, f"Invalid recipient email format: '{to_email}'"

    host = BREVO_SMTP_HOST
    port = BREVO_SMTP_PORT
    login = BREVO_SMTP_LOGIN
    key = BREVO_SMTP_KEY
    from_addr = sender_email or BREVO_SENDER_EMAIL

    if not login or not key:
        return False, "Brevo SMTP credentials are not configured in environment (BREVO_SMTP_LOGIN, BREVO_SMTP_KEY)"

    msg = EmailMessage()
    msg["Subject"] = subject
    if sender_name:
        msg["From"] = f"{sender_name} <{from_addr}>"
    else:
        msg["From"] = from_addr
    msg["To"] = to_email

    msg.set_content(text_content)
    msg.add_alternative(html_content, subtype="html")

    try:
        # Establish connection with timeout
        with smtplib.SMTP(host=host, port=port, timeout=12) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(login, key)
            server.send_message(msg)
        return True, "Email delivered successfully via Brevo SMTP."
    except smtplib.SMTPAuthenticationError as auth_err:
        return False, f"Brevo SMTP authentication failed: {str(auth_err)}"
    except smtplib.SMTPException as smtp_err:
        return False, f"Brevo SMTP dispatch error: {str(smtp_err)}"
    except OSError as net_err:
        # Catches network unreachable, DNS, or sandbox socket barriers
        return False, f"Network connection to Brevo SMTP server ({host}:{port}) failed: {str(net_err)}"
    except Exception as exc:
        return False, f"Unexpected error delivering email: {str(exc)}"

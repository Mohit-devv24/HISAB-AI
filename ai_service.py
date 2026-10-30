"""
AI Integration service for HisabAI using the official Google GenAI SDK (google-genai).
Extracts structured transaction details and classifies voice assistant intents
from natural Hindi/Hinglish commands with multi-tier model fallback.
"""

import json
import logging
import os
from pathlib import Path
import re
from typing import List, Optional
from dotenv import load_dotenv
from pydantic import ValidationError

from models import ExtractedTransaction, VoiceIntentResult, VoiceIntentType

# Resolve project root and .env path reliably
PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATH = PROJECT_ROOT / ".env"

# Load environment variables on module import
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)
else:
    load_dotenv(override=False)

logger = logging.getLogger("HisabAI.AI")

# System prompt for standard transaction extraction
SYSTEM_INSTRUCTION = (
    "You are an AI assistant for an Indian shopkeeper. Extract customer_name, amount, "
    "and transaction_type from the provided Hinglish transaction command.\n\n"
    "Rules:\n"
    "- UDHAAR means goods or services were supplied on credit (customer debt increases).\n"
    "- JAMA means money was received from the customer (customer debt decreases).\n"
    "- Return ONLY valid JSON matching this structure: "
    '{"customer_name": "string", "amount": number, "transaction_type": "UDHAAR" | "JAMA"}\n'
    "- Do not invent customer names, amounts, payment events, or transaction direction.\n"
    "- If essential information (such as who or how much) is missing or ambiguous, "
    'return JSON with an error message: {"error": "detailed explanation of what is missing"}.'
)

# System prompt for conversational voice intent classification
VOICE_INTENT_SYSTEM_PROMPT = (
    "You are the Voice AI brain for 'HisabAI', an Indian shopkeeper digital Khata ledger.\n"
    "Analyze the voice command, detect the language, and extract intent and details.\n\n"
    "Language Matching Rules:\n"
    "- If the user speaks in English, set language to 'english'.\n"
    "- If the user speaks in Hindi script (Devanagari), set language to 'hindi'.\n"
    "- If the user speaks in Hinglish, set language to 'hinglish'.\n\n"
    "Available Intents:\n"
    "- NEW_TRANSACTION / ENTRY: Recording credit given (UDHAAR / CREDIT) or payment received (JAMA / PAYMENT). E.g. 'Rahul ko 500 udhaar diya', 'Goyal ka 2000 udhaar', 'Ramesh ne 250 jama kiye'.\n"
    "- CORRECTION: Correcting details of an unconfirmed or pending transaction. E.g. 'Nahin Mel ID change karo', 'Nahi, email ID change karo. newemail@gmail.com', '500 nahi 800', 'Amount 2000 karo'. Set 'correction_target' to 'email', 'amount', 'customer_name', or 'type'.\n"
    "- CONFIRMATION / CONFIRM: Explicit confirmation. E.g. 'Haan, save kar do', 'Confirm karo', 'Theek hai', 'Yes, confirm'.\n"
    "- CANCELLATION / CANCEL: Explicit cancellation. E.g. 'Nahi, cancel kar do', 'Radd karo', 'Mat save karo', 'No, cancel it'.\n"
    "- QUERY_CUSTOMER_BALANCE: Checking balance or khata of a customer. E.g. 'Sharma ji ka kitna udhaar baaki hai?', 'Rahul ka hisab check karo'.\n"
    "- QUERY_DAILY_SUMMARY: Asking for today's summary or collection. E.g. 'Show me today\'s total collection', 'Aaj kitna jama hua?'.\n"
    "- QUERY_DATE_RANGE_SUMMARY: Historical report. E.g. 'Kal ki report dikhao', 'Pichhle saat din ka hisab'.\n"
    "- QUERY_CUSTOMER_LIST: Customers who owe money. E.g. 'Kis-kis customer ke paise baaki hain?'.\n"
    "- QUERY_CUSTOMER_HISTORY: Transaction history. E.g. 'Raju ka purana hisaab dikhao'.\n"
    "- AMBIGUOUS_ACCOUNT_ACTION: Ambiguous phrases about ending/closing/deleting a khata or account without explicitly saying settle (e.g. 'Tausif ka khata khatam kar do'). Must ask clarification instead of settling.\n"
    "- SETTLE_BALANCE: Explicit instruction to settle customer balance. E.g. 'Tausif ka balance settle karo'.\n"
    "- NAVIGATE_VIEW: Navigating to a specific screen in the app. E.g. 'ledger book kholo', 'open inventory'.\n"
    "- REPEAT_LAST_RESPONSE: Asking to repeat. E.g. 'Dobara sunao', 'Repeat karo'.\n"
    "- STOP_ASSISTANT: Telling assistant to sleep. E.g. 'Bas, assistant band kar do'.\n"
    "- GREETING: Common greetings like 'कैसे हो', 'kaise ho', 'namaste', 'hello', 'kya haal hai'. Return intent 'GREETING' with friendly clarification_message explaining you can help with balances, credit/payment entries, and summaries.\n"
    "- UNKNOWN: Anything unrelated or ambiguous.\n\n"
    "Context Awareness Rules:\n"
    "- When Active Context shows state 'AWAITING_CONFIRMATION', interpret modification phrases ('nahi email badlo', 'change amount', 'mel id change karo') as 'CORRECTION'.\n"
    "- If amount is missing in a new transaction: set intent to 'UNKNOWN', with clarification_message: 'Kitna amount likhna hai?'\n"
    "- If customer name is missing: set clarification_message: 'Kaunse customer ke naam par likhna hai?'\n\n"
    "Return ONLY valid JSON in this schema:\n"
    '{\n'
    '  "intent": "NEW_TRANSACTION"|"CORRECTION"|"CONFIRMATION"|"CANCELLATION"|"QUERY_CUSTOMER_BALANCE"|"QUERY_CUSTOMER_HISTORY"|"QUERY_DAILY_SUMMARY"|"QUERY_DATE_RANGE_SUMMARY"|"QUERY_CUSTOMER_LIST"|"AMBIGUOUS_ACCOUNT_ACTION"|"SETTLE_BALANCE"|"NAVIGATE_VIEW"|"REPEAT_LAST_RESPONSE"|"STOP_ASSISTANT"|"GREETING"|"UNKNOWN",\n'
    '  "correction_target": "email"|"amount"|"customer_name"|"type"|null,\n'
    '  "entities": {\n'
    '    "customer_name": "string or null",\n'
    '    "amount": number or null,\n'
    '    "email": "string or null",\n'
    '    "type": "CREDIT"|"PAYMENT"|null\n'
    '  },\n'
    '  "customer_name": "string or null",\n'
    '  "amount": number or null,\n'
    '  "customer_email": "string or null",\n'
    '  "transaction_type": "UDHAAR"|"JAMA"|null,\n'
    '  "target_view": "dashboard"|"ledger"|"customers"|"inventory"|"reminders"|"reports"|"settings"|"team"|"verifications"|null,\n'
    '  "date_filter": "today"|"yesterday"|"last_7_days"|"this_month"|null,\n'
    '  "corrected_amount": number or null,\n'
    '  "clarification_message": "string or null",\n'
    '  "language": "hindi"|"hinglish"|"english"\n'
    '}'
)

# Modern Google Gemma & Gemini models supported by Google GenAI v1beta API
RECOMMENDED_MODELS = [
    "gemma-4-26b-a4b-it",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite-preview",
    "gemma-4-31b-it",
    "gemini-3.8-flash",
]

# Legacy / deprecated model migration map
LEGACY_MODEL_MAP = {
    "gemma-2-9b-it": "gemma-4-26b-a4b-it",
    "gemma-2-27b-it": "gemma-4-31b-it",
    "gemini-1.5-flash": "gemini-flash-lite-latest",
    "gemini-2.0-flash": "gemini-flash-lite-latest",
}


# ==========================================
# Domain Specific Exception Hierarchy
# ==========================================

class AIConfigurationError(Exception):
    """Raised when GEMMA_API_KEY is missing, blank, or placeholder."""
    pass


class AIAmbiguityError(ValueError):
    """Raised when the user Hinglish command lacks essential details."""
    pass


class AIProviderError(Exception):
    """Raised when upstream Google GenAI API fails due to network, rate limits, or server errors."""
    pass


# ==========================================
# Helper Utilities
# ==========================================

def redact_secrets(message: str) -> str:
    """
    Sanitizes string to remove any API keys or credential patterns before logging or returning to client.
    """
    if not message:
        return ""
    redacted = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", str(message))
    redacted = re.sub(r"AQ\.[0-9A-Za-z-_]{30,}", "[REDACTED_API_KEY]", redacted)
    redacted = re.sub(r"key=[0-9A-Za-z-_]+", "key=[REDACTED]", redacted)
    return redacted


def get_configured_api_key() -> Optional[str]:
    """
    Retrieves the Google GenAI / Gemma API key.
    Reloads .env from project root if not found in os.environ.
    """
    key = (
        os.getenv("GEMMA_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    if not key or len(key.strip()) <= 8:
        if ENV_PATH.exists():
            load_dotenv(dotenv_path=ENV_PATH, override=True)
            key = (
                os.getenv("GEMMA_API_KEY")
                or os.getenv("GOOGLE_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
    return key.strip() if key else None


def is_ai_configured() -> bool:
    """Returns True if a valid API key is present in environment."""
    key = get_configured_api_key()
    return bool(key and len(key) > 8 and not key.startswith("your_"))


def get_active_model_name() -> str:
    """Returns the effective primary model name configured for the app."""
    raw_model = os.getenv("GEMMA_MODEL", "gemma-4-26b-a4b-it").strip()
    return LEGACY_MODEL_MAP.get(raw_model, raw_model)


def get_candidate_models() -> List[str]:
    """Builds an ordered list of candidate models with modern fallbacks (bounded to 3 candidates)."""
    configured = get_active_model_name()
    candidates = [configured]
    for model in ["gemini-2.0-flash", "gemini-1.5-flash"] + RECOMMENDED_MODELS:
        if model not in candidates:
            candidates.append(model)
        if len(candidates) >= 3:
            break
    return candidates


def strip_markdown_fences(raw_text: str) -> str:
    """
    Strips markdown code blocks (```json or ```) and surrounding whitespace
    from LLM responses before JSON decoding.
    """
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        return match.group(0).strip()
    return cleaned.strip()


# ==========================================
# Hindi & English Speech & Number Normalization
# ==========================================

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

ENGLISH_NUMBER_WORDS = {
    r"\bten\s*thousand\b": "10000",
    r"\bfive\s*thousand\b": "5000",
    r"\bthree\s*thousand\b": "3000",
    r"\btwo\s*thousand\s*five\s*hundred\b": "2500",
    r"\btwo\s*thousand\b": "2000",
    r"\bone\s*thousand\s*five\s*hundred\b": "1500",
    r"\bone\s*thousand\b": "1000",
    r"\bnine\s*hundred\b": "900",
    r"\beight\s*hundred\b": "800",
    r"\bseven\s*hundred\b": "700",
    r"\bsix\s*hundred\b": "600",
    r"\bfive\s*hundred\s*fifty\b": "550",
    r"\bfive\s*hundred\b": "500",
    r"\bfour\s*hundred\b": "400",
    r"\bthree\s*hundred\b": "300",
    r"\btwo\s*hundred\s*fifty\b": "250",
    r"\btwo\s*hundred\b": "200",
    r"\bone\s*hundred\s*fifty\b": "150",
    r"\bone\s*hundred\b": "100",
    r"\bfifty\b": "50",
}

HINDI_NUMBER_WORDS = {
    # ── Devanagari composites (most specific first) ─────────────────────────
    # NOTE: \b does not match Unicode word boundaries in Python re; using (?<![\w\u0900-\u097F]) style instead
    r"(?<![\u0900-\u097F\w])दस\s*हज़?ार(?![\u0900-\u097F\w])": "10000",
    r"(?<![\u0900-\u097F\w])पाँच\s*हज़?ार(?![\u0900-\u097F\w])": "5000",
    r"(?<![\u0900-\u097F\w])पांच\s*हज़?ार(?![\u0900-\u097F\w])": "5000",
    r"(?<![\u0900-\u097F\w])चार\s*हज़?ार(?![\u0900-\u097F\w])": "4000",
    r"(?<![\u0900-\u097F\w])तीन\s*हज़?ार(?![\u0900-\u097F\w])": "3000",
    r"(?<![\u0900-\u097F\w])दो\s*हज़?ार\s*पाँच\s*सौ(?![\u0900-\u097F\w])": "2500",
    r"(?<![\u0900-\u097F\w])दो\s*हज़?ार\s*पांच\s*सौ(?![\u0900-\u097F\w])": "2500",
    r"(?<![\u0900-\u097F\w])दो\s*हज़?ार(?![\u0900-\u097F\w])": "2000",
    r"(?<![\u0900-\u097F\w])एक\s*हज़?ार\s*पाँच\s*सौ(?![\u0900-\u097F\w])": "1500",
    r"(?<![\u0900-\u097F\w])एक\s*हज़?ार\s*पांच\s*सौ(?![\u0900-\u097F\w])": "1500",
    r"(?<![\u0900-\u097F\w])एक\s*हज़?ार(?![\u0900-\u097F\w])": "1000",
    # Devanagari hundreds
    r"ढाई\s*सौ": "250",
    r"डेढ़\s*सौ": "150",
    r"डेढ\s*सौ": "150",
    r"नौ\s*सौ": "900",
    r"आठ\s*सौ": "800",
    r"सात\s*सौ": "700",
    r"पाँच\s*सौ\s*पचास": "550",
    r"पांच\s*सौ\s*पचास": "550",
    r"पाँच\s*सौ": "500",
    r"पांच\s*सौ": "500",
    r"चार\s*सौ": "400",
    r"तीन\s*सौ": "300",
    r"दो\s*सौ": "200",
    r"एक\s*सौ": "100",
    r"(?<![\u0900-\u097F\w])पचास(?![\u0900-\u097F\w])": "50",
    r"(?<![\u0900-\u097F\w])सौ(?![\u0900-\u097F\w])": "100",
    # ── Latin transliterations (composites first) ───────────────────────────
    r"\b(?:ek|1)\s*haza?ar\s*(?:ek|1)\s*sau\b": "1100",
    r"\b(?:ek|1)\s*haza?ar\s*(?:do|2)\s*sau\b": "1200",
    r"\b(?:ek|1)\s*haza?ar\s*(?:teen|3)\s*sau\b": "1300",
    r"\b(?:ek|1)\s*haza?ar\s*(?:char|chaar|4)\s*sau\b": "1400",
    r"\b(?:ek|1)\s*haza?ar\s*(?:paanch|panch|5)\s*sau\b": "1500",
    r"\b(?:do|2)\s*haza?ar\s*(?:paanch|panch|5)\s*sau\b": "2500",
    # Thousands
    r"\b(ek|1)\s*haza?ar\b": "1000",
    r"\b(do|2)\s*haza?ar\b": "2000",
    r"\b(teen|3)\s*haza?ar\b": "3000",
    r"\b(char|chaar|4)\s*haza?ar\b": "4000",
    r"\b(paanch|panch|5)\s*haza?ar\b": "5000",
    r"\bdas\s*haza?ar\b": "10000",
    r"\b(dus|10)\s*haza?ar\b": "10000",
    # Hundreds
    r"\bpaanch\s*sau\s*pachaas\b": "550",
    r"\bpaanch\s*sau\b": "500",
    r"\bpanch\s*sau\b": "500",
    r"\bchar\s*sau\b": "400",
    r"\bchaar\s*sau\b": "400",
    r"\bteen\s*sau\b": "300",
    r"\bdo\s*sau\b": "200",
    r"\bek\s*sau\b": "100",
    r"\bdhai\s*sau\b": "250",
    r"\bdhayi\s*sau\b": "250",
    r"\bdedh\s*sau\b": "150",
    r"\bdeedh\s*sau\b": "150",
    r"\bbarah\s*sau\b": "1200",
    r"\bgyarah\s*sau\b": "1100",
    r"\bterah\s*sau\b": "1300",
    r"\bchaudah\s*sau\b": "1400",
    r"\bpandrah\s*sau\b": "1500",
    r"\bsaade\s*saat\s*sau\b": "750",
    r"\bsaat\s*sau\b": "700",
    r"\baath\s*sau\b": "800",
    r"\bnau\s*sau\b": "900",
    # Tens
    r"\bpachaas\b": "50",
    r"\bpachas\b": "50",
    r"\bsaath\b": "60",
    r"\bsattar\b": "70",
    r"\bassi\b": "80",
    r"\bnabbe\b": "90",
    r"\bchaalis\b": "40",
    r"\btees\b": "30",
    r"\bbees\b": "20",
    r"\bdas\b": "10",
}


def detect_input_language(text: str) -> str:
    """
    Detects whether user utterance is Hindi (Devanagari), English, or Hinglish.
    """
    if not text:
        return "hinglish"
    if re.search(r"[\u0900-\u097F]", text):
        return "hindi"

    lower = text.lower()
    english_keywords = [
        "show me", "today's", "collection", "total", "what is", "how much",
        "balance", "credit", "payment", "received", "customer", "please",
        "record", "give", "clear", "settle", "report", "yesterday", "summary",
        "last 7 days", "this month", "history", "owes", "due", "account",
        "yes", "no", "cancel", "confirm"
    ]
    hindi_hinglish_keywords = [
        "udhaar", "jama", "baaki", "baki", "hisaab", "hisab", "khata", "likh", "likho",
        "diya", "diye", "aaj", "kal", "kitna", "kisko", "kis-kis", "kis kis",
        "karo", "kar do", "khatam", "chukta", "sunao", "batao", "bata", "hai", "hain",
        "ka", "ke", "ki", "ko", "ne", "se", "mein", "pehle", "pichhla", "pichhle",
        "haan", "nahi", "theek"
    ]

    en_count = sum(1 for w in english_keywords if re.search(r"\b" + re.escape(w) + r"\b", lower))
    hi_count = sum(1 for w in hindi_hinglish_keywords if re.search(r"\b" + re.escape(w) + r"\b", lower))

    if en_count > 0 and hi_count == 0:
        return "english"
    if hi_count > 0 and en_count == 0:
        return "hinglish"
    if en_count > hi_count:
        return "english"
    return "hinglish"


def normalize_hindi_number_words(text: str) -> str:
    """Replaces common spoken Hindi & English number idioms and Devanagari numerals with digit equivalents.
    Also strips ₹, Rs., or INR currency symbol prefix before digits (e.g. '₹2000' → '2000').
    """
    if not text:
        return ""
    # Convert Devanagari digits ०-९ to 0-9
    normalized = text.translate(DEVANAGARI_DIGITS)
    # Pre-process: strip ₹ or Rs prefix attached to digits
    normalized = re.sub(r'(?:₹|rs\.?|inr)\s*(\d+)', r'\1', normalized, flags=re.IGNORECASE)
    for pattern, replacement in HINDI_NUMBER_WORDS.items():
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    for pattern, replacement in ENGLISH_NUMBER_WORDS.items():
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def strip_wake_word(text: str) -> str:
    """
    Strips leading wake word variations ('Suno Hisab', 'Suno Hisaab', 'सुनो हिसाब')
    and common leading punctuation/whitespace.
    """
    cleaned = text.strip()
    wake_patterns = [
        r"^(?:hey\s+)?suno\s*hisa+b[\s,:\.-]*",
        r"^\u0938\u0941\u0928\u094b\s*\u0939\u093f\u0938\u093e+\u092c[\s,:\.-]*",
        r"^\u0938\u0941\u0928\u094b\u0939\u093f\u0938\u093e\u092c[\s,:\.-]*",
        r"^suno\s*hisaab[\s,:\.-]*",
    ]
    for pat in wake_patterns:
        cleaned = re.sub(pat, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


# ==========================================
# Fast Path Deterministic Matching
# ==========================================

def extract_email_from_text(raw_text: str) -> Optional[str]:
    """
    Extracts an email address from spoken or written text.
    Handles spoken speech variants ('at the rate', 'at', 'dot', 'dash', 'underscore'),
    spaced letter spelling (e.g. 'a g a r w a l 123456k@gmail.com', 'm o h i t @ g m a i l . c o m'),
    and strips trailing conversational words.
    """
    if not raw_text:
        return None
    t = raw_text.lower()
    t = re.sub(r"\bat\s+the\s+rate\b|\bat\s+rate\b|\battherate\b|\bat\b", "@", t)
    t = re.sub(r"\bdot\b", ".", t)
    t = re.sub(r"\bdash\b|\bhyphen\b", "-", t)
    t = re.sub(r"\bunderscore\b", "_", t)

    if "@" not in t:
        return None

    parts = t.split("@", 1)
    before_at = parts[0]
    after_at = parts[1]

    # Clean domain chars
    domain_chars = []
    tld_found = False
    for char in after_at:
        if char.isalnum() or char in ".-_":
            domain_chars.append(char)
        elif char.isspace():
            continue
        else:
            break
        curr = "".join(domain_chars)
        if (curr.endswith(".com") or curr.endswith(".org") or curr.endswith(".net") or
            curr.endswith(".edu") or curr.endswith(".gov") or curr.endswith(".in") or
            curr.endswith(".io") or curr.endswith(".ai")):
            tld_found = True
            break

    domain = "".join(domain_chars) if tld_found else ""
    if not domain:
        m_dom = re.search(r"^\s*([a-z0-9.-]+\.[a-z]{2,10})", after_at)
        domain = m_dom.group(1) if m_dom else ""

    # Clean username part (look backwards from @ for boundary / stop-words)
    stop_words = {
        "email", "id", "mel", "mail", "karo", "change", "badlo", "badal", "hai",
        "ki", "ko", "ka", "ke", "likho", "likh", "update", "ji", "to", "is",
        "address", "nahi", "nahin", "nhi", "aur", "and", "mein", "par", "bhejo",
        "send", "kar", "do", "batao", "sunao", "rakho", "dal", "daalo"
    }

    raw_tokens = [tok for tok in re.split(r"[\s,;:?!]+", before_at) if tok]
    user_pieces = []
    for tok in reversed(raw_tokens):
        cleaned = tok.strip(".,;:?!-")
        if cleaned in stop_words:
            break
        user_pieces.insert(0, cleaned)

    if not user_pieces and raw_tokens:
        user_pieces = [raw_tokens[-1].strip(".,;:?!-")]

    user = "".join(user_pieces)
    if not user or not domain:
        return None

    candidate = f"{user}@{domain}".lower()
    if re.match(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", candidate):
        return candidate
    return None


def fast_match_voice_intent(raw_text: str) -> Optional[VoiceIntentResult]:
    """
    Quickly resolves high-confidence control phrases, Hinglish transaction entries,
    email submissions, queries, and corrections without waiting for external AI calls.
    Supports Hindi, Hinglish, and English commands with sub-millisecond execution.
    """
    text = strip_wake_word(raw_text).strip().lower()
    lang = detect_input_language(text)

    # 0A. Direct amount correction fast path (e.g., "500 nahi, 800" or "400 nahi 600", "nahi amount 2000 hai")
    correction_match = re.search(r"(?:(\d+)\s*(?:nahi|nhi|ke badle|ki jagah|not|instead of)\s*)?(\d+(?:\.\d{1,2})?)\s*(?:rupaye|rs|₹|rupees)?(?:\s*karo|\s*likho|\s*hai|\s*make it)?$", text)
    if correction_match and any(w in text for w in ("nahi", "nhi", "badle", "jagah", "not", "instead")):
        new_amt = float(correction_match.group(2))
        return VoiceIntentResult(
            intent=VoiceIntentType.CORRECT_PENDING_ENTRY,
            correction_target="amount",
            amount=new_amt,
            corrected_amount=new_amt,
            entities={"amount": new_amt},
            language=lang
        )

    # 0B. Email Correction fast path (e.g. "Nahin Mel ID change karo", "Nahi, email ID change karo. newemail@gmail.com")
    extracted_email = extract_email_from_text(raw_text)
    is_email_corr = any(w in text for w in (
        "change email", "email change", "mel id change", "email id change", "email badlo", "email id badlo"
    )) or (
        any(w in text for w in ("change", "badlo", "badal", "update")) and any(w in text for w in ("email", "mel", "mail"))
    ) or (
        any(w in text for w in ("nahi", "nahin", "nhi", "not")) and (
            any(w in text for w in ("email", "mel", "mail")) or
            extracted_email is not None
        )
    )

    if is_email_corr:
        return VoiceIntentResult(
            intent=VoiceIntentType.CORRECTION,
            correction_target="email",
            customer_email=extracted_email,
            entities={"email": extracted_email},
            clarification_message="Kripya nayi email ID bataiye." if not extracted_email else None,
            language=lang
        )

    # 0C. Conversational Greetings (e.g. "कैसे हो", "kaise ho", "namaste", "hello", "kya haal hai", "how are you")
    clean_no_punct = re.sub(r"[^\w\s\u0900-\u097F]", "", text).strip().lower()
    greeting_patterns = (
        "kaise ho", "kaisa hai", "kaise hain", "kya haal hai", "kya hal hai", "namaste", "namaskar",
        "hello", "hi", "hey", "good morning", "good evening", "good afternoon", "how are you", "who are you",
        "कैसे हो", "कैसे हैं", "नमस्ते", "नमस्कार", "क्या हाल है", "हेलो", "हाय", "आप कैसे हो", "आप कैसे हैं"
    )
    is_greeting = (
        clean_no_punct in greeting_patterns
        or any(clean_no_punct.startswith(g + " ") for g in ("hello", "namaste", "namaskar", "hi", "hey", "नमस्ते", "नमस्कार", "हेलो", "हाय"))
        or any(phrase in clean_no_punct for phrase in (
            "kaise ho", "kaisa hai", "kaise hain", "kya haal", "kya hal", "how are you", "who are you",
            "कैसे हो", "कैसे हैं", "क्या हाल"
        ))
    )
    if is_greeting:
        msg = (
            "Hello! I am doing well. I am your HisabAI ledger assistant. I can help you check customer balances, record credit or payments, or view your daily summary."
            if lang == "english" else
            "Namaste! Main badhiya hoon. Main aapka HisabAI khata assistant hoon. Aap mujhse customer ka hisab pooch sakte hain, naya udhaar ya jama entry likhwa sakte hain, ya aaj ka summary dekh sakte hain."
        )
        return VoiceIntentResult(
            intent=VoiceIntentType.GREETING,
            clarification_message=msg,
            language=lang
        )

    # 0D. User Complaints & Troubleshooting Assistance
    complaint_keywords = (
        "दिक्कत", "dikkat", "pareshani", "परेशानी", "problem", "kaam nahi kar raha",
        "samajh nahi", "galat samajh", "not working", "facing issue", "kuch samajh nahi",
        "galat likh", "samajh nahi pa raha", "bhai dikkat"
    )
    if any(w in text for w in complaint_keywords):
        msg = (
            "Kshama kijiye! Agar voice assistant ko samajhne mein dikkat ho rahi hai, "
            "toh kripya dheere aur saaf aawaz mein customer ka naam, amount aur udhaar ya jama bolein. "
            "Aap screen par diye gaye buttons aur manual form se bhi hisab likh sakte hain."
        )
        return VoiceIntentResult(
            intent=VoiceIntentType.USER_FEEDBACK,
            clarification_message=msg,
            language=lang
        )

    # 1. Confirmation fast path
    confirm_phrases = (
        "haan", "ha", "haa", "haanji", "haan ji", "yes", "confirm", "theek hai",
        "save karo", "haan save kar do", "save kar do", "thik hai", "theek hai save kar do",
        "theek hai save karo", "haan save karo", "save kijiye", "theek hai ji", "bilkul",
        "yes confirm", "yes save it", "save it", "ok save it", "confirm please", "yes please", "sure",
        "yes save kr do", "yes save kardo", "yes save kar do", "save kr do", "save kardo",
        "haan save kr do", "haan save kardo", "kar do", "kr do", "kardo", "krdo",
        "kar do save", "kardo save", "kr do save", "krdo save", "bina email ke save kar do",
        "bina email ke save karo", "aise hi save kar do", "aise hi save karo", "skip email",
        "हाँ", "हां", "यस", "सेव कर दो", "सेव करो", "कन्फर्म", "हाँ सेव कर दो", "कर दो"
    )
    if (
        text in confirm_phrases
        or text.startswith("haan ")
        or text.startswith("confirm ")
        or text.startswith("yes ")
        or text.startswith("save ")
        or text.startswith("kar do")
        or text.startswith("kr do")
        or text.startswith("kardo")
        or re.match(r"^(?:yes|haan|ha)?\s*save\s*(?:kar|kr)?\s*do\b", text)
    ):
        return VoiceIntentResult(intent=VoiceIntentType.CONFIRM, language=lang)

    # 2. Cancellation fast path
    cancel_phrases = (
        "nahi", "nahin", "no", "cancel", "radd karo", "cancel kar do",
        "mat karo", "mat save karo", "nahi cancel kar do", "nahi mat karo", "nahi cancel karo",
        "no cancel", "don't save", "cancel it", "cancel transaction", "abort"
    )
    if not is_email_corr and (text in cancel_phrases or text.startswith("nahi ") or text.startswith("cancel ") or text.startswith("no ")):
        return VoiceIntentResult(intent=VoiceIntentType.CANCEL, language=lang)

    # 3. Stop assistant fast path
    if text in ("bas", "stop", "stop assistant", "band karo", "assistant band kar do", "chup ho jao", "exit", "quit", "goodbye"):
        return VoiceIntentResult(intent=VoiceIntentType.STOP_ASSISTANT, language=lang)

    # 4. Repeat fast path
    if text in ("dobara sunao", "repeat", "repeat karo", "pichhli entry kya thi", "phir se bolo", "wapas bolo", "say again", "what did you say"):
        return VoiceIntentResult(intent=VoiceIntentType.REPEAT_LAST_RESPONSE, language=lang)

    # 5. Direct amount correction fast path (e.g., "500 nahi, 800" or "not 500, 800")
    correction_match = re.search(r"(?:(\d+)\s*(?:nahi|nhi|ke badle|ki jagah|not|instead of)\s*)?(\d+(?:\.\d{1,2})?)\s*(?:rupaye|rs|₹|rupees)?(?:\s*karo|\s*likho|\s*hai|\s*make it)?$", text)
    if correction_match and any(w in text for w in ("nahi", "nhi", "badle", "jagah", "not", "instead")):
        new_amt = float(correction_match.group(2))
        return VoiceIntentResult(
            intent=VoiceIntentType.CORRECT_PENDING_ENTRY,
            amount=new_amt,
            corrected_amount=new_amt,
            entities={"amount": new_amt},
            language=lang
        )

    # 6. Spoken Email Submission Fast Path (e.g., "sharma@gmail.com", "email sharma@example.com", "sharma at gmail dot com")
    if extracted_email and not any(k in text for k in ("udhaar", "jama", "credit", "payment", "likho", "likh do")):
        return VoiceIntentResult(
            intent=VoiceIntentType.PROVIDE_EMAIL,
            customer_email=extracted_email,
            entities={"email": extracted_email},
            language=lang
        )

    # 7. Update Customer Email Intent (e.g., "Sharma ji ki email ID update karo", "Is customer ki email ID update karo")
    if any(q in text for q in ("email id update", "email update", "email id badlo", "update email")):
        m_cust = re.search(r"([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+ki\s+email", text)
        cust_name = m_cust.group(1).title() if m_cust else None
        return VoiceIntentResult(
            intent=VoiceIntentType.UPDATE_CUSTOMER_EMAIL,
            customer_name=cust_name,
            customer_email=extracted_email,
            language=lang
        )

    # 7B. Navigation Fast Path (e.g., "ledger book kholo", "open inventory", "customer list dikhao", "reports kholo", "open settings")
    # Must precede entry parser and avoid false positives on financial records with amounts
    nav_verbs = ("kholo", "dikhao", "open", "show", "navigate", "go to", "khol do", "kholna", "kholiye", "dikhana")
    has_nav_verb = any(v in text for v in nav_verbs)
    has_amt = bool(re.search(r"\d+", text))
    has_verification = any(w in text for w in ("verification", "verifications", "claims"))
    has_fin_op = any(k in text for k in ("udhaar", "udhar", "credit", "jama", "paid", "likho", "likh do")) or ("payment" in text and not has_verification)

    # Direct single-term view navigation aliases (e.g., user just says "ledger", "inventory", "settings")
    direct_view_map = {
        "ledger": "ledger",
        "ledger book": "ledger",
        "bahi khata": "ledger",
        "inventory": "inventory",
        "stock": "inventory",
        "customers": "customers",
        "customer directory": "customers",
        "reminders": "reminders",
        "reports": "reports",
        "analytics": "reports",
        "settings": "settings",
        "team": "team",
        "verifications": "verifications",
        "dashboard": "dashboard",
    }
    if text in direct_view_map:
        return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view=direct_view_map[text], language=lang)

    if has_nav_verb and not has_amt and not has_fin_op:
        # Check target views
        if any(w in text for w in ("ledger", "bahi khata", "khata book")):
            # Guard against specific customer ledger queries: "Sharma ji ka ledger dikhao"
            if not re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+ledger", text):
                return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="ledger", language=lang)
        if any(w in text for w in ("customer directory", "customer list", "customers", "grahak suchi", "grahak list")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="customers", language=lang)
        if any(w in text for w in ("inventory", "stock", "product catalog", "products", "maal", "saman list")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="inventory", language=lang)
        if any(w in text for w in ("reminder", "reminders", "tagada")):
            # Guard against "Sharma ji ko reminder bhejo"
            if not any(k in text for k in ("bhejo", "send", "kar do", "karo")) or any(k in text for k in ("screen", "page", "tab", "view")):
                return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="reminders", language=lang)
        if any(w in text for w in ("report", "reports", "analytics")):
            # Guard against "aaj ki report" (daily summary)
            if not any(d in text for d in ("aaj", "kal", "today", "yesterday")):
                return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="reports", language=lang)
        if any(w in text for w in ("setting", "settings", "shop settings", "dukan settings")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="settings", language=lang)
        if any(w in text for w in ("team", "staff", "access control", "rbac")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="team", language=lang)
        if any(w in text for w in ("verification", "verifications", "claims", "payment verification")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="verifications", language=lang)
        if any(w in text for w in ("dashboard", "home", "home screen", "main screen")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="dashboard", language=lang)

    # 8. Daily Summary & Collection Fast Path (e.g., "Aaj ka total hisaab batao", "Today's summary")
    if any(q in text for q in (
        "aaj ka total hisaab", "aaj ka total hisab", "aaj ka hisaab", "aaj ka hisab",
        "aaj ka total", "today's total collection", "today total collection", "today summary", "today's summary"
    )):
        tx_type = "JAMA" if any(k in text for k in ("collection", "jama", "received")) else None
        return VoiceIntentResult(
            intent=VoiceIntentType.QUERY_DAILY_SUMMARY,
            date_filter="today",
            transaction_type=tx_type,
            language=lang
        )

    # 8B. Ambiguous Account Action Fast Path (e.g., "Tausif ka khata khatam kar do", "Close Ramesh's account")
    # Must precede balance settlement so ambiguous phrases are not mistaken for explicit settlement requests
    ambiguous_account_phrases = (
        "khata khatam", "खाता खत्म", "khata band", "खाता बंद",
        "khata delete", "hisaab khatam", "hisab khatam", "खाता हटाओ",
        "hisaab barabar", "hisaab chukta", "hisab clear", "close account", "delete account"
    )
    if any(q in text for q in ambiguous_account_phrases):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+(?:khata|hisaab|hisab)", text)
        if not m:
            m = re.search(r"(?:close|delete|clear)\s+([A-Za-z\u0900-\u097F]+)'?s?\s+account", text)
        ambig_name = m.group(1).title() if m else None
        clarification = (
            f"Would you like to settle the balance for {ambig_name or 'this customer'} or close the account?"
            if lang == "english" else
            f"Aap {ambig_name or 'customer'} ka baaki balance settle karna chahte hain ya account band karna chahte hain?"
        )
        return VoiceIntentResult(
            intent=VoiceIntentType.AMBIGUOUS_ACCOUNT_ACTION,
            customer_name=ambig_name,
            clarification_message=clarification,
            language=lang
        )

    # 8C. Balance Settlement Fast Path (e.g., "Tausif ka balance settle karo", "settle balance")
    if any(q in text for q in ("settle karo", "balance settle", "settle kar do", "chukta kar do", "settle")):
        m_set = re.search(r"([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+balance\s+settle", text)
        if not m_set:
            m_set = re.search(r"([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ki|ke)?\s*settle", text)
        set_name = m_set.group(1).title() if m_set else None
        return VoiceIntentResult(
            intent=VoiceIntentType.SETTLE_BALANCE,
            customer_name=set_name,
            language=lang
        )

    # 9. Customer Balance & Khata Query Fast Path (e.g., "Sharma ji ka balance batao", "Rahul ka hisab", "Agarwal ji ka khata kholo")
    khata_open_m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ki|ke|का|की|के)\s+(?:khata|hisaab|hisab|खाता|हिसाब)\s+(?:kholo|dikhao|batao|खोलो|दिखाओ|बताओ)", text)
    if khata_open_m and khata_open_m.group(1).lower() not in ("aaj", "kal", "parso", "today"):
        return VoiceIntentResult(
            intent=VoiceIntentType.QUERY_CUSTOMER_BALANCE,
            customer_name=khata_open_m.group(1).title(),
            language=lang
        )

    if any(q in text for q in ("balance batao", "balance bata", "ka balance", "kitna baaki", "kitna baki")):
        m_bal = re.search(r"([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ki|ke)?\s*(?:balance|kitna baaki|kitna baki)", text)
        if m_bal and m_bal.group(1).lower() not in ("aaj", "kal", "parso", "today"):
            return VoiceIntentResult(
                intent=VoiceIntentType.QUERY_CUSTOMER_BALANCE,
                customer_name=m_bal.group(1).title(),
                language=lang
            )

    # 10. Hinglish & Hindi Entry Fast Path
    # Handles: "₹2000 संस्कार के नाम पर उधर लिख दो", "संस्कार आईएस 2000 क्रेडिट", "क्रेडिट ₹2000 संस्कार",
    #          "Sanskar ke naam par do hazaar rupaye udhaar likh do", "Sanskar ko do hazaar credit kar do",
    #          "Sanskar ne paanch sau rupaye de diye", "Ramesh ne 500 rupaye jama karaye",
    #          "Gupta ji ke naam par 1200 ka udhaar add karo", "Raju ko 300 udhaar diya"
    has_udhaar = any(k in text for k in ("udhaar", "udhar", "credit", "उधार", "उधर", "क्रेडिट", "उधारी", "diya"))
    has_jama = any(k in text for k in ("jama", "payment", "paid", "karaye", "aaye", "aaya", "जमा", "पेमेंट", "कराए", "आए", "आया", "de diye", "दे दिए")) or bool(re.search(r"\bne\b.*?\b(?:diye|diya|de\s*diye)\b", text)) or bool(re.search(r"ने\s+.*?दिए", text))
    is_ko_diya = bool(re.search(r"\bko\b.*?\b(?:diye|diya)\b", text)) or bool(re.search(r"को\s+.*?दिए", text))

    if has_udhaar or has_jama or is_ko_diya:
        if has_jama and not has_udhaar and not is_ko_diya:
            entry_type = "JAMA"
        elif has_udhaar and not has_jama:
            entry_type = "UDHAAR"
        elif is_ko_diya and not has_jama:
            entry_type = "UDHAAR"
        elif any(k in text for k in ("credit", "udhaar", "udhar", "क्रेडिट", "उधार", "उधर")):
            entry_type = "UDHAAR"
        else:
            entry_type = "JAMA"

        # Strip out email portion if present so amount doesn't match digits in email
        text_no_email = re.sub(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "", text)
        amt_match = re.search(r"(\d+(?:\.\d{1,2})?)", text_no_email)
        entry_amt = float(amt_match.group(1)) if amt_match else None

        name = None
        # Pattern 1: Leading keyword: 'क्रेडिट 2000 संस्कार' or 'उधार 500 राहुल'
        m_lead_kw = re.search(r"^(?:क्रेडिट|उधार|उधर|जमा|पेमेंट|credit|payment|udhaar|udhar|jama)\s*(?:₹|rs\.?)?\s*\d+(?:\.\d{1,2})?\s*(?:rupaye|rs|₹|रुपये)?\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)$", text_no_email, re.IGNORECASE)
        if m_lead_kw:
            name = m_lead_kw.group(1).title()

        # Pattern 2: Leading amount: '2000 संस्कार के नाम पर उधर लिख दो'
        if not name:
            m_amt_lead = re.search(r"^\d+(?:\.\d{1,2})?\s*(?:rupaye|rs|₹|रुपये)?\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ke\s+naam\s+par|के\s+नाम\s+पर)", text_no_email, re.IGNORECASE)
            if m_amt_lead:
                name = m_amt_lead.group(1).title()

        # Pattern 3: Leading customer with 'ke naam par': 'Gupta ji ke naam par 1200 ka udhaar add karo'
        if not name:
            m_naam = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ke\s+naam\s+par|के\s+नाम\s+पर)", text_no_email, re.IGNORECASE)
            if m_naam:
                name = m_naam.group(1).title()

        # Pattern 4: Customer with particle or noise before amount: 'संस्कार आईएस 2000 क्रेडिट', 'Sanskar ko 2000 credit kar do'
        if not name:
            m_cust_amt = re.search(r"^([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:आईएस|is|ko|ka|ke|ne|को|का|के|ने)?\s*(?:₹|rs\.?)?\s*(\d+(?:\.\d{1,2})?)", text_no_email, re.IGNORECASE)
            if m_cust_amt and m_cust_amt.group(1).lower() not in ("udhaar", "jama", "credit", "payment", "record", "give", "क्रेडिट", "उधार", "उधर", "जमा", "पेमेंट"):
                name = m_cust_amt.group(1).title()

        # Pattern 5: Leading name with 'ka/ko/ne/ke': 'Sharma ji ka 200 ...', 'Ramesh ne 500 ...'
        if not name:
            m_hi = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ko|ne|ke|को|का|के|ने)?", text_no_email, re.IGNORECASE)
            if m_hi and m_hi.group(1).lower() not in ("udhaar", "jama", "credit", "payment", "record", "give", "add", "ek", "do", "क्रेडिट", "उधार", "उधर", "जमा", "पेमेंट"):
                name = m_hi.group(1).title()

        # Pattern 6: English 'record 500 credit for Rahul' / 'give 500 credit to Rahul'
        if not name or name.lower() in ("record", "give", "add"):
            m_en = re.search(r"(?:for|to|from)\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", text_no_email, re.IGNORECASE)
            if m_en:
                name = m_en.group(1).title()
            else:
                m_lead = re.search(r"^([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:paid|gave|credit|payment)", text_no_email, re.IGNORECASE)
                if m_lead:
                    name = m_lead.group(1).title()

        if name and entry_amt and entry_amt > 0:
            return VoiceIntentResult(
                intent=VoiceIntentType.ENTRY,
                customer_name=name,
                amount=entry_amt,
                transaction_type=entry_type,
                customer_email=extracted_email,
                language=lang
            )

    return None


# ==========================================
# Core AI Parsing Functions
# ==========================================

def parse_hinglish_input(user_text: str) -> ExtractedTransaction:
    """
    Parses a raw Hinglish voice or text phrase into a validated ExtractedTransaction.
    Preserves exact backwards compatibility for existing tests and /api/parse endpoint.
    """
    sanitized_text = user_text.strip() if user_text else ""
    if not sanitized_text or len(sanitized_text) < 2:
        raise AIAmbiguityError("Transaction input phrase cannot be empty or too short.")

    api_key = get_configured_api_key()
    if not api_key or not is_ai_configured():
        raise AIConfigurationError(
            "GEMMA_API_KEY is not configured or contains placeholder text. "
            "Please add a valid Google AI Studio key to your .env file."
        )

    try:
        from google import genai
        from google.genai import types
        from google.genai.errors import APIError, ServerError
    except ImportError as ie:
        logger.error(f"Failed to import google.genai SDK: {ie}")
        raise AIConfigurationError("google-genai SDK is not installed. Run 'pip install -r requirements.txt'.")

    try:
        client = genai.Client(api_key=api_key)
    except Exception as init_err:
        sanitized_err = redact_secrets(str(init_err))
        logger.error(f"Failed to initialize GenAI client: {sanitized_err}")
        raise AIConfigurationError(f"Failed to initialize Google GenAI client: {sanitized_err}")

    # Normalize spoken numbers
    processed_text = normalize_hindi_number_words(sanitized_text)

    full_prompt = (
        f"{SYSTEM_INSTRUCTION}\n\n"
        f"Transaction command: \"{processed_text}\"\n"
        f"Return ONLY the JSON object:"
    )

    models_to_try = get_candidate_models()
    raw_response_text: Optional[str] = None
    last_exception: Optional[Exception] = None

    for candidate in models_to_try:
        try:
            logger.info(f"Invoking AI parsing with model: {candidate}")
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            )

            response = client.models.generate_content(
                model=candidate,
                contents=full_prompt,
                config=config,
            )

            if response and response.text and response.text.strip():
                raw_response_text = response.text
                logger.info(f"Successful extraction from model: {candidate}")
                break
        except (APIError, ServerError) as api_err:
            sanitized_err = redact_secrets(str(api_err))
            logger.warning(f"Model {candidate} failed with API error: {sanitized_err}")
            last_exception = api_err
            continue
        except Exception as exc:
            sanitized_err = redact_secrets(str(exc))
            logger.warning(f"Model {candidate} failed with exception: {sanitized_err}")
            last_exception = exc
            continue

    if not raw_response_text:
        err_msg = redact_secrets(str(last_exception)) if last_exception else "Model returned empty response."
        logger.error(f"AI extraction failure across all candidate models: {err_msg}")
        raise AIProviderError(f"AI service temporarily unavailable: {err_msg}")

    cleaned_json_str = strip_markdown_fences(raw_response_text)

    try:
        data = json.loads(cleaned_json_str)
    except json.JSONDecodeError as jde:
        logger.error(f"JSON decode failed on output '{raw_response_text}': {jde}")
        raise AIAmbiguityError(
            "AI response was not valid JSON. Please rephrase the transaction, e.g. 'Sharma ji ka 200 udhaar likh do'."
        )

    if not isinstance(data, dict):
        raise AIAmbiguityError("AI response was not a structured dictionary.")

    if "error" in data:
        err_detail = data["error"]
        logger.info(f"AI returned ambiguity for input '{sanitized_text}': {err_detail}")
        raise AIAmbiguityError(f"Could not parse transaction: {err_detail}. Please specify customer name and amount.")

    try:
        validated = ExtractedTransaction.model_validate(data)
        return validated
    except ValidationError as ve:
        logger.error(f"Pydantic schema validation error: {ve}")
        raise AIAmbiguityError(
            "Extracted transaction was missing required fields. Please specify who and how much."
        )


def parse_hinglish_voice(transcript: str, context: Optional[dict] = None) -> dict:
    """
    Sends the Hinglish transcript to Gemma 4 for structured JSON extraction.
    Adheres strictly to the production blueprint specification.
    """
    system_instruction = """
    You are 'HisabAI', an expert financial ledger assistant for Indian shopkeepers.
    Analyze the following Hinglish sentence. Extract the customer name, numerical amount, 
    and transaction type ('UDHAAR' for credit given, 'JAMA' for payment received).
    
    CRITICAL INSTRUCTION: You must respond ONLY with a raw, valid JSON object. 
    Do not use markdown formatting (no ```json).
    
    Expected JSON format:
    {
      "intent": "NEW_TRANSACTION" | "CORRECTION" | "CONFIRMATION" | "CANCELLATION" | "UNKNOWN",
      "customer_name": "string or null",
      "amount": number or null,
      "transaction_type": "UDHAAR" | "JAMA" | null,
      "description": "string"
    }
    """
    
    prompt = f"Transcript: '{transcript}'\nActive Context: {json.dumps(context) if context else 'None'}"

    # 1. Deterministic fast match check for immediate confirmations, cancellations, and transactions
    try:
        fast_intent = fast_match_voice_intent(transcript)
        if fast_intent:
            intent_name = fast_intent.intent.value if hasattr(fast_intent.intent, 'value') else str(fast_intent.intent)
            if intent_name in ("CONFIRMATION", "CONFIRM"):
                return {"intent": "CONFIRMATION", "customer_name": None, "amount": None, "transaction_type": None, "description": ""}
            elif intent_name in ("CANCELLATION", "CANCEL"):
                return {"intent": "CANCELLATION", "customer_name": None, "amount": None, "transaction_type": None, "description": ""}
            elif intent_name in ("NEW_TRANSACTION", "ENTRY") and fast_intent.customer_name and fast_intent.amount:
                tx_type = fast_intent.transaction_type.value if hasattr(fast_intent.transaction_type, 'value') else str(fast_intent.transaction_type or "UDHAAR")
                return {
                    "intent": "NEW_TRANSACTION",
                    "customer_name": fast_intent.customer_name,
                    "amount": float(fast_intent.amount),
                    "transaction_type": tx_type,
                    "description": fast_intent.notes or ""
                }
    except Exception:
        pass

    # 2. Gemma 4 LLM Generation via google-genai
    try:
        from google import genai
        from google.genai import types

        api_key = get_configured_api_key() or os.getenv("GEMMA_API_KEY")
        if not api_key:
            fb = fallback_heuristic_intent(transcript)
            intent_val = fb.intent.value if hasattr(fb.intent, 'value') else str(fb.intent)
            tx_type = fb.transaction_type.value if fb.transaction_type and hasattr(fb.transaction_type, 'value') else (str(fb.transaction_type) if fb.transaction_type else None)
            return {
                "intent": "NEW_TRANSACTION" if intent_val in ("NEW_TRANSACTION", "ENTRY") else intent_val,
                "customer_name": fb.customer_name,
                "amount": float(fb.amount) if fb.amount else None,
                "transaction_type": tx_type,
                "description": fb.notes or ""
            }

        client = genai.Client(api_key=api_key)
        active_model = get_active_model_name() or "gemma-4-26b-a4b-it"

        response = client.models.generate_content(
            model=active_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.1,  # Keep temperature extremely low for deterministic JSON
            ),
        )

        raw_text = response.text.strip()
        cleaned_json = raw_text.replace("```json", "").replace("```", "").strip()
        parsed = json.loads(cleaned_json)
        parsed.setdefault("intent", "UNKNOWN")
        parsed.setdefault("customer_name", None)
        parsed.setdefault("amount", None)
        parsed.setdefault("transaction_type", None)
        parsed.setdefault("description", "")
        return parsed

    except Exception as e:
        logger.error(f"[AI SERVICE ERROR] Gemma parsing failed: {e}")
        try:
            fb = fallback_heuristic_intent(transcript)
            intent_val = fb.intent.value if hasattr(fb.intent, 'value') else str(fb.intent)
            tx_type = fb.transaction_type.value if fb.transaction_type and hasattr(fb.transaction_type, 'value') else (str(fb.transaction_type) if fb.transaction_type else None)
            return {
                "intent": "NEW_TRANSACTION" if intent_val in ("NEW_TRANSACTION", "ENTRY") else intent_val,
                "customer_name": fb.customer_name,
                "amount": float(fb.amount) if fb.amount else None,
                "transaction_type": tx_type,
                "description": fb.notes or ""
            }
        except Exception:
            return {"intent": "UNKNOWN", "error": str(e)}


def classify_voice_command(user_text: str, context: Optional[dict] = None) -> VoiceIntentResult:
    """
    Classifies a voice command into an actionable Khata assistant intent.
    First evaluates deterministic fast paths, then invokes Google GenAI if required.
    """
    cleaned_input = strip_wake_word(user_text).strip()
    if not cleaned_input or len(cleaned_input) < 2:
        return VoiceIntentResult(intent=VoiceIntentType.UNKNOWN, clarification_message="Aapne kya kaha, main samajh nahi paaya. Kripya dobara bolein.")

    # 1. Normalize numbers and currency symbols first (e.g. 'do hazaar' -> '2000', '₹2000' -> '2000')
    normalized_input = normalize_hindi_number_words(cleaned_input)

    # 2. Fast path match on normalized input
    fast_match = fast_match_voice_intent(normalized_input)
    if fast_match:
        return fast_match

    # 2. Check API key
    api_key = get_configured_api_key()
    if not api_key or not is_ai_configured():
        # Fallback heuristic if API key is unconfigured
        return fallback_heuristic_intent(cleaned_input)

    try:
        from google import genai
        from google.genai import types
        from google.genai.errors import APIError, ServerError
    except ImportError:
        return fallback_heuristic_intent(cleaned_input)

    try:
        client = genai.Client(api_key=api_key)
    except Exception as exc:
        logger.error(f"GenAI client init error: {redact_secrets(str(exc))}")
        return fallback_heuristic_intent(cleaned_input)

    # Normalize Hindi spoken numbers
    processed_text = normalize_hindi_number_words(cleaned_input)

    context_prompt = ""
    if context:
        state = context.get("state", "AWAITING_CONFIRMATION" if (context.get("customer") or context.get("pending_customer")) else "IDLE")
        cust = context.get("customer") or context.get("pending_customer")
        amt = context.get("amount") or context.get("pending_amount")
        em = context.get("email") or context.get("customer_email") or context.get("pending_email")
        t_type = context.get("type") or context.get("transaction_type")
        context_prompt = (
            f"Active Context:\n"
            f"- State: {state}\n"
            f"- Pending Transaction: customer='{cust}', amount={amt}, email='{em}', type='{t_type}'\n"
            f"If the user is correcting the pending transaction (e.g. changing email, amount, or customer), "
            f"set intent to 'CORRECTION', specify 'correction_target' ('email'|'amount'|'customer_name'|'type'), "
            f"and return the updated entity in 'entities'.\n"
        )

    full_prompt = (
        f"{VOICE_INTENT_SYSTEM_PROMPT}\n\n"
        f"{context_prompt}"
        f"Command: \"{processed_text}\"\n"
        f"Return ONLY the JSON object:"
    )

    models_to_try = get_candidate_models()
    raw_response_text: Optional[str] = None
    last_exception: Optional[Exception] = None

    for candidate in models_to_try:
        try:
            logger.info(f"Classifying voice intent using model: {candidate}")
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            )

            response = client.models.generate_content(
                model=candidate,
                contents=full_prompt,
                config=config,
            )

            if response and response.text and response.text.strip():
                raw_response_text = response.text
                break
        except Exception as exc:
            sanitized_err = redact_secrets(str(exc))
            logger.warning(f"Voice intent model {candidate} failed: {sanitized_err}")
            last_exception = exc
            continue

    if not raw_response_text:
        logger.warning(f"All GenAI models failed for voice intent, using heuristic fallback: {last_exception}")
        return fallback_heuristic_intent(processed_text)

    cleaned_json_str = strip_markdown_fences(raw_response_text)

    try:
        data = json.loads(cleaned_json_str)

        if "entities" in data and isinstance(data["entities"], dict):
            ents = data["entities"]
            if not data.get("customer_name") and ents.get("customer_name"):
                data["customer_name"] = ents["customer_name"]
            if data.get("amount") is None and ents.get("amount") is not None:
                data["amount"] = ents["amount"]
            if not data.get("customer_email") and ents.get("email"):
                data["customer_email"] = ents["email"]
            if not data.get("transaction_type") and ents.get("type"):
                data["transaction_type"] = "UDHAAR" if ents.get("type") in ("CREDIT", "UDHAAR") else "JAMA"

        intent_str = str(data.get("intent", "")).upper()
        if intent_str in ("NEW_TRANSACTION", "ENTRY"):
            data["intent"] = VoiceIntentType.NEW_TRANSACTION.value
        elif intent_str in ("CORRECTION", "CORRECT_PENDING_ENTRY"):
            data["intent"] = VoiceIntentType.CORRECTION.value
        elif intent_str in ("CONFIRMATION", "CONFIRM"):
            data["intent"] = VoiceIntentType.CONFIRMATION.value
        elif intent_str in ("CANCELLATION", "CANCEL"):
            data["intent"] = VoiceIntentType.CANCELLATION.value
        elif intent_str == "GREETING":
            data["intent"] = VoiceIntentType.GREETING.value

        # Fix any string numbers
        if "amount" in data and isinstance(data["amount"], str):
            try:
                data["amount"] = float(re.sub(r"[^\d.]", "", data["amount"]))
            except ValueError:
                data["amount"] = None
        if "corrected_amount" in data and isinstance(data["corrected_amount"], str):
            try:
                data["corrected_amount"] = float(re.sub(r"[^\d.]", "", data["corrected_amount"]))
            except ValueError:
                data["corrected_amount"] = None

        if "language" not in data or not data["language"]:
            data["language"] = detect_input_language(cleaned_input)

        parsed_result = VoiceIntentResult.model_validate(data)
        # If AI model returned UNKNOWN, check if deterministic heuristic can resolve it
        if parsed_result.intent == VoiceIntentType.UNKNOWN:
            heuristic_res = fallback_heuristic_intent(processed_text)
            if heuristic_res.intent != VoiceIntentType.UNKNOWN:
                return heuristic_res

        return parsed_result
    except Exception as parse_err:
        logger.error(f"Voice intent json parsing error: {parse_err}. Raw output was: {raw_response_text}")
        return fallback_heuristic_intent(processed_text)


def fallback_heuristic_intent(text: str) -> VoiceIntentResult:
    """
    Deterministic regex-based fallback intent parser when AI provider is unavailable.
    Provides natural language matching across Hindi, Hinglish, and English with contextual clarifications.
    """
    lower = text.lower()
    lang = detect_input_language(text)
    is_en = (lang == "english")

    # 00. Correction Intent (e.g. "Nahi, email ID change karo", "Sharma ka amount 2000 karo", "500 nahi 800")
    extracted_email = extract_email_from_text(text)
    is_corr = any(w in lower for w in (
        "change", "badlo", "badal", "mel id", "email id", "instead", "ke badle", "ki jagah"
    )) or (
        any(w in lower for w in ("nahi", "nahin", "nhi", "not")) and (
            any(w in lower for w in ("email", "mel", "mail", "amount", "rupaye", "badlo", "change")) or
            extracted_email is not None or
            bool(re.search(r"\d+", lower))
        )
    )
    if is_corr:
        if extracted_email or any(w in lower for w in ("email", "mel", "mail")):
            return VoiceIntentResult(
                intent=VoiceIntentType.CORRECTION,
                correction_target="email",
                customer_email=extracted_email,
                entities={"email": extracted_email},
                clarification_message="Kripya nayi email ID bataiye." if not extracted_email else None,
                language=lang
            )
        m_amt = re.search(r"(?:amount\s*(?:badlo|change|karo|hai)?\s*)?(\d+(?:\.\d{1,2})?)\s*(?:rupaye|rs|₹|rupees)?", lower)
        if m_amt:
            amt = float(m_amt.group(1))
            return VoiceIntentResult(
                intent=VoiceIntentType.CORRECTION,
                correction_target="amount",
                amount=amt,
                corrected_amount=amt,
                entities={"amount": amt},
                language=lang
            )

    # 00B. Conversational Greeting (e.g. "कैसे हो", "kaise ho", "namaste", "hello")
    clean_no_punct = re.sub(r"[^\w\s\u0900-\u097F]", "", lower).strip().lower()
    greeting_patterns = (
        "kaise ho", "kaisa hai", "kaise hain", "kya haal hai", "kya hal hai", "namaste", "namaskar",
        "hello", "hi", "hey", "good morning", "good evening", "good afternoon", "how are you", "who are you",
        "कैसे हो", "कैसे हैं", "नमस्ते", "नमस्कार", "क्या हाल है", "हेलो", "हाय", "आप कैसे हो", "आप कैसे हैं"
    )
    is_greeting = (
        clean_no_punct in greeting_patterns
        or any(clean_no_punct.startswith(g + " ") for g in ("hello", "namaste", "namaskar", "hi", "hey", "नमस्ते", "नमस्कार", "हेलो", "हाय"))
        or any(phrase in clean_no_punct for phrase in (
            "kaise ho", "kaisa hai", "kaise hain", "kya haal", "kya hal", "how are you", "who are you",
            "कैसे हो", "कैसे हैं", "क्या हाल"
        ))
    )
    if is_greeting:
        msg = (
            "Hello! I am doing well. I am your HisabAI ledger assistant. I can help you check customer balances, record credit or payments, or view your daily summary."
            if is_en else
            "Namaste! Main badhiya hoon. Main aapka HisabAI khata assistant hoon. Aap mujhse customer ka hisab pooch sakte hain, naya udhaar ya jama entry likhwa sakte hain, ya aaj ka summary dekh sakte hain."
        )
        return VoiceIntentResult(
            intent=VoiceIntentType.GREETING,
            clarification_message=msg,
            language=lang
        )

    # 00C. User Complaints & Troubleshooting Assistance
    complaint_keywords = (
        "दिक्कत", "dikkat", "pareshani", "परेशानी", "problem", "kaam nahi kar raha",
        "samajh nahi", "galat samajh", "not working", "facing issue", "kuch samajh nahi",
        "galat likh", "samajh nahi pa raha", "bhai dikkat"
    )
    if any(w in lower for w in complaint_keywords):
        msg = (
            "Kshama kijiye! Agar voice assistant ko samajhne mein dikkat ho rahi hai, "
            "toh kripya dheere aur saaf aawaz mein customer ka naam, amount aur udhaar ya jama bolein. "
            "Aap screen par diye gaye buttons aur manual form se bhi hisab likh sakte hain."
        )
        return VoiceIntentResult(
            intent=VoiceIntentType.USER_FEEDBACK,
            clarification_message=msg,
            language=lang
        )

    # 0. Navigation commands (e.g., "ledger book kholo", "open inventory", "customer list dikhao")
    if any(q in lower for q in ("kholo", "dikhao", "open", "show", "navigate", "go to")):
        if any(w in lower for w in ("ledger", "bahi khata", "khata book")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="ledger", language=lang)
        if any(w in lower for w in ("customer", "customers", "grahak")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="customers", language=lang)
        if any(w in lower for w in ("inventory", "stock", "maal", "products", "saman")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="inventory", language=lang)
        if any(w in lower for w in ("reminder", "reminders", "tagada")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="reminders", language=lang)
        if any(w in lower for w in ("report", "reports", "analytics")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="reports", language=lang)
        if any(w in lower for w in ("setting", "settings", "shop settings")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="settings", language=lang)
        if any(w in lower for w in ("team", "staff", "access", "users")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="team", language=lang)
        if any(w in lower for w in ("verification", "verifications", "claims")):
            return VoiceIntentResult(intent=VoiceIntentType.NAVIGATE_VIEW, target_view="verifications", language=lang)

    # 0B. Reminder Preparation (e.g. "Sharma ji ko reminder bhejo", "Send payment reminder to Ramesh")
    if any(q in lower for q in ("reminder bhejo", "reminder send", "tagada bhejo", "send reminder", "remind")):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ko\s+reminder", lower)
        if not m:
            m = re.search(r"(?:reminder\s+(?:to|for)|remind)\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
        name = m.group(1).title() if m else None
        return VoiceIntentResult(intent=VoiceIntentType.PREPARE_REMINDER, customer_name=name, language=lang)

    # 0C. Receipt Preparation (e.g. "Receipt banao", "Pichli transaction ki receipt banao", "Download receipt")
    if any(q in lower for q in ("receipt banao", "raseed banao", "make receipt", "generate receipt", "receipt")):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ki\s+receipt", lower)
        if not m:
            m = re.search(r"(?:receipt\s+(?:for|of))\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
        name = m.group(1).title() if m else None
        return VoiceIntentResult(intent=VoiceIntentType.PREPARE_RECEIPT, customer_name=name, language=lang)

    # 1. Customer list / Who owes money
    if any(q in lower for q in ("kis-kis", "kis kis", "sabse zyada udhaar", "udhaar kis customer", "who owes", "owes money", "which customers owe", "list debtors", "pending balance customers")):
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_CUSTOMER_LIST, language=lang)

    # 2. Ambiguous account management actions (e.g. "Tausif ka khata khatam kar do" or "Close John's account")
    if any(q in lower for q in (
        "khata khatam", "खाता खत्म", "khata band", "खाता बंद",
        "khata delete", "hisaab khatam", "hisab khatam", "खाता हटाओ",
        "hisaab barabar", "hisaab chukta", "hisab clear", "close account", "delete account"
    )):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+(?:khata|hisaab|hisab)", lower)
        if not m:
            m = re.search(r"(?:close|delete|clear)\s+([A-Za-z\u0900-\u097F]+)'?s?\s+account", lower)
        name = m.group(1).title() if m else None
        clarification = (
            f"Would you like to settle the balance for {name or 'this customer'} or close the account?"
            if is_en else
            f"Aap {name or 'customer'} ka baaki balance settle karna chahte hain ya account band karna chahte hain?"
        )
        return VoiceIntentResult(
            intent=VoiceIntentType.AMBIGUOUS_ACCOUNT_ACTION,
            customer_name=name,
            clarification_message=clarification,
            language=lang
        )

    # 3. Explicit settlement confirmation intent
    if any(q in lower for q in ("balance settle", "settle karo", "hisaab settle", "settle kar do", "chukta kar do", "settle balance", "settle account")):
        m = re.search(r"(?:settle|balance|chukta)?\s*([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s*(?:ka|ke|balance|settle)?", lower)
        name = m.group(1).title() if m and m.group(1).lower() not in ("balance", "settle", "karo", "account") else None
        return VoiceIntentResult(intent=VoiceIntentType.SETTLE_BALANCE, customer_name=name, language=lang)

    # 4. Customer history / ledger detail queries
    if any(q in lower for q in (
        "purana hisaab", "purana hisab", "pichhla hisab", "history", "transactions", "last transaction",
        "aakhiri transaction", "show history", "transaction history", "account history"
    )):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+k[ai]\s+(?:purana|pichhla|history|transaction|last|aakhiri)", lower)
        if not m:
            m = re.search(r"(?:for|of|check)\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
        if not m:
            m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)'?s?\s+history", lower)
        name = m.group(1).title() if m else None
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_CUSTOMER_HISTORY, customer_name=name, language=lang)

    # 5. Daily summary & collection (both English & Hindi)
    if any(q in lower for q in (
        "today's total collection", "today total collection", "today's collection", "today collection",
        "total collection", "how much collection today", "today's summary", "today summary",
        "today's total", "how much collected today", "today's sales",
        "aaj kitna", "aaj ka total", "aaj ka hisab", "aaj ka hisaab", "aaj kitni transactions", "aaj ka summary"
    )):
        tx_type = "JAMA" if any(k in lower for k in ("collection", "jama", "received", "payment")) else ("UDHAAR" if any(k in lower for k in ("credit", "udhaar")) else None)
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DAILY_SUMMARY, date_filter="today", transaction_type=tx_type, language=lang)

    # 6. Historical summary (both English & Hindi)
    if any(w in lower for w in ("yesterday", "kal")) and any(w in lower for w in ("hisab", "hisaab", "summary", "total", "collection", "report", "kitna")):
        tx_type = "JAMA" if any(k in lower for k in ("collection", "jama", "received")) else None
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DATE_RANGE_SUMMARY, date_filter="yesterday", transaction_type=tx_type, language=lang)
    if any(q in lower for q in ("pichhle saat din", "7 din", "last 7 days", "past 7 days", "past week")):
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DATE_RANGE_SUMMARY, date_filter="last_7_days", language=lang)
    if any(q in lower for q in ("is mahine", "this month", "current month")):
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DATE_RANGE_SUMMARY, date_filter="this_month", language=lang)

    # 7. Query customer balance & Khata Opening (e.g. "Agarwal ji ka khata kholo", "Sharma ji ka balance batao")
    khata_open_m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ki|ke|का|की|के)\s+(?:khata|hisaab|hisab|खाता|हिसाब)\s+(?:kholo|dikhao|batao|खोलो|दिखाओ|बताओ)", lower)
    if khata_open_m and khata_open_m.group(1).lower() not in ("aaj", "kal", "parso", "today"):
        return VoiceIntentResult(
            intent=VoiceIntentType.QUERY_CUSTOMER_BALANCE,
            customer_name=khata_open_m.group(1).title(),
            language=lang
        )

    if any(q in lower for q in (
        "kitna baaki", "kitna baki", "khata check", "hisab sunao", "paise lene", "hisab bata",
        "ka hisab", "baki hai", "baaki hai", "how much does", "what is", "balance", "owes"
    )):
        # Hindi: "Sharma ji ka kitna baaki hai", "Rahul ka hisab"
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+(?:kitna|khata|hisab|hisaab|poora|baki|baaki)", lower)
        # English: "how much does Sharma owe", "what is Sharma's balance", "check balance for Sharma"
        if not m:
            m = re.search(r"(?:how much does|what is|balance of|balance for|check)\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
        if not m:
            m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)'?s?\s+balance", lower)
        name = m.group(1).title() if m else None
        if name and name.lower() not in ("aaj", "kal", "parso", "mahine", "today", "yesterday", "the", "what", "how"):
            return VoiceIntentResult(intent=VoiceIntentType.QUERY_CUSTOMER_BALANCE, customer_name=name, language=lang)

    # 8. Entry (Full & Contextual)
    has_udhaar = any(k in lower for k in ("udhaar", "udhar", "credit", "उधार", "उधर", "क्रेडिट", "उधारी", "diya"))
    has_jama = any(k in lower for k in ("jama", "payment", "paid", "karaye", "aaye", "aaya", "जमा", "पेमेंट", "कराए", "आए", "आया", "de diye", "दे दिए")) or bool(re.search(r"\bne\b.*?\b(?:diye|diya|de\s*diye)\b", lower)) or bool(re.search(r"ने\s+.*?दिए", lower))
    is_ko_diya = bool(re.search(r"\bko\b.*?\b(?:diye|diya)\b", lower)) or bool(re.search(r"को\s+.*?दिए", lower))

    if has_udhaar or has_jama or is_ko_diya:
        if has_jama and not has_udhaar and not is_ko_diya:
            tx_type = "JAMA"
        elif has_udhaar and not has_jama:
            tx_type = "UDHAAR"
        elif is_ko_diya and not has_jama:
            tx_type = "UDHAAR"
        elif any(k in lower for k in ("credit", "udhaar", "udhar", "क्रेडिट", "उधार", "उधर")):
            tx_type = "UDHAAR"
        else:
            tx_type = "JAMA"

        amt_match = re.search(r"(\d+(?:\.\d{1,2})?)", lower)
        amt = float(amt_match.group(1)) if amt_match else None

        # Try to find customer name
        name = None
        # Pattern 1: Leading keyword: 'क्रेडिट 2000 संस्कार'
        m_lead_kw = re.search(r"^(?:क्रेडिट|उधार|उधर|जमा|पेमेंट|credit|payment|udhaar|udhar|jama)\s*(?:₹|rs\.?)?\s*\d+(?:\.\d{1,2})?\s*(?:rupaye|rs|₹|रुपये)?\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)$", text, re.IGNORECASE)
        if m_lead_kw:
            name = m_lead_kw.group(1).title()

        # Pattern 2: Leading amount: '2000 संस्कार के नाम पर उधर लिख दो'
        if not name:
            m_amt_lead = re.search(r"^\d+(?:\.\d{1,2})?\s*(?:rupaye|rs|₹|रुपये)?\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ke\s+naam\s+par|के\s+नाम\s+पर)", text, re.IGNORECASE)
            if m_amt_lead:
                name = m_amt_lead.group(1).title()

        # Pattern 3: Leading customer with 'ke naam par'
        if not name:
            m_naam = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ke\s+naam\s+par|के\s+नाम\s+पर)", text, re.IGNORECASE)
            if m_naam:
                name = m_naam.group(1).title()

        # Pattern 4: Customer with particle or noise before amount: 'संस्कार आईएस 2000 क्रेडिट'
        if not name:
            m_cust_amt = re.search(r"^([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:आईएस|is|ko|ka|ke|ne|को|का|के|ने)?\s*(?:₹|rs\.?)?\s*(\d+(?:\.\d{1,2})?)", text, re.IGNORECASE)
            if m_cust_amt and m_cust_amt.group(1).lower() not in ("udhaar", "jama", "credit", "payment", "record", "give", "क्रेडिट", "उधार", "उधर", "जमा", "पेमेंट"):
                name = m_cust_amt.group(1).title()

        # Pattern 5: Leading name with 'ka/ko/ne/ke': "Sharma ji ka 200 udhaar likh do" or "Raju ne 500 jama kar diye"
        if not name:
            m_hi = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ko|ne|ke|को|का|के|ने)?", lower)
            if m_hi and m_hi.group(1).lower() not in ("udhaar", "jama", "credit", "payment", "record", "give", "please", "ek", "do", "क्रेडिट", "उधार", "उधर", "जमा", "पेमेंट"):
                name = m_hi.group(1).title()

        # Pattern 6: English: "record 500 credit for Ramesh" or "Ramesh paid 500" or "give 500 credit to Rahul"
        if not name or name.lower() in ("record", "give", "add"):
            m_en = re.search(r"(?:for|to|from)\s+([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
            if m_en:
                name = m_en.group(1).title()
            else:
                m_lead = re.search(r"^([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+(?:paid|gave|credit|payment)", lower)
                if m_lead:
                    name = m_lead.group(1).title()

        # Contextual check 8A: Complete Entry
        if name and amt:
            return VoiceIntentResult(intent=VoiceIntentType.ENTRY, customer_name=name, amount=amt, transaction_type=tx_type, language=lang)

        # Contextual check 8B: Missing Customer Name (e.g. "500 udhaar likh do", "record 500 credit")
        if amt and not name:
            msg = "Which customer should I record this for?" if is_en else "Kaunse customer ke naam par likhna hai?"
            return VoiceIntentResult(intent=VoiceIntentType.UNKNOWN, clarification_message=msg, language=lang)

        # Contextual check 8C: Missing Amount (e.g. "Sharma ji ka udhaar likh do", "record credit for Sharma")
        if name and not amt:
            msg = "How much amount should I record?" if is_en else "Ek baar amount dobara bata dena. Kitne rupaye likhne hain?"
            return VoiceIntentResult(intent=VoiceIntentType.UNKNOWN, clarification_message=msg, customer_name=name, language=lang)

    # 9. Contextual check 9: Customer name and amount present, but direction ambiguous (neither udhaar nor jama)
    amt_match = re.search(r"(\d+(?:\.\d{1,2})?)", lower)
    if amt_match:
        m_name = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ke|ko|ne)?", lower)
        if m_name and m_name.group(1).lower() not in ("aaj", "kal", "parso", "today", "yesterday", "record", "give", "the"):
            name = m_name.group(1).title()
            msg = (
                "Do you want to record credit (udhaar) or payment received (jama)?"
                if is_en else
                "Aap udhaar likhwana chahte hain ya jama entry karwani hai?"
            )
            return VoiceIntentResult(
                intent=VoiceIntentType.UNKNOWN,
                customer_name=name,
                amount=float(amt_match.group(1)),
                clarification_message=msg,
                language=lang
            )

    # 10. Default / Unclear
    default_msg = (
        "I didn't quite catch that. You can ask for customer balances, daily collection, or record credit and payments."
        if is_en else
        "Aapne kya kaha, main theek se samajh nahi paaya. Kripya dobara bolein."
    )
    return VoiceIntentResult(intent=VoiceIntentType.UNKNOWN, clarification_message=default_msg, language=lang)


def generate_daily_business_summary_explanation(metrics: dict, language: str = "hinglish") -> str:
    """
    Generates an actionable, clear daily business summary insight.
    Uses Google GenAI if configured, or a deterministic fallback template.
    """
    date_str = metrics.get("date", "Aaj")
    total_udhaar = float(metrics.get("total_udhaar", 0))
    total_jama = float(metrics.get("total_jama", 0))
    net_movement = float(metrics.get("net_movement", 0))
    tx_count = int(metrics.get("transaction_count", 0))
    active_customers = int(metrics.get("active_customers_count", 0))
    top_customer = metrics.get("top_customer_today")

    # If AI key is configured, attempt GenAI generation
    if is_ai_configured():
        api_key = get_configured_api_key()
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=api_key)
            prompt = (
                f"You are HisabAI, a helpful business advisor for an Indian Kirana shopkeeper. "
                f"Give a concise, encouraging 2-sentence summary of today's business in Hinglish.\n"
                f"Metrics: Date: {date_str}, Credit Given (Udhaar): Rs. {total_udhaar:.2f}, "
                f"Payments Received (Jama): Rs. {total_jama:.2f}, Net Cash Movement: Rs. {net_movement:.2f}, "
                f"Transactions: {tx_count}, Active Customers: {active_customers}, Top Customer: {top_customer or 'None'}.\n"
                f"Keep it concise, clear, and professional. Return plain text only."
            )
            response = client.models.generate_content(
                model=get_active_model_name(),
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=150)
            )
            if response and response.text:
                return response.text.strip()
        except Exception as exc:
            logger.warning(f"GenAI daily summary generation failed: {exc}, using fallback template.")

    # High quality deterministic template fallback
    if language == "english":
        sentiment = "Healthy cash collection day!" if total_jama >= total_udhaar else "Higher credit extended today. Consider following up on recoveries."
        top_txt = f" Top account today: {top_customer}." if top_customer else ""
        return (
            f"On {date_str}, you recorded {tx_count} transactions across {active_customers} customers. "
            f"Total received: ₹{total_jama:,.2f}, Total credit: ₹{total_udhaar:,.2f}. {sentiment}{top_txt}"
        )
    else:
        # Hinglish
        flow_txt = "Cash flow majboot hai!" if total_jama >= total_udhaar else "Aaj udhaar zyada gaya hai, tagada plan karein."
        top_txt = f" Aaj sabse zyada lenden {top_customer} ke saath hua." if top_customer else ""
        return (
            f"{date_str} ko kul {tx_count} transactions hui ({active_customers} grahak). "
            f"Jama prapt hua ₹{total_jama:,.2f} aur udhaar diya ₹{total_udhaar:,.2f}. {flow_txt}{top_txt}"
        )


def transcribe_audio_bytes(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """
    Ultra-fast and accurate speech transcription using Groq Whisper (or Gemini Flash fallback).
    Eliminates all browser Web Speech API aborted / no-speech issues.
    """
    if not audio_bytes:
        return ""

    # 1. Primary: Groq Whisper (150ms ultra-low latency)
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            transcription = client.audio.transcriptions.create(
                file=(filename, audio_bytes),
                model="whisper-large-v3-turbo",
                prompt="Hinglish Indian Kirana store ledger: udhaar, jama, khata, rupaye, save kar do, haan save kr do, confirm",
                language="hi",
                response_format="text"
            )
            text = str(transcription).strip()
            if text:
                logger.info(f"Groq Whisper transcribed: '{text}'")
                return text
        except Exception as e:
            logger.warning(f"Groq Whisper transcription failed: {e}, falling back to Gemini.")

    # 2. Secondary: Google Gemini Flash Multimodal Audio (Zero extra keys needed)
    gemma_key = os.environ.get("GEMMA_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if gemma_key:
        try:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=gemma_key)
            mime_type = "audio/webm" if filename.endswith(".webm") else "audio/wav"
            prompt = (
                "Transcribe this spoken Hindi/Hinglish Indian retail store audio accurately. "
                "Output ONLY the transcribed words, without explanations or punctuation quotes."
            )
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                    prompt
                ]
            )
            if response and response.text:
                text = response.text.strip()
                logger.info(f"Gemini Audio transcribed: '{text}'")
                return text
        except Exception as e:
            logger.warning(f"Gemini Audio transcription fallback failed: {e}")

    return ""

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
    "- If the user speaks in English (e.g. 'Show me today's total collection', 'Record 500 credit for Ramesh'), set language to 'english'.\n"
    "- If the user speaks in Hindi script (Devanagari), set language to 'hindi'.\n"
    "- If the user speaks in Hinglish (e.g. 'Sharma ji ka 200 udhaar likh do'), set language to 'hinglish'.\n\n"
    "Available Intents:\n"
    "- ENTRY: Recording credit given (UDHAAR) or payment received (JAMA). E.g. 'Rahul ko 500 udhaar diya', 'Ramesh ne 250 jama kiye', 'Give 500 credit to Rahul', 'Record 200 payment from Anita'.\n"
    "- QUERY_CUSTOMER_BALANCE: Checking balance or khata of a customer. E.g. 'Sharma ji ka kitna udhaar baaki hai?', 'Rahul ka hisab check karo', 'What is Rahul\'s balance?'.\n"
    "- QUERY_DAILY_SUMMARY: Asking for today's summary or collection. E.g. 'Show me today\'s total collection', 'Aaj kitna jama hua?', 'Aaj kitna udhaar diya?'.\n"
    "- QUERY_DATE_RANGE_SUMMARY: Historical report. E.g. 'Kal ki report dikhao', 'Pichhle saat din ka hisab', 'Yesterday\'s summary', 'This month\'s ledger'.\n"
    "- QUERY_CUSTOMER_LIST: Customers who owe money. E.g. 'Kis-kis customer ke paise baaki hain?', 'Who owes money?', 'Which customers have pending balance?'.\n"
    "- QUERY_CUSTOMER_HISTORY: Transaction history. E.g. 'Raju ka purana hisaab dikhao', 'Show transaction history for Ramesh'.\n"
    "- CONFIRM: Explicit confirmation. E.g. 'Haan, save kar do', 'Confirm karo', 'Theek hai', 'Yes, confirm'.\n"
    "- CANCEL: Explicit cancellation. E.g. 'Nahi, cancel kar do', 'Radd karo', 'Mat save karo', 'No, cancel it'.\n"
    "- CORRECT_PENDING_ENTRY: Correcting details of pending transaction. E.g. '500 nahi 800', 'Amount galat hai 800 karo'.\n"
    "- REPEAT_LAST_RESPONSE: Asking to repeat. E.g. 'Dobara sunao', 'Repeat karo', 'Pichhli entry kya thi?'.\n"
    "- STOP_ASSISTANT: Telling assistant to sleep. E.g. 'Bas, assistant band kar do', 'Stop assistant', 'Band karo'.\n"
    "- UNKNOWN: Anything unrelated or ambiguous.\n\n"
    "Contextual Clarifications (Instead of generic 'Aapne kya kaha'):\n"
    "- If customer name is missing for an entry command: set intent to 'UNKNOWN', and clarification_message:\n"
    "  * Hindi/Hinglish: 'Kaunse customer ke naam par likhna hai?'\n"
    "  * English: 'Which customer should I record this for?'\n"
    "- If amount is missing: set intent to 'UNKNOWN', and clarification_message:\n"
    "  * Hindi/Hinglish: 'Ek baar amount dobara bata dena. Kitne rupaye likhne hain?'\n"
    "  * English: 'How much amount should I record?'\n"
    "- If transaction type (credit vs payment) is unclear: set intent to 'UNKNOWN', and clarification_message:\n"
    "  * Hindi/Hinglish: 'Aap udhaar likhwana chahte hain ya jama entry karwani hai?'\n"
    "  * English: 'Do you want to record credit (udhaar) or payment received (jama)?'\n\n"
    "Rules:\n"
    "- If goods/credit was given, transaction_type is 'UDHAAR'.\n"
    "- If payment/cash was received/given back, transaction_type is 'JAMA'.\n"
    "- Return ONLY valid JSON in this schema:\n"
    '{"intent": "ENTRY"|"QUERY_CUSTOMER_BALANCE"|"QUERY_CUSTOMER_HISTORY"|"QUERY_DAILY_SUMMARY"|"QUERY_DATE_RANGE_SUMMARY"|"QUERY_CUSTOMER_LIST"|"AMBIGUOUS_ACCOUNT_ACTION"|"SETTLE_BALANCE"|"CONFIRM"|"CANCEL"|"CORRECT_PENDING_ENTRY"|"REPEAT_LAST_RESPONSE"|"STOP_ASSISTANT"|"UNKNOWN", '
    '"customer_name": "string or null", "amount": number or null, "transaction_type": "UDHAAR"|"JAMA"|null, '
    '"date_filter": "today"|"yesterday"|"last_7_days"|"this_month"|null, "corrected_amount": number or null, '
    '"clarification_message": "string or null", "language": "hindi"|"hinglish"|"english"}'
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
    """Builds an ordered list of candidate models with modern fallbacks."""
    configured = get_active_model_name()
    candidates = [configured]
    for model in RECOMMENDED_MODELS:
        if model not in candidates:
            candidates.append(model)
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
    r"(?<![\u0900-\u097F\w])दस\s*हजार(?![\u0900-\u097F\w])": "10000",
    r"(?<![\u0900-\u097F\w])पाँच\s*हजार(?![\u0900-\u097F\w])": "5000",
    r"(?<![\u0900-\u097F\w])पांच\s*हजार(?![\u0900-\u097F\w])": "5000",
    r"(?<![\u0900-\u097F\w])तीन\s*हजार(?![\u0900-\u097F\w])": "3000",
    r"(?<![\u0900-\u097F\w])दो\s*हजार\s*पाँच\s*सौ(?![\u0900-\u097F\w])": "2500",
    r"(?<![\u0900-\u097F\w])दो\s*हजार\s*पांच\s*सौ(?![\u0900-\u097F\w])": "2500",
    r"(?<![\u0900-\u097F\w])दो\s*हजार(?![\u0900-\u097F\w])": "2000",
    r"(?<![\u0900-\u097F\w])एक\s*हजार\s*पाँच\s*सौ(?![\u0900-\u097F\w])": "1500",
    r"(?<![\u0900-\u097F\w])एक\s*हजार\s*पांच\s*सौ(?![\u0900-\u097F\w])": "1500",
    r"(?<![\u0900-\u097F\w])एक\s*हजार(?![\u0900-\u097F\w])": "1000",
    # Devanagari hundreds
    r"ढाई\s*सौ": "250",
    r"डेढ़\s*सौ": "150",
    r"डेढ\s*सौ": "150",
    r"नौ\s*सौ": "900",
    r"आठ\s*सौ": "800",
    r"सात\s*सौ": "700",
    r"पाँच\s*सौ\s*पचास": "550",
    r"पाँच\s*सौ": "500",
    r"पांच\s*सौ": "500",
    r"चार\s*सौ": "400",
    r"तीन\s*सौ": "300",
    r"दो\s*सौ": "200",
    r"एक\s*सौ": "100",
    r"(?<![\u0900-\u097F\w])पचास(?![\u0900-\u097F\w])": "50",
    r"(?<![\u0900-\u097F\w])सौ(?![\u0900-\u097F\w])": "100",
    # ── Latin transliterations (composites first) ───────────────────────────
    r"\b(?:ek|1)\s*hazaar\s*(?:ek|1)\s*sau\b": "1100",
    r"\b(?:ek|1)\s*hazaar\s*(?:do|2)\s*sau\b": "1200",
    r"\b(?:ek|1)\s*hazaar\s*(?:teen|3)\s*sau\b": "1300",
    r"\b(?:ek|1)\s*hazaar\s*(?:char|chaar|4)\s*sau\b": "1400",
    r"\b(?:ek|1)\s*hazaar\s*(?:paanch|panch|5)\s*sau\b": "1500",
    r"\b(?:do|2)\s*hazaar\s*(?:paanch|panch|5)\s*sau\b": "2500",
    # Thousands
    r"\b(ek|1)\s*hazaar\b": "1000",
    r"\b(do|2)\s*hazaar\b": "2000",
    r"\b(teen|3)\s*hazaar\b": "3000",
    r"\b(paanch|panch|5)\s*hazaar\b": "5000",
    r"\bdas\s*hazaar\b": "10000",
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

def fast_match_voice_intent(raw_text: str) -> Optional[VoiceIntentResult]:
    """
    Quickly resolves high-confidence control phrases without waiting for external AI calls.
    Supports Hindi, Hinglish, and English commands.
    """
    text = strip_wake_word(raw_text).strip().lower()
    lang = detect_input_language(text)

    # 1. Confirmation fast path
    confirm_phrases = (
        "haan", "ha", "haa", "haanji", "haan ji", "yes", "confirm", "theek hai",
        "save karo", "haan save kar do", "save kar do", "thik hai", "theek hai save kar do",
        "theek hai save karo", "haan save karo", "save kijiye", "theek hai ji", "bilkul",
        "yes confirm", "yes save it", "save it", "ok save it", "confirm please", "yes please", "sure"
    )
    if text in confirm_phrases or text.startswith("haan ") or text.startswith("confirm ") or text.startswith("yes "):
        return VoiceIntentResult(intent=VoiceIntentType.CONFIRM, language=lang)

    # 2. Cancellation fast path
    cancel_phrases = (
        "nahi", "nahin", "no", "cancel", "radd karo", "cancel kar do",
        "mat karo", "mat save karo", "nahi cancel kar do", "nahi mat karo", "nahi cancel karo",
        "no cancel", "don't save", "cancel it", "cancel transaction", "abort"
    )
    if text in cancel_phrases or text.startswith("nahi ") or text.startswith("cancel ") or text.startswith("no "):
        return VoiceIntentResult(intent=VoiceIntentType.CANCEL, language=lang)

    # 3. Stop assistant fast path
    if text in ("bas", "stop", "stop assistant", "band karo", "assistant band kar do", "chup ho jao", "exit", "quit", "goodbye"):
        return VoiceIntentResult(intent=VoiceIntentType.STOP_ASSISTANT, language=lang)

    # 4. Repeat fast path
    if text in ("dobara sunao", "repeat", "repeat karo", "pichhli entry kya thi", "phir se bolo", "wapas bolo", "say again", "what did you say"):
        return VoiceIntentResult(intent=VoiceIntentType.REPEAT_LAST_RESPONSE, language=lang)

    # 5. Direct amount correction fast path (e.g., "500 nahi, 800" or "not 500, 800")
    correction_match = re.search(r"(?:(\d+)\s*(?:nahi|nhi|ke badle|ki jagah|not|instead of)\s*)?(\d+)\s*(?:rupaye|rs|₹|rupees)?(?:\s*karo|\s*likho|\s*hai|\s*make it)?$", text)
    if correction_match and any(w in text for w in ("nahi", "nhi", "badle", "jagah", "not", "instead")):
        new_amt = float(correction_match.group(2))
        return VoiceIntentResult(
            intent=VoiceIntentType.CORRECT_PENDING_ENTRY,
            amount=new_amt,
            corrected_amount=new_amt,
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


def classify_voice_command(user_text: str, context: Optional[dict] = None) -> VoiceIntentResult:
    """
    Classifies a voice command into an actionable Khata assistant intent.
    First evaluates deterministic fast paths, then invokes Google GenAI if required.
    """
    cleaned_input = strip_wake_word(user_text).strip()
    if not cleaned_input or len(cleaned_input) < 2:
        return VoiceIntentResult(intent=VoiceIntentType.UNKNOWN, clarification_message="Aapne kya kaha, main samajh nahi paaya. Kripya dobara bolein.")

    # 1. Fast path match
    fast_match = fast_match_voice_intent(cleaned_input)
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
    if context and context.get("pending_customer"):
        context_prompt = f"Active Context: Pending entry for customer '{context.get('pending_customer')}' of ₹{context.get('pending_amount')}.\n"

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

        return VoiceIntentResult.model_validate(data)
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

    # 7. Query customer balance
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
    has_udhaar = any(k in lower for k in ("udhaar", "diye", "diya", "credit", "gave"))
    has_jama = any(k in lower for k in ("jama", "jama kiye", "payment", "paid", "received"))

    if has_udhaar or has_jama:
        tx_type = "UDHAAR" if has_udhaar else "JAMA"
        amt_match = re.search(r"(\d+(?:\.\d{1,2})?)", lower)
        amt = float(amt_match.group(1)) if amt_match else None

        # Try to find customer name
        name = None
        # Hindi: "Sharma ji ka 200 udhaar likh do" or "Raju ne 500 jama kar diye"
        m_hi = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)\s+(?:ka|ko|ne|ke)?", lower)
        if m_hi and m_hi.group(1).lower() not in ("udhaar", "jama", "credit", "payment", "record", "give", "please", "ek", "do"):
            name = m_hi.group(1).title()

        # English: "record 500 credit for Ramesh" or "Ramesh paid 500" or "give 500 credit to Rahul"
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

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
    "Analyze the voice command and extract intent and details.\n\n"
    "Available Intents:\n"
    "- ENTRY: Recording credit given (UDHAAR) or payment received (JAMA). E.g. 'Rahul ko 500 udhaar diya', 'Ramesh ne 250 jama kiye', 'Sunita ke khate mein ek hazaar jama kar do'.\n"
    "- QUERY_CUSTOMER_BALANCE: Checking balance or khata of a customer. E.g. 'Rahul ka kitna baaki hai?', 'Ramesh ka khata check karo', 'Suresh ke kitne paise lene hain?', 'Rahul ka poora hisab sunao'.\n"
    "- QUERY_DAILY_SUMMARY: Asking for today's summary or transactions. E.g. 'Aaj kitna jama hua?', 'Aaj kitna udhaar diya?', 'Aaj ka total hisab bata', 'Aaj kitni transactions hui?'.\n"
    "- QUERY_DATE_RANGE_SUMMARY: Asking for historical report. E.g. 'Kal ki report dikhao', 'Pichhle saat din ka hisab', 'Is mahine kitna outstanding hai?'.\n"
    "- QUERY_CUSTOMER_LIST: Inquiring which customers owe money. E.g. 'Kis-kis customer ke paise baaki hain?', 'Sabse zyada udhaar kis customer ka hai?'.\n"
    "- CONFIRM: Explicit confirmation. E.g. 'Haan, save kar do', 'Confirm karo', 'Theek hai'.\n"
    "- CANCEL: Explicit cancellation. E.g. 'Nahi, cancel kar do', 'Radd karo', 'Mat save karo'.\n"
    "- CORRECT_PENDING_ENTRY: Correcting details of pending transaction. E.g. '500 nahi 800', 'Amount galat hai 800 karo'.\n"
    "- REPEAT_LAST_RESPONSE: Asking to repeat. E.g. 'Dobara sunao', 'Repeat karo', 'Pichhli entry kya thi?'.\n"
    "- STOP_ASSISTANT: Telling assistant to sleep. E.g. 'Bas, assistant band kar do', 'Stop assistant', 'Band karo'.\n"
    "- UNKNOWN: Anything unrelated or ambiguous.\n\n"
    "Rules:\n"
    "- If goods/credit was given, transaction_type is 'UDHAAR'.\n"
    "- If payment/cash was received/given back, transaction_type is 'JAMA'.\n"
    "- Return ONLY valid JSON in this schema:\n"
    '{"intent": "ENTRY"|"QUERY_CUSTOMER_BALANCE"|"QUERY_CUSTOMER_HISTORY"|"QUERY_DAILY_SUMMARY"|"QUERY_DATE_RANGE_SUMMARY"|"QUERY_CUSTOMER_LIST"|"AMBIGUOUS_ACCOUNT_ACTION"|"SETTLE_BALANCE"|"CONFIRM"|"CANCEL"|"CORRECT_PENDING_ENTRY"|"REPEAT_LAST_RESPONSE"|"STOP_ASSISTANT"|"UNKNOWN", '
    '"customer_name": "string or null", "amount": number or null, "transaction_type": "UDHAAR"|"JAMA"|null, '
    '"date_filter": "today"|"yesterday"|"last_7_days"|"this_month"|null, "corrected_amount": number or null}'
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
# Hindi Speech & Number Normalization
# ==========================================

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


def normalize_hindi_number_words(text: str) -> str:
    """Replaces common spoken Hindi number idioms (Devanagari + Latin) with digit equivalents.
    Also strips ₹ currency symbol prefix before digits (e.g. '₹2000' → '2000').
    """
    # Pre-process: strip ₹ prefix attached to digits
    normalized = re.sub(r'₹\s*(\d)', r'\1', text)
    for pattern, replacement in HINDI_NUMBER_WORDS.items():
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
    """
    text = strip_wake_word(raw_text).strip().lower()

    # 1. Confirmation fast path
    confirm_phrases = (
        "haan", "ha", "haa", "haanji", "haan ji", "yes", "confirm", "theek hai",
        "save karo", "haan save kar do", "save kar do", "thik hai", "theek hai save kar do",
        "theek hai save karo", "haan save karo", "save kijiye", "theek hai ji", "bilkul"
    )
    if text in confirm_phrases or text.startswith("haan ") or text.startswith("confirm "):
        return VoiceIntentResult(intent=VoiceIntentType.CONFIRM)

    # 2. Cancellation fast path
    cancel_phrases = (
        "nahi", "nahin", "no", "cancel", "radd karo", "cancel kar do",
        "mat karo", "mat save karo", "nahi cancel kar do", "nahi mat karo", "nahi cancel karo"
    )
    if text in cancel_phrases or text.startswith("nahi ") or text.startswith("cancel "):
        return VoiceIntentResult(intent=VoiceIntentType.CANCEL)

    # 3. Stop assistant fast path
    if text in ("bas", "stop", "stop assistant", "band karo", "assistant band kar do", "chup ho jao"):
        return VoiceIntentResult(intent=VoiceIntentType.STOP_ASSISTANT)

    # 4. Repeat fast path
    if text in ("dobara sunao", "repeat", "repeat karo", "pichhli entry kya thi", "phir se bolo", "wapas bolo"):
        return VoiceIntentResult(intent=VoiceIntentType.REPEAT_LAST_RESPONSE)

    # 5. Direct amount correction fast path (e.g., "500 nahi, 800" or "500 nahi 800")
    correction_match = re.search(r"(?:(\d+)\s*(?:nahi|nhi|ke badle|ki jagah)\s*)?(\d+)\s*(?:rupaye|rs|₹)?(?:\s*karo|\s*likho|\s*hai)?$", text)
    if correction_match and ("nahi" in text or "nhi" in text or "badle" in text or "jagah" in text):
        new_amt = float(correction_match.group(2))
        return VoiceIntentResult(
            intent=VoiceIntentType.CORRECT_PENDING_ENTRY,
            amount=new_amt,
            corrected_amount=new_amt
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

        return VoiceIntentResult.model_validate(data)
    except Exception as parse_err:
        logger.error(f"Voice intent json parsing error: {parse_err}. Raw output was: {raw_response_text}")
        return fallback_heuristic_intent(processed_text)


def fallback_heuristic_intent(text: str) -> VoiceIntentResult:
    """
    Deterministic regex-based fallback intent parser when AI provider is unavailable.
    """
    lower = text.lower()

    # 1. Customer list
    if any(q in lower for q in ("kis-kis", "kis kis", "sabse zyada udhaar", "udhaar kis customer")):
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_CUSTOMER_LIST)

    # 2. Ambiguous account management actions (e.g. "Tausif ka khata khatam kar do")
    # Must NOT infer destructive actions blindly; asks user for clarification
    if any(q in lower for q in (
        "khata khatam", "खाता खत्म", "khata band", "खाता बंद",
        "khata delete", "hisaab khatam", "hisab khatam", "खाता हटाओ",
        "hisaab barabar", "hisaab chukta", "hisab clear"
    )):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+(?:khata|hisaab|hisab)", lower)
        name = m.group(1).title() if m else None
        return VoiceIntentResult(
            intent=VoiceIntentType.AMBIGUOUS_ACCOUNT_ACTION,
            customer_name=name,
            clarification_message=f"Aap {name or 'customer'} ka baaki balance settle karna chahte hain ya account band karna chahte hain?"
        )

    # 3. Explicit settlement confirmation intent
    if any(q in lower for q in ("balance settle", "settle karo", "hisaab settle", "settle kar do", "chukta kar do")):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
        name = m.group(1).title() if m else None
        return VoiceIntentResult(intent=VoiceIntentType.SETTLE_BALANCE, customer_name=name)

    # 4. Customer history / ledger detail queries
    if any(q in lower for q in ("purana hisaab", "purana hisab", "pichhla hisab", "history", "transactions", "last transaction", "aakhiri transaction")):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+k[ai]\s+(?:purana|pichhla|history|transaction|last|aakhiri)", lower)
        if not m:
            m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)", lower)
        name = m.group(1).title() if m else None
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_CUSTOMER_HISTORY, customer_name=name)

    # 5. Daily summary (Checked before general 'hisab' query so 'aaj ka hisab' isn't treated as customer named 'Aaj')
    if any(q in lower for q in ("aaj kitna", "aaj ka total", "aaj ka hisab", "aaj ka hisaab", "aaj kitni transactions", "aaj ka summary")):
        tx_type = "JAMA" if "jama" in lower else ("UDHAAR" if "udhaar" in lower else None)
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DAILY_SUMMARY, date_filter="today", transaction_type=tx_type)

    # 6. Historical summary
    if "kal" in lower and any(w in lower for w in ("hisab", "hisaab", "summary", "total", "kitna")):
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DATE_RANGE_SUMMARY, date_filter="yesterday")
    if "pichhle saat din" in lower or "7 din" in lower:
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DATE_RANGE_SUMMARY, date_filter="last_7_days")
    if "is mahine" in lower or "mahine" in lower:
        return VoiceIntentResult(intent=VoiceIntentType.QUERY_DATE_RANGE_SUMMARY, date_filter="this_month")

    # 7. Query customer balance
    if any(q in lower for q in ("kitna baaki", "kitna baki", "khata check", "hisab sunao", "paise lene", "hisab bata", "ka hisab", "baki hai", "baaki hai")):
        m = re.search(r"([A-Za-z\u0900-\u097F]+(?:\s+ji)?)\s+ka\s+(?:kitna|khata|hisab|hisaab|poora|baki|baaki)", lower)
        name = m.group(1).title() if m else None
        if name and name.lower() not in ("aaj", "kal", "parso", "mahine"):
            return VoiceIntentResult(intent=VoiceIntentType.QUERY_CUSTOMER_BALANCE, customer_name=name)

    # 8. Entry
    if any(k in lower for k in ("udhaar", "jama", "diye", "diya", "jama kiye")):
        tx_type = "UDHAAR" if "udhaar" in lower else "JAMA"
        amt_match = re.search(r"(\d+(?:\.\d{1,2})?)", lower)
        amt = float(amt_match.group(1)) if amt_match else None
        name_match = re.search(r"^([a-zA-Z\u0900-\u097F]+(?:\s+ji)?)", lower)
        name = name_match.group(1).title() if name_match else None
        if name and amt:
            return VoiceIntentResult(intent=VoiceIntentType.ENTRY, customer_name=name, amount=amt, transaction_type=tx_type)

    return VoiceIntentResult(intent=VoiceIntentType.UNKNOWN, clarification_message="Kripya spasht karein ki aap kya karna chahte hain.")

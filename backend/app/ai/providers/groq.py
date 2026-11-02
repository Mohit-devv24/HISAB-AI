"""
Groq AI Provider for HisabAI.
Handles ultra-low latency speech-to-text (Whisper Large v3) and high-throughput LLM intent extraction.
"""
import os
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("HisabAI.Groq")

GROQ_CHAT_MODELS = [
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant"
]

class GroqProvider:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY", "").strip()

    def is_available(self) -> bool:
        return bool(self.api_key and len(self.api_key) > 8 and not self.api_key.startswith("your_"))

    def extract_json(self, user_prompt: str, system_prompt: str) -> Optional[Dict[str, Any]]:
        if not self.is_available():
            return None
        try:
            from groq import Groq
            client = Groq(api_key=self.api_key)
            for model_name in GROQ_CHAT_MODELS:
                try:
                    resp = client.chat.completions.create(
                        model=model_name,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        response_format={"type": "json_object"},
                        temperature=0.0
                    )
                    if resp and resp.choices and resp.choices[0].message and resp.choices[0].message.content:
                        return json.loads(resp.choices[0].message.content.strip())
                except Exception as model_err:
                    logger.warning(f"Groq model {model_name} failed: {model_err}")
                    continue
        except Exception as exc:
            logger.error(f"Groq provider error: {exc}")
        return None

    def transcribe(self, audio_bytes: bytes, filename: str = "voice.webm") -> str:
        if not self.is_available() or not audio_bytes:
            return ""
        try:
            from groq import Groq
            client = Groq(api_key=self.api_key)
            transcription = client.audio.transcriptions.create(
                file=(filename, audio_bytes),
                model="whisper-large-v3-turbo",
                prompt="Hinglish Indian Kirana store ledger: udhaar, jama, khata, rupaye, save kar do",
                language="hi",
                response_format="text"
            )
            return str(transcription).strip()
        except Exception as exc:
            logger.error(f"Groq Whisper transcription failed: {exc}")
            return ""

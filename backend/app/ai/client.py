import os
from google import genai

def get_genai_client():
    api_key = os.getenv("GEMMA_API_KEY", "")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)

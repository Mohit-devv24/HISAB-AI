import os

class Settings:
    PROJECT_NAME: str = "Hisab AI"
    VERSION: str = "2.0.0"
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./hisab_ai.db")
    GEMMA_API_KEY: str = os.getenv("GEMMA_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    SECRET_KEY: str = os.getenv("SECRET_KEY", "hisab-ai-production-super-secret-key-32chars")
    SESSION_COOKIE_NAME: str = "hisab_session"

settings = Settings()

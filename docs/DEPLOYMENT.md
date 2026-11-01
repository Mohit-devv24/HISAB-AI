# Hisab AI — Production Deployment Guide

## 1. Prerequisites
- Python 3.10+ (Tested up to Python 3.14)
- Virtual environment (`venv` or `conda`)
- SQLite 3 (or PostgreSQL 14+)

## 2. Environment Configuration
Copy `.env.example` to `.env` and configure keys:
```bash
cp .env.example .env
```
Key variables:
- `GEMMA_API_KEY`: API key for Google Gemini / Gemma natural language entity parsing.
- `GROQ_API_KEY`: (Optional) Groq Whisper audio transcription key.
- `SECRET_KEY`: High-entropy key for session token signing.
- `DATABASE_URL`: `sqlite:///./hisab_ai.db` or PostgreSQL connection URI.

## 3. Dependency Installation
```bash
pip install -r requirements.txt
```

## 4. Run Database Migrations
```bash
python scripts/migrate_database.py
```

## 5. Launch Application Server
For local development:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```
For production:
```bash
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

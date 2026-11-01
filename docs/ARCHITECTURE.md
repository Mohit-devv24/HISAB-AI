# Hisab AI — System Architecture & Design

## 1. Executive Summary
**Hisab AI** is an intelligent voice-first, multi-tenant digital bookkeeping and ledger platform purpose-built for Indian Kirana shopkeepers, merchants, and micro-enterprises. It translates natural, colloquial Hinglish/Hindi speech into financial ledger entries with instant acoustic feedback, email receipts, and payment claim reconciliation.

```
       [ Micro-Shopkeeper / Browser ]
                     │
       Speech (Web Speech / Groq Whisper)
                     ▼
  ┌───────────────────────────────────────────────┐
  │         FastAPI Enterprise Gateway            │
  │  (Auth, Rate Limit, Multi-Tenant Session)    │
  └──────────────────────┬────────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  AI Parsing  │  │ Voice State  │  │   Financial  │
│    Engine    │  │   Machine    │  │    Engine    │
│ (Gemma/Groq) │  │(Confirm/TTS) │  │ (Precision)  │
└───────┬──────┘  └──────┬───────┘  └──────┬───────┘
        │                │                 │
        └────────────────┼─────────────────┘
                         ▼
        ┌─────────────────────────────────┐
        │       Relational Database       │
        │      (SQLite / PostgreSQL)      │
        └─────────────────────────────────┘
```

## 2. Layered Architectural Decomposition

### 2.1 Backend (`backend/app/`)
- **`core/`**: Configuration via environment settings, cryptographic password hashing (bcrypt), JWT verification, and centralized exception handling.
- **`database/`**: SQLAlchemy ORM connectivity, pooling, and multi-tenant scoped sessions.
- **`models/`**: Domain entities including `User`, `Shop`, `Customer`, `Transaction`, `PaymentClaim`, `AuditLog`, `InterestSetting`, and `Reminder`.
- **`schemas/`**: Pydantic v2 schemas validating request/response DTOs.
- **`api/v1/`**: Modular API endpoints grouping Auth, Ledger, Voice, Dashboard, Reports, and Payments.
- **`services/`**: Pure business logic orchestrating data manipulation without web framework coupling.
- **`ai/` & `voice/`**: Dual-engine natural language parsing combining local regex intent matching with Gemma 2.5 / Gemini / Groq Whisper LLM fallback.
- **`financial/`**: Integer-precision paise calculations (avoiding floating point roundoff errors) and transaction state reconciliation.
- **`reports/`**: ReportLab PDF generator rendering thermal receipts and A4 ledger statements.

### 2.2 Frontend (`frontend/`)
- **`frontend/static/`**: Zero-build production SPA (`index.html`) featuring real-time Web Speech recognition, AudioContext VAD, dark/light theme toggle, and SVG charts.
- **`frontend/src/`**: Modern React/TypeScript component library structure for scaling UI modules into a decoupled client.

## 3. Financial Integrity & Invariants
1. **Paise Precision**: All currency storage and operations are conducted using integer paise (`amount_paise`), eliminating IEEE 754 floating point inaccuracies.
2. **Double-Click & Idempotency Protection**: Client and server-side request deduplication prevent accidental double debits/credits.
3. **Multi-Tenant Data Isolation**: All customer, transaction, and payment queries strictly enforce `shop_id` scoping to prevent cross-tenant data leakage.

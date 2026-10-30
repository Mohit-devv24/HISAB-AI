# 📖 Hisab AI — AI Powered Digital Khata (Suno Hisab)

> **Production-Ready, Hackathon AI Powered Digital Ledger** designed specifically for millions of local Indian shopkeepers (*kirana store owners, merchants, and small businesses*). 
> Activate hands-free by saying **"Suno Hisab"**, speak natural Hindi/Hinglish/English commands, query customer balances and daily summaries, review financial transactions before committing, and hear spoken responses with zero acoustic feedback.

---

## 🎯 1. Product & Problem Statement

Small shopkeepers across India lose significant revenue and hours of productivity managing physical paper notebooks (*bahi-khata*). These notebooks get torn, stained, misplaced, or damaged. Furthermore, mental arithmetic and binary floating-point calculations cause tally errors, disputes, and delayed credit collections.

**HisabAI** bridges this literacy and technology gap with a **Zero-Friction, Hands-Free Voice Khata**:
- **"Suno Hisab" Wake Word & Voice Activation**: Say *"Suno Hisab"* to wake up the ledger assistant with a pleasant two-tone audio chime (587.33Hz D5 → 880Hz A5 synthesized via Web Audio API).
- **Natural Spoken Hindi, Hinglish & English**: Understands natural conversational phrasing:
  - *"Suno Hisab, Sharma ji ka 200 rupaye udhaar likh do"*
  - *"Debit 1000 form Raju Account"*
  - *"Sharma ji ka kitna baaki hai?"*
- **Two-Step Financial Write Safety (Prepare → Confirm)**: Financial entries are never written directly to the database on raw voice recognition. The assistant prepares a pending operation, reads back the customer name, amount, and type, and waits for explicit user confirmation (*"Haan, save kar do"* or *"Nahi, cancel"*).
- **Duplicate Protection (Idempotent Writes)**: If a user repeats *"Haan"* or *"Confirm"*, the assistant returns the already-committed record without duplicating database rows.
- **Authoritative Database-Driven Queries**: Financial balances and summaries are **never** calculated by an LLM hallucination. The backend computes exact totals directly from SQL records using integer paise.
- **Acoustic Feedback Gating**: When the assistant speaks its response via SpeechSynthesis, browser microphone recognition is automatically muted to prevent acoustic feedback loops.
- **Exact Integer-Paise Precision**: Stored as integer paise (`amount_paise`) in SQLite (`1 Rupee = 100 Paise`), completely eliminating IEEE 754 binary floating-point accumulation drift.

---

## 2. 🛠️ Work Done on the Day V/S Pre-Existing Work
* **Pre-Existing Baseline (Prior to 10:00 AM):**
  * Core architecture, UI interface, CSS animations, initial backend endpoints, and baseline database models.
* **Built & Refined During Hackathon (Post 10:00 AM):**
  * **Bug Fixes & Error Handling:** Resolved edge cases in audio parsing, state transitions, and database session handling.
  * **Test Suite Expansion & Verification:** Debugged and validated the automated test suite across all test scenarios.
  * **Live Feedback Stabilization:** Fine-tuned browser speech recognition gating during Text-to-Speech synthesis to completely eliminate acoustic looping.

---

## 3. 📚 Third-Party Libraries, APIs & Starter Templates Credits
* **External APIs:**
  * **Google Gemini API / Gemma** (`google-genai` SDK) — Intent classification, entity extraction, and structured parsing.
  * **Reddit API** — Supplementary trend/sentiment analysis and community data integration.
* **Core Frameworks & Libraries:**
  * **Backend:** FastAPI, Uvicorn, SQLAlchemy 2.x, Pydantic v2, Python `zoneinfo`.
  * **Browser Web APIs:** Native Web Speech API (`SpeechRecognition`, `SpeechSynthesis`), Web Audio API (`AudioContext`, `OscillatorNode`).
  * **Testing:** Pytest, HTTPX `TestClient`.
* **Starter Templates & Assets:**
  * Base layout boilerplate and responsive UI starter scaffolding adapted and extended for the Khata theme.

---

## 4. 🤖 AI Coding Assistants Used
* **Anti-Gravity** — Workflow automation, debugging assistance, and prompt engineering.
* **Google Gemini** — Logic ideation, test case generation, and schema typing.
* **OpenAI ChatGPT** — Regex optimization, Hindi number word mapping, and documentation drafting.
* **GitHub Copilot** — In-line code completion and boilerplate generation.

---

## 5. 🎥 Demo Deliverables & Visual Proof

* **2-Minute Demo Video:** [Click Here to Watch Video Recording / Add Your Drive or YouTube Link Here](#)
* **Application Screenshots:**

| 1. Voice Wake & Listening State | 2. Daily Summary & Ledger Dashboard |

| :---: | :---: | :---: |
 | ![Screenshot 1](path/to/screenshot1.png) | ![Screenshot 3](path/to/screenshot3.png) |

---

## 💻 6. Tech Stack

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Backend Framework** | Python 3.11+ / FastAPI | High-performance async REST API with automatic OpenAPI docs |
| **Data Layer & ORM** | SQLAlchemy 2.x & SQLite | Reliable ACID relational persistence with automatic schema migrations |
| **Authoritative Accounting** | Integer Paise (`amount_paise`) | Exact fixed-precision arithmetic (no binary float drift) |
| **AI Model & Inference** | Google Gemma 4 / Gemini Flash | Structured intent & entity classification via official `google-genai` SDK |
| **Schema Validation** | Pydantic v2 | Strict request/response payload typing |
| **Timezone Management** | `zoneinfo` (`Asia/Kolkata`) | Indian business-day boundaries (no deprecated `utcnow`) |
| **Speech-to-Text** | Web Speech API (`hi-IN`) | Native browser Hinglish & Hindi audio transcription |
| **Speech Synthesis (TTS)** | `window.speechSynthesis` (`hi-IN`) | Natural Hindi spoken audio responses |
| **Audio Feedback** | Web Audio API Oscillator | Synthesized wake word acknowledgment chime (D5 → A5) |
| **Frontend UI** | HTML5, CSS3, Vanilla JS | 8-State FSM, Forest-green Khata aesthetic, responsive mobile-first UI |
| **Test Suite** | `pytest` / `unittest` & `TestClient` | In-memory SQLite (`sqlite:///:memory:`) test isolation (31 passing tests) |

---

## 🎙️ 3. "Suno Hisab" Voice Assistant Architecture & FSM

The frontend client operates on a deterministic **8-State Finite State Machine (FSM)**:

```
                  ┌──────────────┐
                  │   SLEEPING   │ <──────────┐
                  └──────┬───────┘            │
     Tap Mic / Start Voice│                   │ Stop Voice
                         ▼                    │
                ┌──────────────────┐          │
                │  LISTENING_IDLE  │ ─────────┘
                └────────┬─────────┘
        "Suno Hisab" heard│ (Chime Plays)
                         ▼
             ┌─────────────────────────┐
             │   WAKE_WORD_DETECTED    │
             └───────────┬─────────────┘
                         │ (Speech stream)
                         ▼
             ┌─────────────────────────┐
             │    CAPTURING_COMMAND    │
             └───────────┬─────────────┘
                         │ Silence / Final transcript
                         ▼
             ┌─────────────────────────┐
             │       PROCESSING        │ (POST /api/voice-assistant)
             └───────────┬─────────────┘
                         │
        ┌────────────────┴────────────────┐
        │ Read Query                      │ Entry (Write)
        ▼                                 ▼
┌──────────────────┐            ┌───────────────────────────┐
│     SPEAKING     │            │   AWAITING_CONFIRMATION   │
│ (TTS Mutes Mic)  │            │ (Shows Card / Listens)    │
└───────┬──────────┘            └─────────────┬─────────────┘
        │ Finished TTS                        │ User says "Haan" / "Nahi"
        └──────────────┐        ┌─────────────┘
                       ▼        ▼
                ┌──────────────────┐
                │  LISTENING_IDLE  │
                └──────────────────┘
```

### FSM States:
1. **`SLEEPING`**: Microphone is inactive.
2. **`LISTENING_IDLE`**: Continuous listening active in browser; waiting for wake word *"Suno Hisab"*.
3. **`WAKE_WORD_DETECTED`**: Plays two-tone Web Audio chime; shifts UI badge to *"WAKE WORD HEARD"*.
4. **`CAPTURING_COMMAND`**: Buffers spoken command following wake word.
5. **`PROCESSING`**: Dispatches intent request to `POST /api/voice-assistant`.
6. **`AWAITING_CONFIRMATION`**: Financial entry prepared; confirmation card displayed; assistant waits for *"Haan"* or *"Nahi"*.
7. **`SPEAKING`**: SpeechSynthesis utters Hindi response out loud; recognition input is gated to prevent acoustic looping.
8. **`ERROR_RECOVERY`**: Displays helpful guidance and automatically resumes idle listening.

---

## 🔄 4. Two-Step Financial Workflow (Prepare → Confirm)

HisabAI strictly separates financial preparation from database mutation:

```
[Shopkeeper Speaks] ──> "Suno Hisab, Sharma ji ka 250 udhaar likho"
                                 │
                                 ▼
                   [POST /api/voice-assistant]
                                 │
                                 ├──> Validates Entities (Customer="Sharma ji", ₹=250.00, Type=UDHAAR)
                                 ├──> Registers Pending Operation (5-min TTL)
                                 └──> NO Database Write Occurs!
                                 │
                                 ▼
[Assistant Speaks & Shows Card] ──> "Sharma ji ke khate mein ₹250.00 udhaar likhne hain. Kya main save kar doon?"
                                 │
                 ┌───────────────┴───────────────┐
                 │ User Speaks "Haan"            │ User Speaks "Nahi" / "Cancel"
                 ▼                               ▼
    [POST /api/voice-assistant]     [POST /api/voice-assistant]
    (action="confirm")              (action="cancel")
                 │                               │
                 ├──> Writes ₹250.00 (25,000 paise)├──> Discards Pending Op
                 ├──> Marks Status="committed"   └──> Zero DB Mutation
                 └──> Idempotency Guaranteed!
```

---

## 🗣️ 5. Supported Voice Commands & Queries

| Category | Example Spoken Hindi / Hinglish Phrase | Action Taken |
| :--- | :--- | :--- |
| **Credit Given (Udhaar)** | *"Suno Hisab, Sharma ji ka 200 rupaye udhaar likh do"* | Prepares ₹200.00 UDHAAR for Sharma ji |
| **Payment Received (Jama)** | *"Suno Hisab, Raju ne 500 rupaye jama kiye"* | Prepares ₹500.00 JAMA for Raju |
| **Customer Balance** | *"Suno Hisab, Sharma ji ka kitna baaki hai?"* | Authoritatively calculates net balance from SQL |
| **Daily Summary** | *"Suno Hisab, aaj ka hisab batao"* | Reports today's total Udhaar, Jama, and Net |
| **Historical Summary** | *"Suno Hisab, kal ka hisab batao"* | Reports yesterday's summary in IST |
| **Debtors List** | *"Suno Hisab, kis kis ka udhaar bacha hai?"* | Lists customers with pending balances |
| **Confirmation** | *"Haan", "Save kar do", "Confirm", "Theek hai"* | Commits pending transaction to database |
| **Cancellation** | *"Nahi", "Cancel karo", "Mat likho"* | Cancels pending transaction without writing |
| **Amount Correction** | *"400 nahi 600"* | Updates pending amount and re-confirms |
| **Repeat Response** | *"Dobara sunao", "Repeat karo"* | Re-utters the assistant's previous response |
| **Stop Assistant** | *"Band karo", "Stop assistant"* | Pauses assistant |

---

## 📂 6. Directory Layout

```text
HisabAI/
├── main.py            # FastAPI endpoints, /api/voice-assistant, pending operation registry, IST logic
├── models.py          # SQLAlchemy Transaction model & Pydantic v2 schemas (VoiceIntentResult, etc.)
├── database.py        # SQLite engine, SessionLocal, get_db & safe migration logic
├── ai_service.py      # Google Gemma integration, fast-match NLP, Hindi number words, wake word stripper
├── requirements.txt   # Pinned production dependencies
├── .env.example       # Environment template for Google AI Studio API key
├── .gitignore         # Clean repository hygiene (excludes .env and hisab_ai.db)
├── test_app.py        # Complete automated test suite (31 passing tests)
├── README.md          # Master architecture and user guide
└── static/
    └── index.html     # Voice Assistant UI with 8-State FSM, Web Audio chime, and live metrics
```

---

## 🚀 7. Quickstart Setup & Run

### Prerequisites
- Python 3.11 or higher
- A free Google AI Studio API Key from [aistudio.google.com](https://aistudio.google.com/app/apikey)

### Step 1: Open Terminal & Clone Directory
```bash
cd "Hisab Ai"
```

### Step 2: Create & Activate Virtual Environment
```bash
# macOS / Linux
python3 -m venv venv
source venv/bin/activate

# Windows
python -m venv venv
venv\Scripts\activate
```

### Step 3: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 4: Configure Environment
Copy `.env.example` to `.env` and configure your API key:
```bash
cp .env.example .env
```
Edit `.env`:
```env
GEMMA_API_KEY=your_actual_google_ai_studio_api_key_here
GEMMA_MODEL=gemma-4-26b-a4b-it
```

### Step 5: Run Automated Tests
Execute the comprehensive test suite:
```bash
pytest -v test_app.py
```
*Expected: **31 passed, 0 failed**.*

### Step 6: Start Server
```bash
uvicorn main:app --reload --port 8000
```
Open **[http://localhost:8000](http://localhost:8000)** in Chrome, Edge, or Safari.

---

## 🧪 8. Test Suite Verification (31 Tests)

The automated test suite in `test_app.py` runs with an isolated in-memory SQLite database (`sqlite:///:memory:`) and tests all critical components:

1. **System Health & AI Configuration**: `GET /api/health`
2. **Backwards Compatibility**: `POST /api/parse` and `POST /api/transaction`
3. **Integer-Paise Precision**: Verifies exact balances without IEEE 754 drift.
4. **IST Timezone Filtering**: Verifies Indian Standard Time boundaries around midnight.
5. **Customer Ledger Lookup**: Case-insensitivity, space normalization, and 404 responses.
6. **Hindi Speech Normalization**: Normalizes "paanch sau" → 500, "ek hazaar do sau" → 1200, "pachas" → 50.
7. **Wake Word Stripping**: Strips "Suno Hisab" and Devnagari variations.
8. **Fast-Path Deterministic Matching**: Resolves "haan", "theek hai", "nahi", "band karo", "500 nahi 800" without LLM overhead.
9. **Voice Assistant Read Queries**:
   - Customer balance lookups.
   - Ambiguous partial customer matches.
   - Daily and historical ("Kal") summaries.
   - Outstanding customer debt lists.
10. **Two-Step Write Confirmation & Idempotency**:
    - Step 1 (Entry) prepares transaction without database write.
    - Step 2 (Confirm) commits once.
    - Duplicate Step 2 calls return committed state without creating duplicate rows.
11. **Cancellation & Correction**:
    - Cancellation discards pending operation with zero DB writes.
    - Amount correction ("400 nahi 600") updates amount and awaits user confirmation.

---

## 🔒 9. Security, Privacy & Browser Constraints

- **Credential Redaction**: API keys are strictly redacted from error messages and logs before reaching clients.
- **Browser SpeechRecognition Boundaries**:
  - The Web Speech API requires an active, unmuted browser tab running on `localhost` or `HTTPS`.
  - Browsers do **not** support silent background listening when the browser window is closed or entirely minimized due to OS-level microphone security sandbox restrictions.
  - Native background wake-word listening on desktop/mobile requires packaging the app in an Electron/Tauri container or native Android/iOS background service.
- **Acoustic Loop Prevention**: Recognition is programmatically paused during Text-to-Speech playback to ensure the assistant does not transcribe its own voice.

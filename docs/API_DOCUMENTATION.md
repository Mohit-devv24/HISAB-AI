# Hisab AI — API Documentation (v1)

## Base URL
- Local: `http://127.0.0.1:8000`
- API Prefix: `/api` (and `/api/v1`)

---

## 1. Authentication & Session

### `POST /api/auth/signup`
Register a new shopkeeper account and provision their initial primary shop.
- **Request Body**:
  ```json
  {
    "email": "shop@example.com",
    "password": "Password123!",
    "confirm_password": "Password123!",
    "full_name": "Ramesh Kumar",
    "shop_name": "Ramesh Kirana Store"
  }
  ```
- **Response**: `200 OK` with session cookie and user profile.

### `POST /api/auth/login`
Authenticate user with credentials.
- **Request Body**:
  ```json
  {
    "email": "shop@example.com",
    "password": "Password123!"
  }
  ```

### `POST /api/auth/logout`
Terminates active session and clears HTTP-only authentication tokens.

---

## 2. Voice Assistant & Natural Language

### `POST /api/voice-assistant`
Process text or transcribed natural language query with context-aware state machine.
- **Request Body**:
  ```json
  {
    "transcript": "Ramesh ko 500 udhaar likho",
    "confirmed": false,
    "pending_operation_id": null
  }
  ```
- **Response**:
  ```json
  {
    "reply_message": "Maine Ramesh ka ₹500 ka udhaar prepare kar liya hai. Kya save karein?",
    "action_taken": "prepared_transaction",
    "requires_confirmation": true,
    "pending_operation": { ... }
  }
  ```

### `POST /api/voice-audio`
Upload raw PCM/WebM/WAV audio blob for server-side Groq Whisper transcription and execution.

---

## 3. Ledger & Transactions

### `GET /api/transactions`
List paginated transactions for the active shop.
- **Query Params**: `limit=50`, `offset=0`, `customer_name=`, `type=UDHAAR|JAMA`

### `POST /api/transactions`
Directly record a verified financial ledger entry.
- **Request Body**:
  ```json
  {
    "customer_name": "Ramesh Kumar",
    "customer_email": "ramesh@gmail.com",
    "amount": 500.00,
    "type": "UDHAAR",
    "notes": "Dal, Chawal"
  }
  ```

---

## 4. Payment Claims & Verification

### `GET /api/payment-claims`
List UPI/QR payment claims submitted by customers awaiting merchant review.

### `POST /api/payment-claims/{claim_id}/approve`
Owner/Manager approves a payment claim, automatically crediting JAMA and balancing customer ledger.

---

## 5. Reports & PDFs

### `GET /api/reports/receipt/{transaction_id}`
Download 80mm thermal receipt PDF for a specific transaction.

### `GET /api/reports/statement/{customer_name}`
Generate full A4 customer account ledger statement PDF.

# Hisab AI — Security Policy & Controls

## 1. Authentication & Password Security
- **Bcrypt Hashing**: Passwords stored using industry-standard salted bcrypt. Plaintext credentials are never written to disk or logs.
- **Session Tokens**: HTTP-only, Secure, SameSite cookies guard against Cross-Site Scripting (XSS) and token hijacking.
- **Constant-Time Comparison**: Key and password validations employ constant-time checks to prevent timing attacks.

## 2. Multi-Tenant Authorization & RBAC
- Every transactional query filters implicitly by `shop_id` derived from the authenticated session context.
- Staff accounts are restricted from destructive actions, settings modifications, and manual ledger overrides.

## 3. Financial & Input Sanitization
- **Strict Integer Arithmetic**: Storing currency in integer paise prevents floating-point rounding exploits.
- **CSV & PDF Injection Neutralization**: Dynamic user input is sanitized to escape formula triggers (`=`, `+`, `-`, `@`) when exporting spreadsheets.
- **Secret Redaction**: Logging middleware redacts sensitive fields (`password`, `token`, `api_key`, `secret`).

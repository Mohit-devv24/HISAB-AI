# Hisab AI — Database Schema & Data Dictionary

The persistence layer supports both SQLite (embedded production/dev) and PostgreSQL with ACID compliance and foreign key referential integrity.

---

## 1. Entity Relationship Overview

```
 [User] 1 ──── n [ShopMember] n ──── 1 [Shop]
                                          │
                  ┌───────────────────────┼───────────────────────┐
                  │ 1                     │ 1                     │ 1
                  ▼ n                     ▼ n                     ▼ n
             [Customer]             [Transaction]           [PaymentClaim]
                  │                       │
                  └───────────┬───────────┘
                              │
                              ▼
                        [AuditLog]
```

---

## 2. Table Definitions

### `users`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Primary user ID |
| `email` | VARCHAR(255) | UNIQUE, NOT NULL, INDEX | Login email address |
| `hashed_password` | VARCHAR(255) | NULLABLE | Bcrypt password hash |
| `full_name` | VARCHAR(255) | NOT NULL | Display name |
| `google_id` | VARCHAR(255) | NULLABLE, UNIQUE | Google OAuth sub ID |
| `role` | VARCHAR(50) | DEFAULT 'owner' | System role |
| `created_at` | DATETIME | NOT NULL | UTC creation timestamp |

### `shops`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Shop entity ID |
| `owner_id` | INTEGER | FK -> users.id | Primary owner reference |
| `name` | VARCHAR(255) | NOT NULL | Store or enterprise name |
| `currency` | VARCHAR(10) | DEFAULT 'INR' | Shop primary currency |
| `created_at` | DATETIME | NOT NULL | Registration date |

### `customers`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Customer ID |
| `shop_id` | INTEGER | FK -> shops.id, INDEX | Multi-tenant shop partition |
| `name` | VARCHAR(255) | NOT NULL, INDEX | Customer full name |
| `phone` | VARCHAR(50) | NULLABLE | Mobile phone number |
| `email` | VARCHAR(255) | NULLABLE | Receipt notification email |
| `created_at` | DATETIME | NOT NULL | Customer record created |

### `transactions`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | INTEGER | PK, AUTOINCREMENT | Transaction sequence ID |
| `shop_id` | INTEGER | FK -> shops.id, INDEX | Multi-tenant partition |
| `customer_id` | INTEGER | FK -> customers.id, INDEX | Related customer record |
| `amount_paise`| INTEGER | NOT NULL | Financial amount in integer paise |
| `type` | VARCHAR(20) | NOT NULL (UDHAAR/JAMA) | Debit vs Credit designation |
| `notes` | TEXT | NULLABLE | Transaction narration / items |
| `created_at` | DATETIME | NOT NULL, INDEX | IST transaction timestamp |

### `payment_claims`
Stores digital payments declared by customers via UPI/QR code awaiting merchant approval.

### `audit_logs`
Immutable historical log recording all creation, mutation, deletion, and rollback actions with user and IP references.

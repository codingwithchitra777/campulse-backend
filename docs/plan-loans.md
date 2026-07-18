# Plan — Personal loan ledger (money lent / borrowed)

## Goal

Track informal money lent to / borrowed from people. Private, single-user, per
counterparty. **Completely separate from trading P/L, holdings, and the equity
curve** — a loan is not a trade.

## Data model

**`loans`** (idempotent init_db)
- `loan_id` VARCHAR PK
- `user_id` VARCHAR (owner)
- `direction` VARCHAR — `lent` (they owe me) | `borrowed` (I owe them)
- `counterparty` VARCHAR — person's name (free text)
- `principal` NUMERIC(20,4)
- `currency` VARCHAR — `KHR` | `USD`
- `loan_date` DATE
- `due_date` DATE NULL
- `note` TEXT NULL
- `status` VARCHAR — `open` | `partial` | `settled` (derived, cached)
- `created_at` TIMESTAMP

**`loan_repayments`**
- `repayment_id` VARCHAR PK
- `loan_id` VARCHAR FK
- `amount` NUMERIC(20,4)
- `paid_date` DATE
- `note` TEXT NULL
- `created_at` TIMESTAMP

## Core rules (real-world best practice)

1. `outstanding = principal − sum(repayments)`. Never store a paid/unpaid bool.
2. Status derived: `open` (0 back) → `partial` (some) → `settled` (outstanding ≤ 0).
3. **Never touches trading P/L / equity chart.** Own ledger, own totals.
4. **Per-currency, never blended.** KHR and USD totals shown separately.
5. Money as `Decimal`, stored NUMERIC(20,4). No floats.
6. Dates on loan and every repayment.
7. Two sections: "Owed to me" (lent) vs "I owe" (borrowed).
8. Close = status change, not delete. Keep repayment trail.

## Backend

- `LoanRepository` — create/list/get/delete loan; add/delete repayment;
  `sum_repayments(loan_id)`; recompute+persist status after any repayment change.
- `LoanService` — outstanding + status logic; per-currency, per-direction totals.
- Endpoints (`endpoints/loans.py`, JWT user-scoped):
  - `GET /api/loans` — list with outstanding + status, optional `direction`/`status` filter.
  - `POST /api/loans` — create.
  - `DELETE /api/loans/{id}`.
  - `POST /api/loans/{id}/repayments` — record a repayment → returns loan + **sends Telegram receipt** (below).
  - `DELETE /api/loans/{id}/repayments/{rid}`.
  - `GET /api/loans/summary` — per-currency totals for each direction.
- Schemas in `schemas/loans.py`.

## Telegram receipt on repayment (user's request)

On `POST /repayments`, after persisting, send a **forwardable** message to the
user's linked Telegram chat via the existing `telegram_client.send_message`:

```
✅ Repayment recorded

Sok repaid $200.00 on 18 Jul 2026.
Outstanding: $350.00 of $550.00.
— via CamPulse
```

- Resolve the chat via the linked-account chat_id (resolve_primary → user_links).
- Best-effort: wrapped in try/except, never blocks the API response.
- Wording is neutral and forward-ready (user forwards it to the borrower as a receipt).
- Dormant if no linked Telegram / no bot token (same gate as reminders).

## Due-date reminders (reuse existing daemon)

Extend the reminder daemon: for loans with `due_date` within N days and not
settled, send "Sok owes you $500, due in 3 days." Gated on
`TELEGRAM_WEBHOOK_SECRET` like the others.

## Frontend

- Route `/loans` + nav link.
- Model `Loan`, `LoanRepayment`, `LoanSummary`.
- ApiService: getLoans / getLoanSummary / createLoan / deleteLoan /
  addRepayment / deleteRepayment.
- Page: two sections (Owed to me / I owe), per-currency subtotals, status badge,
  create form, repayment drawer, settled filter.
- Reuse `money` pipe, `rxResource`, admin/history layout.
- i18n en/km LOANS block.

## Scope for MVP

- Principal + repayments only (no interest accrual — bake interest into amounts).
- Telegram receipt on repayment + due-date reminder.

## Tests

- Outstanding + status transitions (open→partial→settled).
- Per-currency totals never blended.
- Repayment beyond principal → settled (outstanding floored at 0 for display).
- Delete repayment recomputes status.
- Telegram receipt: monkeypatch send_message, assert one forwardable message,
  assert no-op when no linked chat.
- Disposable `pytest_` user, cleaned by exact id.

## Verify

- pytest (new + full suite green).
- Live: create loan → record repayment → confirm Telegram receipt arrives.

## Rollout

- Commit + push, **manual backend redeploy**, live-verify.

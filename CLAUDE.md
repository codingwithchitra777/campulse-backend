# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`campulse-backend` is the **FastAPI (Python 3.12) backend** for CamPulse — a Cambodia-focused trading journal / portfolio tracker. It serves the `campulse-web` Angular app (sibling repo, has its own CLAUDE.md) **and** hosts the Telegram bot as a webhook. It's one of three CamPulse repos that share a single database:

- `campulse-web` — Angular frontend (talks to this API).
- `campulse-backend` — this service (REST API + Telegram webhook + background jobs).
- `campulse-bot` — the **retired** standalone polling bot; its features now live here as `app/services/telegram_bot.py`. Don't add features there.

> **Heads-up:** `README.md` describes an aspirational "Spring Boot / Java 21" stack and a Lowest-Price-First roadmap. That's marketing, not the code. The real stack is **FastAPI + psycopg2 + raw SQL** (see `pyproject.toml`).

## Commands

Always use the project venv's Python — the base interpreter is missing deps (e.g. `logfire`), which fails imports:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:warnings           # full suite
./.venv/Scripts/python.exe -m pytest tests/test_trades.py -q     # one file
./.venv/Scripts/python.exe -m pytest tests/test_trades.py::test_buy_trade_is_persisted   # one test
./.venv/Scripts/python.exe -m py_compile app/services/foo.py     # quick syntax check before a test run
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001   # run locally (e.g. for web live-verify)
```

There is no lint step configured.

## Critical gotchas (read before running anything)

1. **Tests run against the PRODUCTION Neon Postgres.** There is no separate test DB — `get_db()` uses `settings.database_url` for everything, and `init_db()` runs on the first connection. So `pytest` (and even importing `app.main`) **connects to and migrates prod**. Consequences:
   - Tests that don't clean up leave `pytest_*` users behind. After a run, delete them by **exact enumerated id** — never a broad `LIKE`/pattern delete:
     ```python
     cur.execute("SELECT user_id FROM users WHERE user_id LIKE 'pytest_%'")
     ids = [r[0] for r in cur.fetchall()]
     cur.execute("DELETE FROM users WHERE user_id = ANY(%s)", (ids,))   # enumerate first, then delete exact ids
     ```
   - Use disposable ids and delete-by-exact-id in fixtures for new tests (see `tests/test_watchlist.py`, `test_gold.py`).
2. **The deployed backend does NOT auto-deploy** (FastAPI Cloud at `campulse-backend.fastapicloud.dev`). After pushing, changes are live only after a **manual redeploy** — always remind the user.
3. **Schema migrations live in `init_db()`** (`app/db/database.py`) as idempotent `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` / guarded `ALTER … TYPE`. They apply to prod the moment any code (including a test run) connects. Add new tables/columns there, keeping them idempotent.

## Architecture

Layered, no ORM — raw SQL via `psycopg2`:

```
app/api/v1/endpoints/*  →  app/services/*  →  app/repositories/*  →  app/db/database.py (pool + get_db + init_db)
```

- **DB access**: `get_db()` is a context manager that commits on success / rolls back on exception, backed by a `ThreadedConnectionPool`. Repositories run SQL with snake_case columns and return dicts with **camelCase** keys (except `chat_id`, `market`, `currency`, `note`, `tags`). When you add a column, thread it through the repo's SELECT lists + returned dicts (there are several per table).
- **Auth**: JWT. `app/api/deps.py` `get_current_user` / `require_admin` decode the bearer token; endpoints depend on them. Google / Telegram (login widget, Mini App `initData`) / demo logins all mint a JWT in `endpoints/auth.py`.
- **Account identity**: Google is the canonical account; a Telegram id is an alias linked via a one-time code. `services/identity.resolve_primary()` maps alias→primary at login and bot-write time (`user_links` / `link_codes`), so a linked Telegram user reads/writes the same portfolio.
- **Multi-market money** (the load-bearing design):
  - `services/markets.py` defines markets `CSX | US | GOLD_KH`, their currency (`KHR | USD`), and `quantize_money(value, currency)` (KHR=0dp, USD=2dp).
  - Money is **`Decimal` end-to-end**, stored as `NUMERIC` (never float); FastAPI serializes `Decimal` to a JSON number.
  - `services/price_providers.PriceRouter` routes `get_latest_price(market, symbol)` to `CSXProvider` (CSX feed), `FinnhubProvider` (US, needs `FINNHUB_API_KEY`; also company-news + symbol search), or `ManualProvider` (local gold `XAU-KH`, priced off the admin board in `manual_prices`).
  - Matching (`services/best_profit_matcher.BestProfitMatcherService.match_sell` / `simulate_sell`) is **cheapest-lot-first** (best-profit) — **not** LIFO, despite the historical naming that was purged; don't reintroduce "LIFO" in code or copy. It's **scoped by market** so a US `ABC` never matches a CSX `ABC`. `services/trade_service.record_trade()` is the single write path shared by `POST /trades` and the Telegram bot.
- **Background daemon threads** (single-instance assumption; all **dormant unless `TELEGRAM_WEBHOOK_SECRET` is set**, so they're no-ops in tests/local): `PricingService` price-refresh + daily snapshot loop; `reminder_scheduler` (08:00/14:00 Asia/Phnom_Penh session reminders); `alert_service` (polls `price_alerts`, messages the user's linked Telegram on a cross). Started from `main.py`'s lifespan.
- **Telegram**: `endpoints/telegram.py` validates the `X-Telegram-Bot-Api-Secret-Token` header, then `TelegramBotService` dispatches slash commands and replies via `services/telegram_client.py` (module-level `send_message` / `send_photo`). Chart images are rendered with matplotlib in `app/utils/chart_renderer.py`.
- **Coach** — two coaches over one snapshot. `services/rule_coach.py` is the **free default**: deterministic sentences built from the snapshot, no key, no credit, no network, so `GET /api/ai/insights` can never fail on billing. `services/ai_coach.py` is the **optional paid pass** (`POST /api/ai/insights`) for cross-signal reading the templates can't do. Both consume the same `build_snapshot()` output — keep it that way so they can't drift, and hold both to the same descriptive-never-advisory rule (`test_rule_coach.py::test_never_gives_advice`).
- **AI Coach** (`services/ai_coach.py`): `AnalyticsService.compute()` → `build_snapshot()` → Claude → cached in `ai_insights` keyed by `snapshot_hash`. Two rules the code (not the prompt) enforces: **descriptive, never advisory** (real money, we're not licensed advisers — `DISCLAIMER` is appended server-side), and **no PII off-box** — `build_snapshot()` is an *allow-list*, never a dict spread, so a new `users`/`trades` column can't leak. `test_ai_coach.py::test_snapshot_leaks_no_pii` guards this; keep it passing. Degrades to `enabled: false` with no `ANTHROPIC_API_KEY` rather than erroring. Regenerates only when the snapshot hash changes, and at most once per user per day.
- **Config**: `app/core/config.py` is a frozen `Settings` dataclass read from env **at import time** (`DATABASE_URL`, `JWT_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_BOT_USERNAME`, `FINNHUB_API_KEY`, `GOOGLE_CLIENT_ID`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`).

Feature endpoints beyond core trading: `analytics` (win rate, hold time, per-currency + per-journal-tag stats), `watchlist`, `alerts`, `market` (news/search/quote), `ai` (coach insights + an admin-only `GET /api/ai/health` that makes one real ~$0.0003 call to prove the key works on the deployed box), `admin` (users, stats, manual gold-price board). Keep response shapes in sync with `campulse-web`'s `src/app/models.ts`.

## Verifying a change

Because tests hit prod and the bot threads are gated off locally, verify with `pytest` (behavior-preserving refactors must keep the existing suite green) plus, for provider/webhook paths, either monkeypatched HTTP or a live check against the deployed base URL. Add tests that create rows under disposable ids and clean up by exact id on teardown.

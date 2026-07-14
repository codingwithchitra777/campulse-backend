# Plan: International stocks (Finnhub) + Local Cambodian gold

## Status
- **Phase 1 — DONE** (schema + provider seam, behavior-preserving). `market`/`currency`
  columns on trades/allocations/price_history (default CSX/KHR), threaded through
  repos, `record_trade`, the matcher, and portfolio output; `markets.py` constants;
  `price_providers.py` (`CSXProvider` + `PriceRouter`); `finnhub_api_key` config.
  **Scope note:** the Decimal (INT→NUMERIC) migration was intentionally moved to the
  start of Phase 2, where USD/gold actually need sub-unit precision — doing it in
  Phase 1 would have flipped integer money to floats on the wire with no consumer yet.
  Market-keyed matching (the `ABC` collision fix) also lands in Phase 2 with the first
  non-CSX symbols, since the collision cannot occur while only CSX exists.
- Phases 2–4 — pending.


## Decisions locked in
- **Portfolio rollup:** group by currency — separate KHR and USD sub-totals, **no blending / no FX** in phase 1. A converted grand-total can come later.
- **Gold:** **local Cambodian gold** (not global XAU/USD spot). Cambodian gold trades in *chi / damlong* at shop-board prices that don't track world spot — and there is **no free API**, so its price is **manually maintained** (admin sets the daily board). Global XAU/USD stays a trivial future add because the provider abstraction supports it.
- **Data source for international:** Finnhub (`/quote?symbol=…`). Verify free-tier limits/coverage at build time (do not assume).

## The core problem (why this isn't just an API call)
Three assumptions are welded into every layer today:
1. **Whole-riel integers** — `TradeCreate.price:int`, DB `price INT`, allocations INT, `price_history INT`, `int(...)` throughout `portfolio.py`. USD cents & fractional gold need **Decimal**.
2. **One currency, summed blindly** — `portfolio()` adds P/L across all tickers as one unit. Mixed currency ⇒ meaningless totals ⇒ need **currency per asset + grouping**.
3. **One provider, bare-ticker key** — `pricing.get_latest_price(ticker)` is CSX-only, and `ABC` already exists on CSX (collision risk vs a US `ABC`). Need **market-aware routing**.

## Keystone: the Instrument / market model
Every trade gains **`market`** (`CSX` | `US` | `GOLD_KH`) and **`currency`** (`KHR` | `USD`). The matching/position/grouping key becomes **(user_id, market, ticker)** instead of (user_id, ticker) — this kills the `ABC` collision and enables per-currency grouping. The best-profit/LIFO matcher itself is **unchanged** (it already operates on a per-symbol trade list; we just also filter by market).

Defaults for local gold (change if wrong):
- Unit = **chi** (1 damlong = 10 chi). qty is in chi; price is per chi.
- Currency = **USD** (Phnom Penh shops commonly quote USD; configurable).
- Price is **admin-maintained** — one daily board for everyone (not per-user).

## Provider architecture
`PriceProvider.get_price(market, symbol) -> PriceResult`, with a router keyed on `market`:
- `CSXProvider` — existing CSX fetch (all stocks in one call, 45s cache, snapshot loop). Unchanged.
- `FinnhubProvider` — `GET /quote?symbol=AAPL`. **One symbol per call, ~60/min free tier** ⇒ keep the existing background-refresh-loop + per-symbol cache; never fetch per request. Needs `FINNHUB_API_KEY`.
- `ManualProvider` — reads the latest admin-set price from a new `manual_prices` table. Serves `GOLD_KH`. Also feeds the daily snapshot so the equity chart works.

## Backend changes (phased)

### Phase 1 — Money-model foundation (behavior-preserving; no new features)
- **DB migration** (`ALTER COLUMN … TYPE NUMERIC(20,4)` — lossless from INT): `trades.price/qty/commission`, all `allocations.*_price/*_qty/*_commission`, `price_history.price`.
- **Add columns:** `trades.market VARCHAR NOT NULL DEFAULT 'CSX'`, `trades.currency VARCHAR NOT NULL DEFAULT 'KHR'`; same `market`/`currency` on `allocations` and `price_history`. Backfill existing rows → `CSX`/`KHR` (they already are).
- **Python:** switch money to `Decimal` (`int(...)` → `Decimal(...)`); Pydantic `int` → `Decimal` in `schemas/trade.py`; serialize Decimal as JSON number.
- **Repos:** `list_trades`, `list_allocations`, `position_detail`, `_ticker_set`, matching key → include `market`. `next_seq` stays per (user, market, ticker) or per user (decide — recommend per user for a single global sequence).
- **Provider interface** extracted; `CSXProvider` wraps today's `PricingService`. Router returns CSX for market=CSX.
- Full existing test suite must stay green — this phase changes representation, not behavior.

### Phase 2 — Finnhub + US equities
- `config.finnhub_api_key`; `FinnhubProvider`; register `US` in the router.
- Symbol lookup endpoint (Finnhub `/search`) so the frontend can validate/pick US symbols.
- Background refresh extended to the union of held US symbols (rate-limit aware).
- `portfolio()` returns `currency` per position; endpoint groups or frontend groups.

### Phase 3 — Local gold
- `manual_prices` table (`market, symbol, currency, price, updated_at, updated_by`).
- Admin endpoint `PUT /api/admin/manual-price` to set today's local gold board; `ManualProvider` reads it; snapshot writer includes it.
- Seed instrument `GOLD_KH / XAU-KH` (chi), currency USD.

### Phase 4 — Frontend
- **Record Trade:** asset-type picker (CSX stock | US stock | Local gold) → drives market+currency; CSX = existing dropdown, US = symbol search, gold = fixed symbol + chi qty.
- **Currency formatting** helper replaces every hardcoded "riel"; format per position currency.
- **Portfolio:** grouped sections with per-currency sub-totals (KHR / USD).
- **Dashboard/History:** currency-aware columns; per-currency P/L.
- **Admin:** a "Local gold price" card to set the daily board.
- Models: `Trade`/positions gain `market`, `currency`.

### Telegram bot (deferred within this effort)
Bot `/buy /sell /price` stay CSX-default in phases 1–3 (they call `record_trade`, which now needs market/currency — default to CSX/KHR). Add market syntax (e.g. `/buy US:AAPL 172.34 10`) in a later pass.

## Migration & safety
- Migration is additive + type-widening (INT→NUMERIC is lossless); existing CSX data keeps working untouched after backfill.
- All money-cleanup work verified against the existing test suite before any feature lands.

## Risks / to verify at build time
- **Finnhub free tier**: real-time coverage for the intended US symbols and exact rate limits — verify, don't assume.
- **No API for local gold** — accepted; admin-maintained board is the mechanism.
- **Decimal JSON serialization** across FastAPI → Angular (numbers vs strings) — pin the approach in Phase 1.
- **`next_seq` semantics** across markets — confirm single global sequence per user is desired.

## Verification per phase
Real run each phase (per project norm): local backend on :8001, disposable users, live-verify the new flow in the browser, then revert/clean by exact id. Phase 1 = full suite green. Phase 2 = a real US quote end-to-end. Phase 3 = set gold board + record a gold trade + see it valued.

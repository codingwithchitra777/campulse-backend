# Corporate actions: bonus shares & splits (PPSP 1:1 — ex-date 27 July)

## Why now

PPSP distributes a 1:1 bonus share with ex-date **27 July**. On that day the CSX
feed will print roughly half the old price. If CamPulse does nothing, every PPSP
holder simultaneously sees:

1. a fake ~−50% unrealised loss (journal still says old qty @ old cost),
2. false "below" Telegram alerts firing on the adjusted price,
3. permanently wrong realised P/L on every future sell (proceeds at adjusted
   prices matched against pre-bonus cost basis).

Deadline-driven: must be deployed and tested before 27 July.

## The invariant

A bonus/split changes *units*, not *value*. For multiplier `m` (1:1 bonus → m=2):
share count × m, per-share cost ÷ m, **total invested unchanged**. Everything
below is in service of that invariant across the matcher, portfolio, equity
chart, analytics, and alerts.

## Design decision — how the adjustment is represented

Three candidate representations; the constraints that decide it:

- The **matcher** (`best_profit_matcher`) consumes cheapest-first and computes
  `open = qty - Σ allocations`; allocations snapshot `buyPrice/buyQty` at match
  time, so *already-booked* realised P/L is immune to later row edits.
- The **equity series** (`portfolio._equity_series`) *replays trades
  chronologically* against `price_history` snapshots: holdings on day D come
  from trades dated ≤ D, valued at day-D prices.

**Rejected: bonus as a zero-price BUY row.** Cheapest-first would consume the
free lot first → one giant fake win then a fake loss. Distorts every per-sale
stat even though full-liquidation totals come out right.

**Rejected: scale the original buy rows in place (qty × m on the old date).**
The equity replay would then hold the doubled quantity *before* the ex-date,
valued at pre-adjustment prices → fake pre-history double-count.

**Chosen: per-lot cost split, dated at the ex-date.** For each BUY row with open
quantity `r > 0` of the affected (user, market, symbol):

- update the row's `price ÷= m` (qty, date, commission untouched);
- insert a new BUY row: `qty = floor(r × (m−1))`, `price = old_price / m`,
  `commission = 0`, `order_date = ex_date`, `corp_action_id` set,
  note "PPSP 1:1 bonus shares".

Why this satisfies every consumer:

| Consumer | Effect |
|---|---|
| Matcher | Both rows carry the adjusted per-share cost; `open` stays in consistent share units; future sells book honest P/L. No zero-cost lot exists. |
| Equity chart | Pre-ex-date replay: old qty × raw snapshot prices. From ex-date: qty × m × adjusted prices. Continuous — **no `price_history` rewrite needed** (pre-ex snapshots stay raw and match pre-ex holdings). |
| Booked P/L | Allocations snapshotted `buyPrice` at sale time → history untouched. |
| Fully-sold rows | `open = 0` → skipped entirely; their displayed price stays the true historical price. |
| Cost invariant | `r×(p/m) + r(m−1)×(p/m) = r×p` — exact for 1:1. Commission total unchanged (per-share dilution is the correct economics). |

Known, accepted approximations (documented, not bugs):
- Partially-sold rows display the adjusted price for the whole row while their
  sold portion's allocations show the original price — standard broker-statement
  behaviour ("adjusted cost basis").
- Bonus rows are dated at the ex-date, so hold-time analytics count bonus shares
  from the ex-date, slightly understating true hold. Chosen over corrupting the
  equity replay.
- Non-integer bonus results (e.g. 1:10 on 55 shares) floor per lot. PPSP 1:1 is
  exact; revisit largest-remainder distribution only if a fractional-ratio
  action ever appears on CSX.

## Schema

```sql
CREATE TABLE IF NOT EXISTS corporate_actions (
    action_id VARCHAR(100) PRIMARY KEY,
    market VARCHAR(16) NOT NULL DEFAULT 'CSX',
    symbol VARCHAR(50) NOT NULL,
    action_type VARCHAR(16) NOT NULL,          -- 'bonus' | 'split'
    ratio_new INTEGER NOT NULL,                -- bonus: new shares per held (1:1 => 1)
    ratio_held INTEGER NOT NULL,               --        held shares       (1:1 => 1)
    ex_date DATE NOT NULL,
    note TEXT,
    created_by VARCHAR(100),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TIMESTAMP                       -- NULL until the daemon applies it
);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS corp_action_id VARCHAR(100);
```

Multiplier: bonus `m = 1 + ratio_new/ratio_held`; split `m = ratio_new/ratio_held`
(covers reverse splits). Stored as two ints so the math stays exact.

In `init_db()` as usual (idempotent, applies to prod on first connect).

## Backend pieces

1. **`repositories/corporate_action.py`** — create / list / list_pending
   (`ex_date <= CURRENT_DATE AND applied_at IS NULL`) / mark_applied / delete
   (only while unapplied).
2. **`services/corporate_action_service.py`** — the core:
   - `apply_action(action)`: one DB transaction per user — find that user's BUY
     rows of (market, symbol) with open qty (reuse the matcher's
     open-qty logic), do the split-and-insert above, using `next_seq`.
     Skips users with nothing open. Sets `applied_at` last.
   - **Idempotency**: guarded by `applied_at`; bonus rows carry
     `corp_action_id`, so a crashed half-run can be detected and resumed
     per-user (skip users who already have a row for this action id).
   - **Alerts**: active alerts on the symbol get `target_price ÷= m`, plus a
     Telegram note to the owner ("alert threshold adjusted for the 1:1 bonus").
     New `AlertRepository.rescale_targets(market, symbol, m)`.
   - **Broadcast**: each affected holder with a resolvable chat_id gets:
     "📢 PPSP 1:1 bonus applied — your N shares are now N×2, avg cost adjusted,
     portfolio value unchanged." (via `resolve_chat_id`, same as alerts).
3. **Daemon** — same pattern as `alert_service`/`reminder_scheduler`: checks
   pending actions every 15 min, gated on `TELEGRAM_WEBHOOK_SECRET` (dormant in
   tests/local). Catches up automatically if the backend was asleep on ex-date.
4. **Admin endpoints** (`endpoints/admin.py`):
   - `POST /api/admin/corporate-actions` (validate ratio > 0, ex_date, symbol)
   - `GET  /api/admin/corporate-actions`
   - `DELETE /api/admin/corporate-actions/{id}` — 409 once applied.

## Frontend pieces (campulse-web)

- Admin page: "Corporate actions" card next to the gold board — form
  (market/symbol/type/ratio/ex-date/note) + list with applied status.
- History page: rows with `corp_action_id` show a small "bonus" badge (they
  already carry the note; badge is cosmetic).
- Models + ApiService methods for the three endpoints.

## Telegram

No new commands. The broadcast + alert-rescale notes above are the bot surface.

## Tests (`tests/test_corporate_actions.py`)

All with disposable `pytest_` users and a fake symbol so prod data is never
touched; delete by exact id on teardown.

- Apply math: fully-open lot; partially-sold lot (sold portion's allocations
  untouched, remaining split correctly); multiple lots; multiple users;
  non-holder untouched.
- Invariant: total invested before == after, to the riel.
- Matcher: sell after apply books P/L against adjusted basis; no zero-cost lot
  is ever created.
- Equity continuity: replay a synthetic history across the ex-date — no cliff.
- Alerts: target rescaled; inactive alerts untouched.
- Idempotency: `apply_action` twice → second run is a no-op.
- Endpoints: admin-only, validation, delete-after-apply → 409.
- Floor rounding: 1:10 bonus on 55 shares → 5 (documented behaviour).

## Rollout timeline (ex-date 27 July)

| When | What |
|---|---|
| Now → ~22 July | Build + tests + this repo's full suite green. |
| ~23 July | Push, **manual redeploy**, live-verify: create a throwaway action on a fake symbol for a disposable user via the deployed API, confirm apply + broadcast, clean up by exact id. |
| As announced | Admin (you) enters the real PPSP action with the official ratio + 27 July ex-date. |
| 27 July ~00:15 | Daemon applies it before the market prints adjusted prices. Holders get the Telegram note instead of a fake crash. |

## Out of scope (deliberate)

- Cash dividends (next feature — separate table, simpler math, no deadline).
- Rewriting `price_history` (not needed under the chosen design).
- The CSX feed's own "change %" on ex-date showing a big red number in the
  ticker strip — cosmetic, feed-side, one-day artifact.

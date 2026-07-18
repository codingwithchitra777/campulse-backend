"""Corporate actions: bonus shares & forward splits (see docs/plan-corporate-actions.md).

A bonus/split changes units, not value: for multiplier m, share count × m,
per-share cost ÷ m, **total invested unchanged**. The chosen representation is a
per-lot cost split dated at the ex-date:

  - each BUY row with open qty r > 0 gets its price ÷ m in place;
  - a new BUY row (qty = floor(r × (m−1)), price = old/m, commission 0,
    order_date = ex_date, corp_action_id set) carries the bonus shares.

Why not the two obvious alternatives:
  - a zero-price bonus row: the cheapest-first matcher would consume it first
    and book one giant fake win, then a fake loss;
  - scaling the original rows' qty in place: the equity chart *replays trades by
    date*, so it would show the doubled quantity before the ex-date.

Fully-sold rows have open = 0 and are untouched, so closed history stays true;
already-booked P/L lives in allocations' snapshotted buy_price and is immune.

One daemon thread applies pending actions (ex_date reached, not yet applied),
same pattern as alert_service; dormant unless the Telegram bot is configured.
Each user is one transaction; trades.corp_action_id is the resume guard if a
run dies halfway through the user list.
"""
import time
import uuid
import logging
import threading
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.markets import format_money

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 15 * 60
_started = False
_lock = threading.Lock()

SUPPORTED_TYPES = ("bonus", "split")


def action_multiplier(action: Dict[str, Any]) -> Decimal:
    """bonus 1:1 -> 2 (held + new per held); split 2:1 -> 2 (new per held).
    Only m > 1 is supported — reverse splits would need qty reduction, which is
    a different mechanism (rejected at creation time)."""
    new, held = Decimal(action["ratioNew"]), Decimal(action["ratioHeld"])
    if action["actionType"] == "bonus":
        return (held + new) / held
    return new / held


def describe_ratio(action: Dict[str, Any]) -> str:
    return f"{action['ratioNew']}:{action['ratioHeld']} {action['actionType']}"


class CorporateActionService:
    def __init__(self, action_repo, user_repo, link_repo, alert_repo,
                 send_message=None):
        self.action_repo = action_repo
        self.user_repo = user_repo
        self.link_repo = link_repo
        self.alert_repo = alert_repo
        self.send_message = send_message  # None => no Telegram notes (tests)

    # ---- apply ----

    def apply_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        """Apply one action to every holder's open lots. Idempotent: guarded by
        applied_at at the action level and corp_action_id per user."""
        from app.db.database import get_db

        m = action_multiplier(action)
        if m <= 1:
            raise ValueError(f"unsupported multiplier {m} for action {action['actionId']}")

        market, symbol = action["market"], action["symbol"].upper()
        done_users = self.action_repo.users_already_applied(action["actionId"])

        # Open qty per BUY row, grouped by user — same open-lot definition as the
        # matcher (qty - Σ allocations against the buy).
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.user_id, t.trade_id, t.price, t.qty, t.currency,
                           COALESCE(a.alloc, 0)
                    FROM trades t
                    LEFT JOIN (
                        SELECT buy_trade_id, SUM(qty_allocated) AS alloc
                        FROM allocations GROUP BY buy_trade_id
                    ) a ON a.buy_trade_id = t.trade_id
                    WHERE t.side = 'BUY' AND t.market = %s AND t.ticker = %s
                    ORDER BY t.user_id
                    """,
                    (market, symbol)
                )
                rows = cur.fetchall()

        lots_by_user: Dict[str, List[Dict[str, Any]]] = {}
        for user_id, trade_id, price, qty, currency, alloc in rows:
            open_qty = int(qty) - int(alloc)
            if open_qty <= 0 or user_id in done_users:
                continue
            lots_by_user.setdefault(user_id, []).append({
                "tradeId": trade_id, "price": Decimal(price), "openQty": open_qty,
                "currency": currency,
            })

        summaries = []
        for user_id, lots in lots_by_user.items():
            summary = self._apply_for_user(action, user_id, lots, m)
            if summary:
                summaries.append(summary)

        rescaled = self.alert_repo.rescale_targets(market, symbol, m)
        self.action_repo.mark_applied(action["actionId"])

        self._notify(action, summaries, rescaled)
        logger.info(f"Corporate action {describe_ratio(action)} {symbol}: "
                    f"adjusted {len(summaries)} holder(s), rescaled {len(rescaled)} alert(s).")
        return {"holders": len(summaries), "alertsRescaled": len(rescaled)}

    def _apply_for_user(self, action, user_id, lots, m: Decimal) -> Optional[Dict[str, Any]]:
        """Price ÷ m on each open lot + one bonus row per lot — atomically for
        this user, so a crash leaves them either fully adjusted or untouched."""
        from app.db.database import get_db

        note = f"{action['symbol']} {describe_ratio(action)} bonus shares"
        added = 0
        held = sum(l["openQty"] for l in lots)
        currency = lots[0]["currency"]
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq), 0) FROM trades WHERE user_id = %s", (user_id,))
                seq = int(cur.fetchone()[0])
                for lot in lots:
                    new_price = (lot["price"] / m).quantize(Decimal("0.0001"))
                    bonus_qty = int(Decimal(lot["openQty"]) * (m - 1))  # floor; exact for 1:1
                    cur.execute("UPDATE trades SET price = %s WHERE trade_id = %s",
                                (new_price, lot["tradeId"]))
                    if bonus_qty <= 0:
                        continue
                    seq += 1
                    cur.execute(
                        """
                        INSERT INTO trades (trade_id, user_id, seq, ticker, side, price, qty,
                                            commission, order_date, market, currency, note, corp_action_id)
                        VALUES (%s, %s, %s, %s, 'BUY', %s, %s, 0, %s, %s, %s, %s, %s)
                        """,
                        (str(uuid.uuid4()), user_id, seq, action["symbol"].upper(), new_price,
                         bonus_qty, action["exDate"], action["market"], currency, note,
                         action["actionId"])
                    )
                    added += bonus_qty
        return {"userId": user_id, "held": held, "added": added, "currency": currency}

    # ---- notifications ----

    def _notify(self, action, summaries, rescaled) -> None:
        if not self.send_message:
            return
        from app.services.alert_service import resolve_chat_id
        symbol, ratio = action["symbol"].upper(), describe_ratio(action)
        for s in summaries:
            chat_id = resolve_chat_id(s["userId"], self.user_repo, self.link_repo)
            if chat_id is None:
                continue
            try:
                self.send_message(chat_id, (
                    f"📢 {symbol} {ratio} applied.\n"
                    f"Your {s['held']:,} shares are now {s['held'] + s['added']:,} — "
                    f"average cost adjusted, portfolio value unchanged."))
            except Exception as e:
                logger.error(f"Corp-action note failed for {s['userId']}: {e}")
        for a in rescaled:
            chat_id = resolve_chat_id(a["userId"], self.user_repo, self.link_repo)
            if chat_id is None:
                continue
            try:
                self.send_message(chat_id, (
                    f"🔔 Your {symbol} alert was adjusted for the {ratio}: "
                    f"{format_money(a['oldTarget'], a['currency'])} → "
                    f"{format_money(a['newTarget'], a['currency'])}."))
            except Exception as e:
                logger.error(f"Alert-rescale note failed for {a['userId']}: {e}")

    # ---- daemon ----

    def check_once(self) -> int:
        applied = 0
        for action in self.action_repo.list_pending():
            try:
                self.apply_action(action)
                applied += 1
            except Exception as e:
                logger.error(f"Applying corporate action {action['actionId']} failed: {e}",
                             exc_info=True)
        return applied


def _loop():
    from app.repositories.corporate_action import CorporateActionRepository
    from app.repositories.user import UserRepository
    from app.repositories.link import LinkRepository
    from app.repositories.alert import AlertRepository
    from app.services import telegram_client

    svc = CorporateActionService(CorporateActionRepository(), UserRepository(),
                                 LinkRepository(), AlertRepository(),
                                 telegram_client.send_message)
    logger.info("Corporate-action scheduler started.")
    while True:
        try:
            n = svc.check_once()
            if n:
                logger.info(f"Applied {n} corporate action(s).")
        except Exception as e:
            logger.error(f"Corporate-action check failed: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_corporate_action_scheduler():
    """Idempotent daemon start; dormant unless the Telegram bot is configured
    (same gate as the other schedulers, so tests/local never touch prod data)."""
    global _started
    if not settings.telegram_webhook_secret:
        return
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True).start()
        _started = True

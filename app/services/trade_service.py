"""Shared trade-recording logic used by both the REST endpoint (POST /trades)
and the Telegram webhook bot (/buy, /sell), so web and Telegram produce
identical trades. Raises ValueError on invalid input — callers translate that
to their own error surface (HTTP 400 / a Telegram reply)."""
import uuid
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from app.services.lifo_matcher import LifoMatcherService

logger = logging.getLogger(__name__)

COMMISSION_RATE = 0.0047


def record_trade(
    trade_repo,
    alloc_repo,
    user_id: str,
    ticker: str,
    side: str,
    price: int,
    qty: int,
    commission: Optional[int] = None,
    order_date: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Persist a trade (BUY or SELL) and, for SELL, run LIFO matching.
    Returns raw (unserialized) dicts: {trade, allocations, realisedPnl, warning}."""
    side = side.upper()
    ticker = ticker.upper()

    if side not in ("BUY", "SELL"):
        raise ValueError("Side must be BUY or SELL")
    if price <= 0 or qty <= 0:
        raise ValueError("Price and Quantity must be positive")

    if commission is None:
        commission = int(price * qty * COMMISSION_RATE)
    order_date = order_date or datetime.utcnow()
    # Allow the rest of "today" so a date-only input for today never rejects.
    if order_date.date() > datetime.utcnow().date():
        raise ValueError("Order date cannot be in the future")

    trade = {
        "tradeId": str(uuid.uuid4()),
        "userId": user_id,
        "seq": trade_repo.next_seq(user_id),
        "ticker": ticker,
        "side": side,
        "price": price,
        "qty": qty,
        "commission": commission,
        "orderDate": order_date,
    }
    trade_repo.add_trade(trade)

    allocations = []
    realised_pnl = 0
    warning = None
    if side == "SELL":
        try:
            allocations = LifoMatcherService(trade_repo, alloc_repo).match_sell_lifo(trade)
            realised_pnl = sum(int(a.get("realisedPnl", 0)) for a in allocations)
        except Exception as e:
            logger.warning(f"LIFO Matching warning: {e}")
            warning = f"LIFO Match failed: {e}"

    return {"trade": trade, "allocations": allocations, "realisedPnl": realised_pnl, "warning": warning}

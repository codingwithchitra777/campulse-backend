"""The free coach: turns a snapshot into plain sentences with no model call.

Everything here is already in the user's own numbers — AnalyticsService computed
it — so this costs nothing, never fails, and needs no API key. The AI coach
(ai_coach.py) is the optional deeper pass on the same snapshot; both read the
identical `build_snapshot()` output, so neither can drift from the other.

Same rule as the AI coach: **descriptive, never advisory**. State what the
trader's own history shows and stop. No "buy", no "sell", no targets.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.services.ai_coach import DISCLAIMER, thin_data_message  # noqa: F401  (re-exported)
from app.services.markets import format_money

# A tag needs this many closed trades before its win rate means anything.
MIN_TAG_TRADES = 3
# Below this gap, two tags are the same tag as far as we're concerned.
TAG_GAP_PCT = 15.0

MARKET_NAMES = {"CSX": "the Cambodian market", "US": "US stocks", "GOLD_KH": "local gold"}


def _money(value, currency: str) -> str:
    return format_money(Decimal(str(value or 0)), currency or "KHR")


def _headline(s: Dict[str, Any]) -> str:
    n, w, l = s["closedTradeCount"], s["wins"], s["losses"]
    return (f"You've closed {n} trades so far — {w} winners and {l} losers, "
            f"a {s['winRatePct']}% win rate.")


def _tag_gap(s: Dict[str, Any]) -> Optional[str]:
    """The most useful line we can produce: the trader's own tags, ranked."""
    tags = [t for t in s.get("byTag", []) if t["trades"] >= MIN_TAG_TRADES]
    if len(tags) < 2:
        return None
    best = max(tags, key=lambda t: t["winRatePct"])
    worst = min(tags, key=lambda t: t["winRatePct"])
    if best["tag"] == worst["tag"] or best["winRatePct"] - worst["winRatePct"] < TAG_GAP_PCT:
        return None
    return (f"Your biggest split is by journal tag: #{best['tag']} wins "
            f"{best['winRatePct']}% of the time across {best['trades']} trades, "
            f"while #{worst['tag']} wins just {worst['winRatePct']}% across "
            f"{worst['trades']}. That's the clearest pattern in your journal.")


def _hold(s: Dict[str, Any]) -> Optional[str]:
    d = s.get("avgHoldDays", 0.0)
    if d <= 0:
        return None
    return f"You hold a position for {d} days on average, weighted by size."


def _extremes(s: Dict[str, Any]) -> Optional[str]:
    best, worst = s.get("bestTrade"), s.get("worstTrade")
    if not best or not worst:
        return None
    if best["ticker"] == worst["ticker"] and best["realisedPnl"] == worst["realisedPnl"]:
        return None
    return (f"Your best close was {best['ticker']} at "
            f"{_money(best['realisedPnl'], best['currency'])}; your worst was "
            f"{worst['ticker']} at {_money(worst['realisedPnl'], worst['currency'])}.")


def _currencies(s: Dict[str, Any]) -> List[str]:
    """Per currency, never blended — KHR and USD are not addable."""
    out = []
    for g in s.get("byCurrency", []):
        c = g["currency"]
        realised, unrealised = g["realisedPnl"], g["unrealisedPnl"]
        verb = "made" if realised >= 0 else "lost"
        line = f"In {c} you've {verb} {_money(abs(realised), c)} realised"
        if unrealised:
            side = "up" if unrealised >= 0 else "down"
            line += f", and you're {side} {_money(abs(unrealised), c)} on open positions"
        out.append(line + ".")
    return out


def _markets(s: Dict[str, Any]) -> Optional[str]:
    ms = [m for m in s.get("byMarket", []) if m.get("positions")]
    if len(ms) < 2:
        return None
    top = max(ms, key=lambda m: m["positions"])
    total = sum(m["positions"] for m in ms)
    name = MARKET_NAMES.get(top["market"], top["market"])
    return (f"{top['positions']} of your {total} open positions are in {name}.")


def build_insight(snapshot: Dict[str, Any]) -> str:
    """A readable paragraph, or a thin-data note when there's nothing to say."""
    thin = thin_data_message(snapshot)
    if thin:
        return thin

    parts = [_headline(snapshot), _tag_gap(snapshot), _hold(snapshot),
             _extremes(snapshot), *_currencies(snapshot), _markets(snapshot)]
    return " ".join(p for p in parts if p)

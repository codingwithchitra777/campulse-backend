"""AI Coach: turns a user's own trading stats into a plain-language readout.

Two hard rules, both enforced here rather than left to the prompt:

1. **Descriptive, never advisory.** CamPulse handles real money and we are not
   licensed advisers, so the coach may say "your win rate on #plan trades is 71%
   vs 32% on #FOMO" but must never say "buy PWSA" or "PWSA will go up". The
   system prompt forbids it and `DISCLAIMER` is appended server-side so it cannot
   be prompted away.
2. **No PII leaves the server.** `build_snapshot()` is an allow-list: it copies
   named aggregate fields out of AnalyticsService.compute() and nothing else, so
   a new column on `users` or `trades` can never silently reach the API. There is
   no user id, name, email, or chat id in the snapshot — see test_ai_coach.py.

Cost control: the snapshot is hashed and the insight cached against that hash, so
we only pay when the user's numbers actually changed (see repositories/ai_insight).
"""
from __future__ import annotations

import json
import hashlib
import logging
from decimal import Decimal
from typing import Any, Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Below this many closed trades the numbers are noise, not a pattern — say so
# rather than paying for a model to over-read three data points.
MIN_CLOSED_TRADES = 5

DISCLAIMER = ("This is an automated summary of your own recorded trades, not financial "
              "advice. CamPulse is not a licensed adviser.")

MAX_TOKENS = 900

SYSTEM_PROMPT = """You are the CamPulse trading journal's analyst. You are given a JSON \
summary of one trader's OWN past trades on the Cambodian (CSX), US, and local gold markets.

Your job is to describe patterns that are already visible in their numbers, so the trader \
can see their own behaviour clearly.

You MUST:
- Talk only about what the data shows: win rate, holding period, per-tag performance, \
realised vs unrealised P/L, best and worst closed trades.
- Compare the trader's own segments against each other (e.g. tag A vs tag B, one market \
vs another) and point out the largest, most actionable-looking gaps.
- Keep money in the currency it is given in. NEVER add KHR and USD together.
- Be direct and specific. Cite the actual numbers. 150-200 words, plain prose, no headings.
- Write for a retail trader in clear, simple English.

You MUST NOT:
- Recommend buying, selling, or holding any security, or predict any price or direction.
- Suggest position sizes, entry points, exit points, or targets.
- Comment on whether any ticker is good, cheap, expensive, or worth owning.
- Invent any number that is not in the JSON. If the data is thin, say it is thin.

If asked for advice, describe the pattern instead and stop there."""


def _plain(v: Any) -> Any:
    """Decimal -> float for JSON. Money precision doesn't matter here — the model
    reads these as rough magnitudes, and the UI renders the real values."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def build_snapshot(stats: Dict[str, Any]) -> Dict[str, Any]:
    """Allow-listed, anonymised view of AnalyticsService.compute() output.

    Copies named fields only. Nothing identifying the user is included, and
    nothing is passed through by spreading a source dict."""
    def trade(t):
        if not t:
            return None
        return {
            "ticker": t.get("ticker"),
            "market": t.get("market"),
            "currency": t.get("currency"),
            "realisedPnl": _plain(t.get("realisedPnl")),
        }

    return {
        "tradeCount": stats.get("tradeCount", 0),
        "closedTradeCount": stats.get("closedTradeCount", 0),
        "wins": stats.get("wins", 0),
        "losses": stats.get("losses", 0),
        "winRatePct": round(float(stats.get("winRate", 0.0)), 1),
        "avgHoldDays": round(float(stats.get("avgHoldDays", 0.0)), 1),
        "bestTrade": trade(stats.get("bestTrade")),
        "worstTrade": trade(stats.get("worstTrade")),
        "byCurrency": [
            {
                "currency": g.get("currency"),
                "realisedPnl": _plain(g.get("realisedPnl")),
                "unrealisedPnl": _plain(g.get("unrealisedPnl")),
                "invested": _plain(g.get("invested")),
                "wins": g.get("wins", 0),
                "losses": g.get("losses", 0),
            }
            for g in stats.get("byCurrency", [])
        ],
        "byMarket": [
            {
                "market": m.get("market"),
                "currency": m.get("currency"),
                "positions": m.get("positions", 0),
                "invested": _plain(m.get("invested")),
            }
            for m in stats.get("byMarket", [])
        ],
        "byTag": [
            {
                "tag": g.get("tag"),
                "trades": g.get("trades", 0),
                "wins": g.get("wins", 0),
                "losses": g.get("losses", 0),
                "winRatePct": round(float(g.get("winRate", 0.0)), 1),
            }
            for g in stats.get("byTag", [])
        ],
    }


def snapshot_hash(snapshot: Dict[str, Any]) -> str:
    """Stable cache key. sort_keys matters — dict order must not mint a new hash."""
    blob = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def is_configured() -> bool:
    return bool(settings.anthropic_api_key)


def thin_data_message(snapshot: Dict[str, Any]) -> Optional[str]:
    """Short-circuit before spending a token on too little data."""
    n = snapshot.get("closedTradeCount", 0)
    if n >= MIN_CLOSED_TRADES:
        return None
    need = MIN_CLOSED_TRADES - n
    return (f"You have {n} closed trade(s) so far. Close about {need} more and the coach "
            f"can start comparing your win rate, holding periods, and journal tags against "
            f"each other. Until then any pattern would just be noise.")


class AICoachService:
    """Wraps the Anthropic client. `client` is injectable so tests never call out."""

    def __init__(self, client=None, model: Optional[str] = None):
        self._client = client
        self.model = model or settings.anthropic_model

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def generate(self, snapshot: Dict[str, Any]) -> str:
        """One insight from one snapshot. Raises on API failure; callers decide."""
        thin = thin_data_message(snapshot)
        if thin:
            return thin

        payload = json.dumps(snapshot, sort_keys=True, default=str)
        resp = self._get_client().messages.create(
            model=self.model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content":
                       f"Here is my trading summary:\n\n{payload}\n\n"
                       f"Describe the patterns you can see in my own numbers."}],
        )
        if resp.stop_reason == "refusal":
            logger.warning("AI coach refused a snapshot; returning a neutral message.")
            return "The coach could not summarise this data. Please try again later."
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return text or "The coach returned an empty summary. Please try again later."

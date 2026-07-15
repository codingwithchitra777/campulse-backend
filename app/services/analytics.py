"""Portfolio analytics computed purely from the user's own trades/allocations
(no external calls). Money is kept per-currency — never blended across KHR/USD.
Descriptive stats only (win rate, hold time, best/worst); no trade recommendations."""
from __future__ import annotations
from typing import Dict, Any, Optional
from collections import defaultdict
from decimal import Decimal


def _new_currency_bucket():
    return {
        "currency": None,
        "realisedPnl": Decimal(0),
        "unrealisedPnl": Decimal(0),
        "invested": Decimal(0),
        "value": Decimal(0),
        "wins": 0,
        "losses": 0,
    }


class AnalyticsService:
    def __init__(self, trade_repo, alloc_repo, portfolio_service):
        self.trade_repo = trade_repo
        self.alloc_repo = alloc_repo
        self.portfolio = portfolio_service

    def compute(self, user_id: str) -> Dict[str, Any]:
        trades = self.trade_repo.list_trades(user_id)
        allocs = self.alloc_repo.list_allocations(user_id)
        trade_map = {t["tradeId"]: t for t in trades}

        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]

        # Realised P/L per SELL trade = the "closed trade" outcome.
        realised_by_sell = defaultdict(lambda: Decimal(0))
        for a in allocs:
            realised_by_sell[a["sellTradeId"]] += Decimal(a["realisedPnl"])
        closed = [(trade_map[sid], pnl) for sid, pnl in realised_by_sell.items() if sid in trade_map]

        wins = sum(1 for _, p in closed if p > 0)
        losses = sum(1 for _, p in closed if p < 0)
        closed_count = len(closed)
        win_rate = (wins / closed_count * 100.0) if closed_count else 0.0

        # Average holding period (days), weighted by allocated quantity.
        total_days = Decimal(0)
        total_qty = Decimal(0)
        for a in allocs:
            b = trade_map.get(a["buyTradeId"])
            s = trade_map.get(a["sellTradeId"])
            if b and s:
                days = (s["orderDate"].date() - b["orderDate"].date()).days
                q = Decimal(a["qtyAllocated"])
                total_days += Decimal(days) * q
                total_qty += q
        avg_hold_days = float(total_days / total_qty) if total_qty else 0.0

        best = max(closed, key=lambda x: x[1]) if closed else None
        worst = min(closed, key=lambda x: x[1]) if closed else None

        def summarize(entry):
            if not entry:
                return None
            t, p = entry
            return {
                "ticker": t["ticker"],
                "market": t.get("market", "CSX"),
                "currency": t.get("currency", "KHR"),
                "realisedPnl": p,
                "sellDate": t["orderDate"].isoformat(),
            }

        # Per-currency roll-up (realised from allocs; live figures from the portfolio).
        cur: Dict[str, Any] = defaultdict(_new_currency_bucket)
        for a in allocs:
            c = a.get("currency", "KHR")
            cur[c]["currency"] = c
            cur[c]["realisedPnl"] += Decimal(a["realisedPnl"])
        for t, p in closed:
            c = t.get("currency", "KHR")
            cur[c]["currency"] = c
            if p > 0:
                cur[c]["wins"] += 1
            elif p < 0:
                cur[c]["losses"] += 1

        portfolio = self.portfolio.portfolio(user_id)
        by_market: Dict[str, Any] = defaultdict(lambda: {"market": None, "currency": "KHR", "positions": 0, "invested": Decimal(0)})
        for h in portfolio:
            c = h.get("currency", "KHR")
            cur[c]["currency"] = c
            cur[c]["unrealisedPnl"] += Decimal(h["unrealisedPnl"])
            if h["remainingQty"] > 0:
                if h["lastPrice"] is not None:
                    cur[c]["value"] += Decimal(h["lastPrice"]) * Decimal(h["remainingQty"])
                if h["avgCostRemaining"] is not None:
                    cur[c]["invested"] += Decimal(h["avgCostRemaining"]) * Decimal(h["remainingQty"])
                m = h.get("market", "CSX")
                by_market[m]["market"] = m
                by_market[m]["currency"] = c
                by_market[m]["positions"] += 1
                if h["avgCostRemaining"] is not None:
                    by_market[m]["invested"] += Decimal(h["avgCostRemaining"]) * Decimal(h["remainingQty"])

        by_currency = sorted(cur.values(), key=lambda g: (g["currency"] != "KHR", g["currency"] or ""))

        # Performance by journal tag: credit each closed trade's win/loss to the
        # tags on its SELL and on the BUY lots it consumed, so tagging either the
        # entry rationale or the exit surfaces the pattern.
        buys_by_sell = defaultdict(set)
        for a in allocs:
            buys_by_sell[a["sellTradeId"]].add(a["buyTradeId"])

        def _tags(t):
            return {s.strip().lower() for s in (t.get("tags") or "").split(",") if s.strip()}

        tag_stats: Dict[str, Any] = defaultdict(lambda: {"tag": None, "trades": 0, "wins": 0, "losses": 0})
        for sell, pnl in closed:
            if pnl == 0:
                continue
            tagset = set(_tags(sell))
            for bid in buys_by_sell.get(sell["tradeId"], ()):
                b = trade_map.get(bid)
                if b:
                    tagset |= _tags(b)
            for tag in tagset:
                g = tag_stats[tag]
                g["tag"] = tag
                g["trades"] += 1
                if pnl > 0:
                    g["wins"] += 1
                else:
                    g["losses"] += 1

        by_tag = []
        for g in tag_stats.values():
            total = g["wins"] + g["losses"]
            by_tag.append({**g, "winRate": (g["wins"] / total * 100.0) if total else 0.0})
        by_tag.sort(key=lambda x: (-x["trades"], x["tag"]))

        return {
            "tradeCount": len(trades),
            "buyCount": len(buys),
            "sellCount": len(sells),
            "closedTradeCount": closed_count,
            "wins": wins,
            "losses": losses,
            "winRate": win_rate,
            "avgHoldDays": avg_hold_days,
            "bestTrade": summarize(best),
            "worstTrade": summarize(worst),
            "byCurrency": by_currency,
            "byMarket": list(by_market.values()),
            "byTag": by_tag,
        }

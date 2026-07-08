from __future__ import annotations
from typing import Dict, Any, List, Optional
from collections import defaultdict

from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.services.pricing import PricingService

class PortfolioService:
    def __init__(self, trade_repo: TradeRepository, alloc_repo: AllocationRepository, pricing: PricingService):
        self.trade_repo = trade_repo
        self.alloc_repo = alloc_repo
        self.pricing = pricing

    def _ticker_set(self, user_id: str) -> List[str]:
        trades = self.trade_repo.list_trades(user_id)
        return sorted({t["ticker"] for t in trades})

    def position_detail(self, user_id: str, ticker: str) -> Dict[str, Any]:
        trades = self.trade_repo.list_trades(user_id, ticker)
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]

        total_bought = sum(int(t["qty"]) for t in buys)
        total_sold = sum(int(t["qty"]) for t in sells)
        remaining = total_bought - total_sold

        # Remaining lots (BUY lots minus allocated qty)
        allocs = self.alloc_repo.list_allocations(user_id, ticker)
        alloc_by_buy = defaultdict(int)
        for a in allocs:
            alloc_by_buy[a["buyTradeId"]] += int(a["qtyAllocated"])

        remaining_lots = []
        for b in buys:
            open_qty = int(b["qty"]) - alloc_by_buy.get(b["tradeId"], 0)
            remaining_lots.append({
                "seq": b["seq"],
                "tradeId": b["tradeId"],
                "price": int(b["price"]),
                "qtyOriginal": int(b["qty"]),
                "qtyOpen": max(0, open_qty),
                "commission": int(b.get("commission", 0)),
                "orderDate": b["orderDate"],
            })
        
        # Sort by sequence number descending (newest first) to show LIFO order
        remaining_lots.sort(key=lambda x: x["seq"], reverse=True)

        sold_pct = 0.0 if total_bought == 0 else (total_sold / total_bought) * 100.0

        return {
            "ticker": ticker,
            "totalBoughtQty": total_bought,
            "totalSoldQty": total_sold,
            "remainingQty": remaining,
            "soldPercent": sold_pct,
            "remainingLots": remaining_lots,
        }

    def realised_pnl(self, user_id: str, ticker: Optional[str] = None) -> int:
        allocs = self.alloc_repo.list_allocations(user_id, ticker)
        return sum(int(a["realisedPnl"]) for a in allocs)

    def unrealised_pnl(self, user_id: str, ticker: str, current_price: int) -> Dict[str, Any]:
        pos = self.position_detail(user_id, ticker)
        remaining_lots = pos["remainingLots"]

        total_qty = sum(l["qtyOpen"] for l in remaining_lots)
        if total_qty <= 0:
            return {"ticker": ticker, "remainingQty": 0, "avgCost": None, "unrealisedPnl": 0}

        total_cost = 0.0
        for l in remaining_lots:
            buy_qty = l["qtyOpen"]
            unit_cost = float(l["price"])
            total_cost += buy_qty * unit_cost

        avg_cost = total_cost / total_qty
        unrealised = int(round(total_qty * (float(current_price) - avg_cost)))

        return {"ticker": ticker, "remainingQty": total_qty, "avgCost": avg_cost, "unrealisedPnl": unrealised}

    def portfolio(self, user_id: str) -> List[Dict[str, Any]]:
        result = []
        for ticker in self._ticker_set(user_id):
            price_res = self.pricing.get_latest_price(ticker)
            last_price = int(price_res.price) if price_res.price is not None else None

            pos = self.position_detail(user_id, ticker)
            realised = self.realised_pnl(user_id, ticker)

            unrealised = 0
            avg_cost = None
            unrealised_pct = 0.0
            if last_price is not None and pos["remainingQty"] > 0:
                u = self.unrealised_pnl(user_id, ticker, last_price)
                unrealised = u["unrealisedPnl"]
                avg_cost = u["avgCost"]
                if avg_cost and avg_cost > 0:
                    unrealised_pct = ((last_price - avg_cost) / avg_cost) * 100.0

            # Calculate total bought cost for total PnL % calculation
            trades = self.trade_repo.list_trades(user_id, ticker)
            buys = [t for t in trades if t["side"] == "BUY"]
            total_bought_cost = sum(int(t["qty"]) * int(t["price"]) for t in buys)

            total_pnl = realised + unrealised
            total_pnl_pct = 0.0
            if total_bought_cost > 0:
                total_pnl_pct = (total_pnl / total_bought_cost) * 100.0

            result.append({
                "ticker": ticker,
                "lastPrice": last_price,
                "remainingQty": pos["remainingQty"],
                "soldPercent": pos["soldPercent"],
                "avgCostRemaining": avg_cost,
                "realisedPnl": realised,
                "unrealisedPnl": unrealised,
                "unrealisedPnlPercent": unrealised_pct,
                "totalPnl": total_pnl,
                "totalPnlPercent": total_pnl_pct,
            })
        return result

    def top_profitable_tickers(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        allocs = self.alloc_repo.list_allocations(user_id)
        pnl_by_ticker = defaultdict(int)
        for a in allocs:
            pnl_by_ticker[a["ticker"]] += int(a["realisedPnl"])
        ranked = sorted(pnl_by_ticker.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"ticker": t, "realisedPnl": p} for t, p in ranked]

    def top_profitable_buy_orders(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        allocs = self.alloc_repo.list_allocations(user_id)
        pnl_by_buy = defaultdict(int)
        for a in allocs:
            pnl_by_buy[a["buyTradeId"]] += int(a["realisedPnl"])

        trades = self.trade_repo.list_trades(user_id)
        trade_map = {t["tradeId"]: t for t in trades}

        ranked = sorted(pnl_by_buy.items(), key=lambda x: x[1], reverse=True)[:limit]
        out = []
        for buy_id, pnl in ranked:
            t = trade_map.get(buy_id, {})
            out.append({
                "buyTradeId": buy_id,
                "seq": t.get("seq"),
                "ticker": t.get("ticker"),
                "buyPrice": t.get("price"),
                "realisedPnl": pnl
            })
        return out

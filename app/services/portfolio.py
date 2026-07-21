from __future__ import annotations
from typing import Dict, Any, List, Optional
from collections import defaultdict
from decimal import Decimal

from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.repositories.price_history import PriceHistoryRepository
from app.services.pricing import PricingService
from app.services.price_providers import PriceRouter, CSXProvider
from app.services import markets

class PortfolioService:
    def __init__(self, trade_repo: TradeRepository, alloc_repo: AllocationRepository, pricing: PricingService,
                 price_history_repo: Optional[PriceHistoryRepository] = None,
                 price_router: Optional[PriceRouter] = None):
        self.trade_repo = trade_repo
        self.alloc_repo = alloc_repo
        self.pricing = pricing
        self.price_history = price_history_repo or PriceHistoryRepository()
        # Market-aware price lookups; defaults to a CSX-only router built from the
        # injected pricing service, so existing (CSX) callers behave identically.
        self.price_router = price_router or PriceRouter({markets.CSX: CSXProvider(pricing)})

    def _ticker_set(self, user_id: str) -> List[str]:
        trades = self.trade_repo.list_trades(user_id)
        return sorted({t["ticker"] for t in trades})

    def position_detail(self, user_id: str, ticker: str, market: Optional[str] = None) -> Dict[str, Any]:
        trades = self.trade_repo.list_trades(user_id, ticker, market=market)
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]

        total_bought = sum(int(t["qty"]) for t in buys)
        total_sold = sum(int(t["qty"]) for t in sells)
        remaining = total_bought - total_sold

        # Remaining lots (BUY lots minus allocated qty)
        allocs = self.alloc_repo.list_allocations(user_id, ticker, market=market)
        alloc_by_buy = defaultdict(int)
        for a in allocs:
            alloc_by_buy[a["buyTradeId"]] += int(a["qtyAllocated"])

        remaining_lots = []
        for b in buys:
            open_qty = int(b["qty"]) - alloc_by_buy.get(b["tradeId"], 0)
            remaining_lots.append({
                "seq": b["seq"],
                "tradeId": b["tradeId"],
                "price": Decimal(b["price"]),
                "qtyOriginal": int(b["qty"]),
                "qtyOpen": max(0, open_qty),
                "commission": Decimal(b.get("commission", 0) or 0),
                "orderDate": b["orderDate"],
            })

        # Cheapest lots first — the order the best-profit matcher consumes
        # them, so position displays read as the sell queue.
        remaining_lots.sort(key=lambda x: (x["price"], x["seq"]))

        sold_pct = 0.0 if total_bought == 0 else (total_sold / total_bought) * 100.0

        return {
            "ticker": ticker,
            "totalBoughtQty": total_bought,
            "totalSoldQty": total_sold,
            "remainingQty": remaining,
            "soldPercent": sold_pct,
            "remainingLots": remaining_lots,
        }

    def realised_pnl(self, user_id: str, ticker: Optional[str] = None, market: Optional[str] = None) -> Decimal:
        allocs = self.alloc_repo.list_allocations(user_id, ticker, market=market)
        return sum((Decimal(a["realisedPnl"]) for a in allocs), Decimal(0))

    def unrealised_pnl(self, user_id: str, ticker: str, current_price, market: Optional[str] = None,
                       currency: str = "KHR") -> Dict[str, Any]:
        pos = self.position_detail(user_id, ticker, market=market)
        remaining_lots = pos["remainingLots"]

        total_qty = sum(l["qtyOpen"] for l in remaining_lots)
        if total_qty <= 0:
            return {"ticker": ticker, "remainingQty": 0, "avgCost": None, "unrealisedPnl": Decimal(0)}

        total_cost = Decimal(0)
        for l in remaining_lots:
            total_cost += Decimal(l["qtyOpen"]) * Decimal(l["price"])

        avg_cost = total_cost / Decimal(total_qty)
        unrealised = markets.quantize_money(Decimal(total_qty) * (Decimal(current_price) - avg_cost), currency)

        return {"ticker": ticker, "remainingQty": total_qty, "avgCost": avg_cost, "unrealisedPnl": unrealised}

    def portfolio(self, user_id: str, valuation_mode: str = "BID") -> List[Dict[str, Any]]:
        all_trades = self.trade_repo.list_trades(user_id)
        # First trade of each ticker fixes its market/currency (one ticker == one market).
        meta: Dict[str, tuple] = {}
        for t in all_trades:
            meta.setdefault(t["ticker"], (t.get("market", "CSX"), t.get("currency", "KHR")))

        result = []
        for ticker in sorted(meta):
            market, currency = meta[ticker]
            price_res = self.price_router.get_latest_price(market, ticker)
            
            last_price = None
            if price_res.price is not None:
                if valuation_mode == "ASK":
                    ask = price_res.raw.get("askPrice") if price_res.raw else None
                    last_price = Decimal(str(ask)) if ask is not None else Decimal(str(price_res.price))
                else:
                    bid = price_res.raw.get("bidPrice") if price_res.raw else None
                    last_price = Decimal(str(bid)) if bid is not None else Decimal(str(price_res.price))

            pos = self.position_detail(user_id, ticker, market=market)
            realised = self.realised_pnl(user_id, ticker, market=market)

            unrealised = Decimal(0)
            avg_cost = None
            unrealised_pct = 0.0
            if last_price is not None and pos["remainingQty"] > 0:
                u = self.unrealised_pnl(user_id, ticker, last_price, market=market, currency=currency)
                unrealised = u["unrealisedPnl"]
                avg_cost = u["avgCost"]
                if avg_cost and avg_cost > 0:
                    unrealised_pct = float((last_price - avg_cost) / avg_cost) * 100.0

            # Calculate total bought cost for total PnL % calculation
            buys = [t for t in all_trades if t["ticker"] == ticker and t["side"] == "BUY"]
            total_bought_cost = sum((Decimal(t["price"]) * int(t["qty"]) for t in buys), Decimal(0))

            total_pnl = realised + unrealised
            total_pnl_pct = 0.0
            if total_bought_cost > 0:
                total_pnl_pct = float(total_pnl) / float(total_bought_cost) * 100.0

            result.append({
                "ticker": ticker,
                "market": market,
                "currency": currency,
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

    def realised_pnl_by_year(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self.alloc_repo.realised_pnl_by_year(user_id)
        years: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            y = years.setdefault(r["year"], {
                "year": r["year"],
                "realisedPnl": 0,
                "sellCount": 0,
                "tickers": []
            })
            y["realisedPnl"] += r["realisedPnl"]
            # A sell trade has exactly one ticker, so per-ticker counts sum cleanly.
            y["sellCount"] += r["sellCount"]
            y["tickers"].append({
                "ticker": r["ticker"],
                "realisedPnl": r["realisedPnl"],
                "sellCount": r["sellCount"]
            })
        out = sorted(years.values(), key=lambda x: x["year"], reverse=True)
        for y in out:
            y["tickers"].sort(key=lambda t: t["realisedPnl"], reverse=True)
        return out

    def chart_timeline(self, user_id: str, market: Optional[str] = None, target_currency: str = "KHR", rate: Optional[Dict[str, Any]] = None, valuation_mode: str = "BID") -> Dict[str, Any]:
        """Per-day cumulative series for the Reports charts: money invested vs
        recovered (from the trade ledger) and running realized P/L (allocations
        keyed to the SELL trade's order date, like realised_pnl_by_year)."""
        trades = self.trade_repo.list_trades(user_id, market=market)

        def convert_val(amount, trade_currency):
            if not amount or trade_currency == target_currency:
                return amount
            if not rate:
                return amount
            
            # rate is always USD base, KHR target
            # if we have USD and want KHR -> sell USD -> bid_rate
            if trade_currency == 'USD' and target_currency == 'KHR':
                return amount * Decimal(rate['bidRate'])
            # if we have KHR and want USD -> buy USD -> ask_rate
            if trade_currency == 'KHR' and target_currency == 'USD':
                return amount / Decimal(rate['askRate'])
            return amount

        investment: List[Dict[str, Any]] = []
        invested = Decimal(0)
        recovered = Decimal(0)
        for t in trades:
            t_curr = t.get("currency", "KHR")
            val = Decimal(t["price"]) * Decimal(t["qty"])
            comm = Decimal(t.get("commission", 0) or 0)
            
            if t["side"] == "BUY":
                invested += convert_val(val + comm, t_curr)
            else:
                recovered += convert_val(val - comm, t_curr)
                
            day = t["orderDate"].date().isoformat()
            if investment and investment[-1]["date"] == day:
                investment[-1]["invested"] = float(invested)
                investment[-1]["recovered"] = float(recovered)
            else:
                investment.append({"date": day, "invested": float(invested), "recovered": float(recovered)})

        order_date_by_trade = {t["tradeId"]: (t["orderDate"], t.get("currency", "KHR")) for t in trades}
        allocs = self.alloc_repo.list_allocations(user_id, market=market)
        
        events = []
        for a in allocs:
            order_info = order_date_by_trade.get(a["sellTradeId"])
            if order_info:
                dt, curr = order_info
                events.append((dt, convert_val(Decimal(a["realisedPnl"]), curr)))
            else:
                # Fallback if somehow not in trades
                events.append((a["createdAt"], convert_val(Decimal(a["realisedPnl"]), "KHR")))

        events.sort(key=lambda x: x[0])

        pnl: List[Dict[str, Any]] = []
        cumulative = Decimal(0)
        for when, realised in events:
            cumulative += realised
            day = when.date().isoformat()
            if pnl and pnl[-1]["date"] == day:
                pnl[-1]["cumulativePnl"] = float(cumulative)
            else:
                pnl.append({"date": day, "cumulativePnl": float(cumulative)})

        return {"investment": investment, "pnl": pnl, "equity": self._equity_series(trades, target_currency, rate, valuation_mode)}

    def _equity_series(self, trades: List[Dict[str, Any]], target_currency: str, rate: Optional[Dict[str, Any]], valuation_mode: str = "BID") -> List[Dict[str, Any]]:
        """Market value of open holdings on each snapshotted trading day.
        Prices forward-fill between snapshots; a ticker with no snapshot yet
        falls back to its most recent trade price (cost)."""
        if not trades:
            return []
        tickers = sorted({t["ticker"] for t in trades})
        history = self.price_history.get_history(tickers)
        if not history:
            return []

        hist_by_date: Dict[str, Dict[str, int]] = defaultdict(dict)
        for h in history:
            if valuation_mode == "ASK":
                hist_by_date[h["date"]][h["ticker"]] = h.get("askPrice") if h.get("askPrice") is not None else h["price"]
            else:
                hist_by_date[h["date"]][h["ticker"]] = h.get("bidPrice") if h.get("bidPrice") is not None else h["price"]

        def convert_val(amount, trade_currency):
            if not amount or trade_currency == target_currency:
                return amount
            if not rate:
                return amount
            if trade_currency == 'USD' and target_currency == 'KHR':
                return amount * Decimal(rate['bidRate'])
            if trade_currency == 'KHR' and target_currency == 'USD':
                return amount / Decimal(rate['askRate'])
            return amount

        holdings: Dict[str, int] = defaultdict(int)
        last_trade_price: Dict[str, int] = {}
        trade_currency: Dict[str, str] = {}
        last_snap_price: Dict[str, int] = {}
        equity: List[Dict[str, Any]] = []
        ti = 0
        for day in sorted(hist_by_date):
            while ti < len(trades) and trades[ti]["orderDate"].date().isoformat() <= day:
                t = trades[ti]
                qty = int(t["qty"])
                holdings[t["ticker"]] += qty if t["side"] == "BUY" else -qty
                last_trade_price[t["ticker"]] = Decimal(t["price"])
                trade_currency[t["ticker"]] = t.get("currency", "KHR")
                ti += 1
            last_snap_price.update(hist_by_date[day])
            if ti == 0:
                continue  # snapshot predates the first trade
            value = Decimal(0)
            for ticker, qty in holdings.items():
                if qty <= 0:
                    continue
                p = last_snap_price.get(ticker, last_trade_price.get(ticker, 0))
                value += convert_val(Decimal(p) * Decimal(qty), trade_currency.get(ticker, "KHR"))
            equity.append({"date": day, "value": float(value)})
        return equity

    def top_profitable_tickers(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        allocs = self.alloc_repo.list_allocations(user_id)
        pnl_by_ticker = defaultdict(lambda: Decimal(0))
        for a in allocs:
            pnl_by_ticker[a["ticker"]] += Decimal(a["realisedPnl"])
        ranked = sorted(pnl_by_ticker.items(), key=lambda x: x[1], reverse=True)[:limit]
        return [{"ticker": t, "realisedPnl": p} for t, p in ranked]

    def top_profitable_buy_orders(self, user_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        allocs = self.alloc_repo.list_allocations(user_id)
        pnl_by_buy = defaultdict(lambda: Decimal(0))
        for a in allocs:
            pnl_by_buy[a["buyTradeId"]] += Decimal(a["realisedPnl"])

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

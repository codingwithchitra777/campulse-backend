from __future__ import annotations
from typing import Dict, Any, List, Optional
from datetime import datetime
from decimal import Decimal
import uuid

from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.services.markets import quantize_money

class BestProfitMatcherService:
    """Matches SELLs against open BUY lots **cheapest-price-first** (minimum cost
    basis => maximum realised P/L). This is deliberately NOT LIFO — the old
    `LifoMatcherService` name was a misnomer and has been retired."""

    def __init__(self, trade_repo: TradeRepository, alloc_repo: AllocationRepository):
        self.trade_repo = trade_repo
        self.alloc_repo = alloc_repo

    def _allocated_qty_for_buy(self, user_id: str, buy_trade_id: str) -> int:
        allocs = self.alloc_repo.list_allocations_for_buy(user_id, buy_trade_id)
        return sum(int(a["qtyAllocated"]) for a in allocs)

    def match_sell(self, sell_trade: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Create allocations rows for this SELL trade.
        Best-profit matching: consumes the LOWEST PRICE open buy lots first
        (minimum cost basis => maximum realised P/L per sale).
        Assumes sell_trade already inserted.
        """
        if sell_trade["side"] != "SELL":
            raise ValueError("match_sell expects a SELL trade")

        user_id = sell_trade["userId"]
        ticker = sell_trade["ticker"]
        # Only match against buys in the SAME market, so e.g. a US "ABC" sell never
        # consumes a CSX "ABC" lot (bare tickers can collide across markets).
        market = sell_trade.get("market", "CSX")
        currency = sell_trade.get("currency", "KHR")
        sell_qty_remaining = int(sell_trade["qty"])
        sell_qty = int(sell_trade["qty"])
        sell_price = Decimal(sell_trade["price"])
        sell_comm = Decimal(sell_trade.get("commission", 0) or 0)
        sell_unit_proceeds = sell_price - (sell_comm / sell_qty if sell_qty else Decimal(0))

        # Best-profit matching: cheapest buy lots first; tie-break on the
        # older lot (lower seq) so results are deterministic.
        buys = self.trade_repo.list_trades_by_side(user_id, ticker, "BUY", market=market)
        buys = sorted(buys, key=lambda b: (Decimal(b["price"]), int(b["seq"])))
        allocations_created = []

        for buy in buys:
            if sell_qty_remaining <= 0:
                break

            buy_qty = int(buy["qty"])
            already_alloc = self._allocated_qty_for_buy(user_id, buy["tradeId"])
            open_qty = buy_qty - already_alloc
            if open_qty <= 0:
                continue

            qty_alloc = min(open_qty, sell_qty_remaining)

            # Commission proportional allocation
            buy_comm = Decimal(buy.get("commission", 0) or 0)
            buy_price = Decimal(buy["price"])

            buy_unit_cost = buy_price + (buy_comm / buy_qty if buy_qty else Decimal(0))
            realised = quantize_money(Decimal(qty_alloc) * (sell_unit_proceeds - buy_unit_cost), currency)

            alloc = {
                "allocId": str(uuid.uuid4()),
                "userId": user_id,
                "ticker": ticker,
                "sellTradeId": sell_trade["tradeId"],
                "buyTradeId": buy["tradeId"],
                "qtyAllocated": qty_alloc,
                "buyPrice": buy_price,
                "buyCommission": buy_comm,
                "buyQty": buy_qty,
                "sellPrice": sell_price,
                "sellCommission": sell_comm,
                "sellQty": sell_qty,
                "realisedPnl": realised,
                "createdAt": datetime.utcnow(),
                "market": market,
                "currency": currency,
            }

            self.alloc_repo.add_allocation(alloc)
            allocations_created.append(alloc)

            sell_qty_remaining -= qty_alloc

        if sell_qty_remaining > 0:
            raise ValueError(f"SELL qty exceeds available position by {sell_qty_remaining} shares for {ticker}")

        return allocations_created

    def simulate_sell(self, user_id: str, ticker: str, price, qty: int, commission=None, market: Optional[str] = None) -> Dict[str, Any]:
        """
        Simulate best-profit matching for a proposed SELL trade and compute
        simulated P/L. Must mirror match_sell's lot ordering exactly.
        Does NOT insert any records into the database.
        """
        from app.services.markets import normalize_market, default_currency

        price = Decimal(price)
        m = normalize_market(market) if market else None
        buys = self.trade_repo.list_trades_by_side(user_id, ticker, "BUY", market=m)
        buys = sorted(buys, key=lambda b: (Decimal(b["price"]), int(b["seq"])))
        # Currency: from the explicit market, else inferred from the held lots.
        currency = default_currency(m) if m else (buys[0].get("currency", "KHR") if buys else "KHR")

        # Calculate open qty for each buy lot
        allocs = self.alloc_repo.list_allocations(user_id, ticker, market=m)
        alloc_by_buy = {}
        for a in allocs:
            buy_id = a["buyTradeId"]
            alloc_by_buy[buy_id] = alloc_by_buy.get(buy_id, 0) + int(a["qtyAllocated"])

        qty_to_match = qty
        total_cost_basis = Decimal(0)

        for buy in buys:
            if qty_to_match <= 0:
                break

            buy_qty = int(buy["qty"])
            already_alloc = alloc_by_buy.get(buy["tradeId"], 0)
            open_qty = buy_qty - already_alloc
            if open_qty <= 0:
                continue

            qty_alloc = min(open_qty, qty_to_match)
            buy_comm = Decimal(buy.get("commission", 0) or 0)

            # Unit cost for this buy lot (including proportional commission)
            buy_unit_cost = Decimal(buy["price"]) + (buy_comm / buy_qty if buy_qty else Decimal(0))
            total_cost_basis += Decimal(qty_alloc) * buy_unit_cost
            qty_to_match -= qty_alloc

        if qty_to_match > 0:
            # Not enough shares to sell
            return {
                "valid": False,
                "validationError": f"Cannot sell {qty} shares. You only own {qty - qty_to_match} shares of {ticker}."
            }

        # Calculate simulated proceeds (net of sell commission)
        if commission is not None:
            sell_comm = Decimal(commission)
        else:
            sell_comm = quantize_money(price * qty * Decimal("0.0047"), currency)
        total_proceeds = (Decimal(qty) * price) - sell_comm
        simulated_pnl = quantize_money(total_proceeds - total_cost_basis, currency)

        is_loss = simulated_pnl < 0
        loss_amount = abs(simulated_pnl) if is_loss else Decimal(0)

        return {
            "valid": True,
            "validationError": None,
            "simulatedPnl": simulated_pnl,
            "isLoss": is_loss,
            "simulatedLossAmount": loss_amount
        }


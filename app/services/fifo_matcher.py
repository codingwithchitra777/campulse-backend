from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime
import uuid

from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository

class FifoMatcherService:
    def __init__(self, trade_repo: TradeRepository, alloc_repo: AllocationRepository):
        self.trade_repo = trade_repo
        self.alloc_repo = alloc_repo

    def _allocated_qty_for_buy(self, user_id: str, buy_trade_id: str) -> int:
        allocs = self.alloc_repo.list_allocations_for_buy(user_id, buy_trade_id)
        return sum(int(a["qtyAllocated"]) for a in allocs)

    def match_sell_fifo(self, sell_trade: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Create fifo_allocations rows for this SELL trade.
        Assumes sell_trade already inserted.
        """
        if sell_trade["side"] != "SELL":
            raise ValueError("match_sell_fifo expects a SELL trade")

        user_id = sell_trade["userId"]
        ticker = sell_trade["ticker"]
        sell_qty_remaining = int(sell_trade["qty"])

        buys = self.trade_repo.list_trades_by_side(user_id, ticker, "BUY")
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
            buy_comm = int(buy.get("commission", 0))
            sell_comm = int(sell_trade.get("commission", 0))

            buy_unit_cost = float(buy["price"]) + (buy_comm / buy_qty if buy_qty else 0.0)
            sell_unit_proceeds = float(sell_trade["price"]) - (sell_comm / int(sell_trade["qty"]) if int(sell_trade["qty"]) else 0.0)

            realised = int(round(qty_alloc * (sell_unit_proceeds - buy_unit_cost)))

            alloc = {
                "allocId": str(uuid.uuid4()),
                "userId": user_id,
                "ticker": ticker,
                "sellTradeId": sell_trade["tradeId"],
                "buyTradeId": buy["tradeId"],
                "qtyAllocated": qty_alloc,
                "buyPrice": int(buy["price"]),
                "buyCommission": buy_comm,
                "buyQty": buy_qty,
                "sellPrice": int(sell_trade["price"]),
                "sellCommission": sell_comm,
                "sellQty": int(sell_trade["qty"]),
                "realisedPnl": realised,
                "createdAt": datetime.utcnow(),
            }

            self.alloc_repo.add_allocation(alloc)
            allocations_created.append(alloc)

            sell_qty_remaining -= qty_alloc

        if sell_qty_remaining > 0:
            raise ValueError(f"SELL qty exceeds available position by {sell_qty_remaining} shares for {ticker}")

        return allocations_created

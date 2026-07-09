from typing import List, Optional, Dict, Any
from app.db.database import get_db

class AllocationRepository:
    def add_allocation(self, alloc: Dict[str, Any]) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                     """
                     INSERT INTO allocations (
                         alloc_id, user_id, ticker, sell_trade_id, buy_trade_id, qty_allocated,
                         buy_price, buy_commission, buy_qty, sell_price, sell_commission, sell_qty,
                         realised_pnl, created_at
                     ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                     """,
                     (
                         alloc["allocId"],
                         alloc["userId"],
                         alloc["ticker"],
                         alloc["sellTradeId"],
                         alloc["buyTradeId"],
                         alloc["qtyAllocated"],
                         alloc["buyPrice"],
                         alloc["buyCommission"],
                         alloc["buyQty"],
                         alloc["sellPrice"],
                         alloc["sellCommission"],
                         alloc["sellQty"],
                         alloc["realisedPnl"],
                         alloc["createdAt"]
                     )
                )

    def list_allocations(self, user_id: str, ticker: Optional[str] = None) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                query = """
                     SELECT alloc_id, user_id, ticker, sell_trade_id, buy_trade_id, qty_allocated,
                            buy_price, buy_commission, buy_qty, sell_price, sell_commission, sell_qty,
                            realised_pnl, created_at FROM allocations WHERE user_id = %s
                """
                params = [user_id]
                if ticker:
                     query += " AND ticker = %s"
                     params.append(ticker)
                query += " ORDER BY created_at ASC"
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [
                     {
                         "allocId": r[0],
                         "userId": r[1],
                         "ticker": r[2],
                         "sellTradeId": r[3],
                         "buyTradeId": r[4],
                         "qtyAllocated": r[5],
                         "buyPrice": r[6],
                         "buyCommission": r[7],
                         "buyQty": r[8],
                         "sellPrice": r[9],
                         "sellCommission": r[10],
                         "sellQty": r[11],
                         "realisedPnl": r[12],
                         "createdAt": r[13]
                     }
                     for r in rows
                ]

    def has_allocations(self, trade_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM allocations WHERE buy_trade_id = %s OR sell_trade_id = %s)",
                    (trade_id, trade_id)
                )
                return cur.fetchone()[0]

    def get_total_realised_pnl(self) -> float:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(realised_pnl), 0) FROM allocations")
                return cur.fetchone()[0]

    def list_allocations_for_buy(self, user_id: str, buy_trade_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                     """
                     SELECT alloc_id, user_id, ticker, sell_trade_id, buy_trade_id, qty_allocated,
                            buy_price, buy_commission, buy_qty, sell_price, sell_commission, sell_qty,
                            realised_pnl, created_at FROM allocations 
                     WHERE user_id = %s AND buy_trade_id = %s
                     """,
                     (user_id, buy_trade_id)
                )
                rows = cur.fetchall()
                return [
                     {
                         "allocId": r[0],
                         "userId": r[1],
                         "ticker": r[2],
                         "sellTradeId": r[3],
                         "buyTradeId": r[4],
                         "qtyAllocated": r[5],
                         "buyPrice": r[6],
                         "buyCommission": r[7],
                         "buyQty": r[8],
                         "sellPrice": r[9],
                         "sellCommission": r[10],
                         "sellQty": r[11],
                         "realisedPnl": r[12],
                         "createdAt": r[13]
                     }
                     for r in rows
                ]

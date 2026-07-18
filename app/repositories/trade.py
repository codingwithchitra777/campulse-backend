from typing import List, Optional, Dict, Any
from datetime import datetime
from app.db.database import get_db

class TradeRepository:
    def add_trade(self, trade: Dict[str, Any]) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Ensure the user exists before inserting the trade
                cur.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (trade["userId"], "User " + trade["userId"])
                )
                cur.execute(
                    """
                    INSERT INTO trades (trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        trade["tradeId"],
                        trade["userId"],
                        trade["seq"],
                        trade["ticker"],
                        trade["side"],
                        trade["price"],
                        trade["qty"],
                        trade["commission"],
                        trade["orderDate"],
                        trade.get("market", "CSX"),
                        trade.get("currency", "KHR")
                    )
                )

    def get_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency, note, tags FROM trades WHERE trade_id = %s",
                    (trade_id,)
                )
                row = cur.fetchone()
                if not row:
                     return None
                return {
                     "tradeId": row[0],
                     "userId": row[1],
                     "seq": row[2],
                     "ticker": row[3],
                     "side": row[4],
                     "price": row[5],
                     "qty": row[6],
                     "commission": row[7],
                     "orderDate": row[8],
                     "market": row[9],
                     "currency": row[10],
                     "note": row[11],
                     "tags": row[12]
                }

    def list_trades(
        self,
        user_id: str,
        ticker: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        market: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                query = "SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency, note, tags, corp_action_id FROM trades WHERE user_id = %s"
                params = [user_id]
                if ticker:
                     query += " AND ticker = %s"
                     params.append(ticker)
                if market:
                     query += " AND market = %s"
                     params.append(market)
                query += " ORDER BY order_date ASC, seq ASC"
                if limit is not None:
                     query += " LIMIT %s OFFSET %s"
                     params.extend([limit, offset])
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [
                     {
                         "tradeId": r[0],
                         "userId": r[1],
                         "seq": r[2],
                         "ticker": r[3],
                         "side": r[4],
                         "price": r[5],
                         "qty": r[6],
                         "commission": r[7],
                         "orderDate": r[8],
                         "market": r[9],
                         "currency": r[10],
                         "note": r[11],
                         "tags": r[12],
                         "corpActionId": r[13]
                     }
                     for r in rows
                ]

    def list_all_trades(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency
                    FROM trades ORDER BY order_date DESC LIMIT %s OFFSET %s
                    """,
                    (limit, offset)
                )
                rows = cur.fetchall()
                return [
                     {
                         "tradeId": r[0],
                         "userId": r[1],
                         "seq": r[2],
                         "ticker": r[3],
                         "side": r[4],
                         "price": r[5],
                         "qty": r[6],
                         "commission": r[7],
                         "orderDate": r[8],
                         "market": r[9],
                         "currency": r[10]
                     }
                     for r in rows
                ]

    def count_trades(self, user_id: Optional[str] = None, ticker: Optional[str] = None) -> int:
        with get_db() as conn:
            with conn.cursor() as cur:
                query = "SELECT COUNT(*) FROM trades"
                clauses = []
                params = []
                if user_id:
                    clauses.append("user_id = %s")
                    params.append(user_id)
                if ticker:
                    clauses.append("ticker = %s")
                    params.append(ticker)
                if clauses:
                    query += " WHERE " + " AND ".join(clauses)
                cur.execute(query, tuple(params))
                return cur.fetchone()[0]

    def next_seq(self, user_id: str) -> int:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM trades WHERE user_id = %s", (user_id,))
                return cur.fetchone()[0]

    def update_trade(
        self, trade_id: str, user_id: str, ticker: str, price: int, qty: int, commission: int,
        order_date: datetime
    ) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE trades SET ticker = %s, price = %s, qty = %s, commission = %s, order_date = %s
                    WHERE trade_id = %s AND user_id = %s
                    RETURNING trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency
                    """,
                    (ticker, price, qty, commission, order_date, trade_id, user_id)
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                     "tradeId": row[0],
                     "userId": row[1],
                     "seq": row[2],
                     "ticker": row[3],
                     "side": row[4],
                     "price": row[5],
                     "qty": row[6],
                     "commission": row[7],
                     "orderDate": row[8],
                     "market": row[9],
                     "currency": row[10]
                }

    def update_journal(self, trade_id: str, user_id: str, note, tags) -> Optional[Dict[str, Any]]:
        """Set the journal note/tags on any of the user's trades (metadata, so
        allowed on matched/SELL trades unlike price/qty edits)."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE trades SET note = %s, tags = %s
                    WHERE trade_id = %s AND user_id = %s
                    RETURNING trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency, note, tags
                    """,
                    (note, tags, trade_id, user_id)
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                     "tradeId": row[0], "userId": row[1], "seq": row[2], "ticker": row[3],
                     "side": row[4], "price": row[5], "qty": row[6], "commission": row[7],
                     "orderDate": row[8], "market": row[9], "currency": row[10],
                     "note": row[11], "tags": row[12]
                }

    def delete_trade(self, trade_id: str, user_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM trades WHERE trade_id = %s AND user_id = %s",
                    (trade_id, user_id)
                )
                return cur.rowcount > 0

    def list_trades_by_side(self, user_id: str, ticker: str, side: str, market: Optional[str] = None) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                query = """
                     SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date, market, currency FROM trades
                     WHERE user_id = %s AND ticker = %s AND side = %s
                     """
                params = [user_id, ticker, side]
                if market:
                     query += " AND market = %s"
                     params.append(market)
                query += " ORDER BY order_date ASC, seq ASC"
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
                return [
                     {
                         "tradeId": r[0],
                         "userId": r[1],
                         "seq": r[2],
                         "ticker": r[3],
                         "side": r[4],
                         "price": r[5],
                         "qty": r[6],
                         "commission": r[7],
                         "orderDate": r[8],
                         "market": r[9],
                         "currency": r[10]
                     }
                     for r in rows
                ]

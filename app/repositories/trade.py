from typing import List, Optional, Dict, Any
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
                    INSERT INTO trades (trade_id, user_id, seq, ticker, side, price, qty, commission, order_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        trade["orderDate"]
                    )
                )

    def get_trade(self, trade_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date FROM trades WHERE trade_id = %s",
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
                     "orderDate": row[8]
                }

    def list_trades(
        self,
        user_id: str,
        ticker: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                query = "SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date FROM trades WHERE user_id = %s"
                params = [user_id]
                if ticker:
                     query += " AND ticker = %s"
                     params.append(ticker)
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
                         "orderDate": r[8]
                     }
                     for r in rows
                ]

    def list_all_trades(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date
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
                         "orderDate": r[8]
                     }
                     for r in rows
                ]

    def count_trades(self, user_id: Optional[str] = None) -> int:
        with get_db() as conn:
            with conn.cursor() as cur:
                if user_id:
                    cur.execute("SELECT COUNT(*) FROM trades WHERE user_id = %s", (user_id,))
                else:
                    cur.execute("SELECT COUNT(*) FROM trades")
                return cur.fetchone()[0]

    def list_trades_by_side(self, user_id: str, ticker: str, side: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                     """
                     SELECT trade_id, user_id, seq, ticker, side, price, qty, commission, order_date FROM trades 
                     WHERE user_id = %s AND ticker = %s AND side = %s
                     ORDER BY order_date ASC, seq ASC
                     """,
                     (user_id, ticker, side)
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
                         "orderDate": r[8]
                     }
                     for r in rows
                ]

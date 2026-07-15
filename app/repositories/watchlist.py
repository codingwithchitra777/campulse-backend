from typing import List, Dict, Any
from app.db.database import get_db


class WatchlistRepository:
    def add(self, user_id: str, market: str, symbol: str, currency: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                # The watchlist FKs to users; ensure the row exists (a freshly
                # authed user may not have recorded any trade yet).
                cur.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, "User " + user_id)
                )
                cur.execute(
                    """
                    INSERT INTO watchlist (user_id, market, symbol, currency)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, market, symbol) DO NOTHING
                    """,
                    (user_id, market, symbol.upper(), currency)
                )

    def list(self, user_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT market, symbol, currency, added_at FROM watchlist "
                    "WHERE user_id = %s ORDER BY added_at DESC",
                    (user_id,)
                )
                return [
                    {"market": r[0], "symbol": r[1], "currency": r[2], "addedAt": r[3]}
                    for r in cur.fetchall()
                ]

    def remove(self, user_id: str, market: str, symbol: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM watchlist WHERE user_id = %s AND market = %s AND symbol = %s",
                    (user_id, market, symbol.upper())
                )
                return cur.rowcount > 0

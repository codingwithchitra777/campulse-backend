from datetime import date
from typing import List, Dict, Any
from app.db.database import get_db

class PriceHistoryRepository:
    def upsert_snapshot(self, ticker: str, snapshot_date: date, price, market: str = "CSX",
                        bid_price=None, ask_price=None) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO price_history (ticker, snapshot_date, price, market, bid_price, ask_price)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticker, snapshot_date) DO UPDATE SET 
                        price = EXCLUDED.price, 
                        market = EXCLUDED.market,
                        bid_price = EXCLUDED.bid_price,
                        ask_price = EXCLUDED.ask_price
                    """,
                    (ticker, snapshot_date, price, market, bid_price, ask_price)
                )

    def get_history(self, tickers: List[str]) -> List[Dict[str, Any]]:
        if not tickers:
            return []
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker, snapshot_date, price, bid_price, ask_price FROM price_history
                    WHERE ticker = ANY(%s)
                    ORDER BY snapshot_date ASC
                    """,
                    (tickers,)
                )
                rows = cur.fetchall()
                return [
                    {
                        "ticker": r[0],
                        "date": r[1].isoformat(),
                        "price": r[2],
                        "bidPrice": r[3],
                        "askPrice": r[4]
                    }
                    for r in rows
                ]

    def get_recent_history_all(self, days: int = 60) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ticker, snapshot_date, price, bid_price, ask_price FROM price_history
                    WHERE snapshot_date >= CURRENT_DATE - INTERVAL '%s days'
                    ORDER BY snapshot_date ASC
                    """,
                    (days,)
                )
                rows = cur.fetchall()
                return [
                    {
                        "ticker": r[0],
                        "date": r[1].isoformat(),
                        "price": r[2],
                        "bidPrice": r[3],
                        "askPrice": r[4]
                    }
                    for r in rows
                ]

    def delete_snapshots(self, ticker: str) -> int:
        """Test-cleanup helper: remove all snapshots for one exact ticker."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM price_history WHERE ticker = %s", (ticker,))
                return cur.rowcount

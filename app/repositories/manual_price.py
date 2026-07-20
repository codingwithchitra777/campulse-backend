from typing import Optional, Dict, Any, List
from app.db.database import get_db


class ManualPriceRepository:
    """Current admin-set price for instruments with no live feed (local gold)."""

    def upsert(self, market: str, symbol: str, price, currency: str = "USD",
               change=0, updated_by: Optional[str] = None,
               bid_price: Optional[float] = None, ask_price: Optional[float] = None) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO manual_prices (market, symbol, price, currency, change, updated_by, updated_at, bid_price, ask_price)
                    VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s, %s)
                    ON CONFLICT (market, symbol) DO UPDATE SET
                        price = EXCLUDED.price,
                        currency = EXCLUDED.currency,
                        change = EXCLUDED.change,
                        updated_by = EXCLUDED.updated_by,
                        bid_price = EXCLUDED.bid_price,
                        ask_price = EXCLUDED.ask_price,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (market, symbol.upper(), price, currency, change, updated_by, bid_price, ask_price)
                )

    def get(self, market: str, symbol: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT market, symbol, price, currency, change, updated_by, updated_at, bid_price, ask_price "
                    "FROM manual_prices WHERE market = %s AND symbol = %s",
                    (market, symbol.upper())
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "market": row[0], "symbol": row[1], "price": row[2], "currency": row[3],
                    "change": row[4], "updatedBy": row[5], "updatedAt": row[6],
                    "bidPrice": row[7], "askPrice": row[8],
                }

    def list_all(self) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT market, symbol, price, currency, change, updated_by, updated_at, bid_price, ask_price "
                    "FROM manual_prices ORDER BY market, symbol"
                )
                return [
                    {"market": r[0], "symbol": r[1], "price": r[2], "currency": r[3],
                     "change": r[4], "updatedBy": r[5], "updatedAt": r[6], "bidPrice": r[7], "askPrice": r[8]}
                    for r in cur.fetchall()
                ]

    def delete(self, market: str, symbol: str) -> int:
        """Test-cleanup helper: remove one exact manual price row."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM manual_prices WHERE market = %s AND symbol = %s",
                            (market, symbol.upper()))
                return cur.rowcount

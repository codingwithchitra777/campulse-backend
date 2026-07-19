from typing import List, Optional, Dict, Any
from datetime import datetime, date
from app.db.database import get_db

class MarketEventRepository:
    def upsert_event(self, event_id: str, event_type: str, event_date: date, 
                     market: str = "CSX", symbol: Optional[str] = None, 
                     description: Optional[str] = None, created_by: Optional[str] = None) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO market_events (event_id, market, event_type, symbol, event_date, description, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id) DO UPDATE SET
                        market = EXCLUDED.market,
                        event_type = EXCLUDED.event_type,
                        symbol = EXCLUDED.symbol,
                        event_date = EXCLUDED.event_date,
                        description = EXCLUDED.description
                    """,
                    (event_id, market, event_type, symbol, event_date, description, created_by)
                )

    def delete_event(self, event_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM market_events WHERE event_id = %s", (event_id,))

    def list_events(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT event_id, market, event_type, symbol, event_date, description, created_by, created_at 
                    FROM market_events 
                    ORDER BY event_date ASC
                    LIMIT %s OFFSET %s
                    """, (limit, offset)
                )
                rows = cur.fetchall()
                return [
                    {
                        "eventId": r[0],
                        "market": r[1],
                        "eventType": r[2],
                        "symbol": r[3],
                        "eventDate": r[4],
                        "description": r[5],
                        "createdBy": r[6],
                        "createdAt": r[7]
                    } for r in rows
                ]

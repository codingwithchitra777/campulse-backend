import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.db.database import get_db

class ExchangeRateRepository:
    def add_rate(self, base_currency: str, target_currency: str, bid_rate: float, ask_rate: float, effective_date: str, created_by: Optional[str] = None) -> Dict[str, Any]:
        rate_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cur:
                # Upsert to allow correcting a date's rate
                cur.execute(
                    """
                    INSERT INTO exchange_rates (id, base_currency, target_currency, bid_rate, ask_rate, effective_date, created_by, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (base_currency, target_currency, effective_date)
                    DO UPDATE SET bid_rate = EXCLUDED.bid_rate,
                                  ask_rate = EXCLUDED.ask_rate,
                                  created_by = EXCLUDED.created_by,
                                  created_at = EXCLUDED.created_at
                    RETURNING id, base_currency, target_currency, bid_rate, ask_rate, effective_date, created_by, created_at
                    """,
                    (rate_id, base_currency.upper(), target_currency.upper(), bid_rate, ask_rate, effective_date, created_by, datetime.utcnow())
                )
                row = cur.fetchone()
                return {
                    "id": row[0],
                    "baseCurrency": row[1],
                    "targetCurrency": row[2],
                    "bidRate": float(row[3]),
                    "askRate": float(row[4]),
                    "effectiveDate": row[5].isoformat() if hasattr(row[5], "isoformat") else row[5],
                    "createdBy": row[6],
                    "createdAt": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7]
                }

    def get_latest_rate(self, base_currency: str = 'USD', target_currency: str = 'KHR') -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, base_currency, target_currency, bid_rate, ask_rate, effective_date, created_by, created_at
                    FROM exchange_rates
                    WHERE base_currency = %s AND target_currency = %s
                    ORDER BY effective_date DESC, created_at DESC
                    LIMIT 1
                    """,
                    (base_currency.upper(), target_currency.upper())
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row[0],
                    "baseCurrency": row[1],
                    "targetCurrency": row[2],
                    "bidRate": float(row[3]),
                    "askRate": float(row[4]),
                    "effectiveDate": row[5].isoformat() if hasattr(row[5], "isoformat") else row[5],
                    "createdBy": row[6],
                    "createdAt": row[7].isoformat() if hasattr(row[7], "isoformat") else row[7]
                }

    def get_history(self, base_currency: str = 'USD', target_currency: str = 'KHR', limit: int = 100) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, base_currency, target_currency, bid_rate, ask_rate, effective_date, created_by, created_at
                    FROM exchange_rates
                    WHERE base_currency = %s AND target_currency = %s
                    ORDER BY effective_date DESC, created_at DESC
                    LIMIT %s
                    """,
                    (base_currency.upper(), target_currency.upper(), limit)
                )
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "baseCurrency": r[1],
                        "targetCurrency": r[2],
                        "bidRate": float(r[3]),
                        "askRate": float(r[4]),
                        "effectiveDate": r[5].isoformat() if hasattr(r[5], "isoformat") else r[5],
                        "createdBy": r[6],
                        "createdAt": r[7].isoformat() if hasattr(r[7], "isoformat") else r[7]
                    }
                    for r in rows
                ]

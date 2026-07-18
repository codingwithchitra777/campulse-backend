import uuid
from datetime import datetime
from typing import List, Dict, Any
from app.db.database import get_db


class AlertRepository:
    def create(self, user_id: str, market: str, symbol: str, currency: str,
               target_price, direction: str) -> Dict[str, Any]:
        alert_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, "User " + user_id)
                )
                cur.execute(
                    """
                    INSERT INTO price_alerts (alert_id, user_id, market, symbol, currency, target_price, direction)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (alert_id, user_id, market, symbol.upper(), currency, target_price, direction)
                )
        return {"alertId": alert_id, "market": market, "symbol": symbol.upper(),
                "currency": currency, "targetPrice": target_price, "direction": direction, "active": True}

    def list_for_user(self, user_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT alert_id, market, symbol, currency, target_price, direction, active, created_at, triggered_at "
                    "FROM price_alerts WHERE user_id = %s ORDER BY active DESC, created_at DESC",
                    (user_id,)
                )
                return [self._row(r) for r in cur.fetchall()]

    def list_active(self) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT alert_id, user_id, market, symbol, currency, target_price, direction "
                    "FROM price_alerts WHERE active = TRUE"
                )
                return [
                    {"alertId": r[0], "userId": r[1], "market": r[2], "symbol": r[3],
                     "currency": r[4], "targetPrice": r[5], "direction": r[6]}
                    for r in cur.fetchall()
                ]

    def rescale_targets(self, market: str, symbol: str, multiplier) -> List[Dict[str, Any]]:
        """Divide active alerts' targets by the corporate-action multiplier so a
        1:1 bonus doesn't turn "below 2,000" into a false crash ping. Returns the
        affected alerts (old + new target) so the caller can notify each owner."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE price_alerts
                    SET target_price = ROUND(target_price / %s, 4)
                    WHERE active = TRUE AND market = %s AND symbol = %s
                    RETURNING alert_id, user_id, symbol, currency,
                              ROUND(target_price * %s, 4), target_price
                    """,
                    (multiplier, market, symbol.upper(), multiplier)
                )
                return [
                    {"alertId": r[0], "userId": r[1], "symbol": r[2], "currency": r[3],
                     "oldTarget": r[4], "newTarget": r[5]}
                    for r in cur.fetchall()
                ]

    def remove(self, alert_id: str, user_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM price_alerts WHERE alert_id = %s AND user_id = %s", (alert_id, user_id))
                return cur.rowcount > 0

    def mark_triggered(self, alert_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE price_alerts SET active = FALSE, triggered_at = %s WHERE alert_id = %s",
                    (datetime.utcnow(), alert_id)
                )

    @staticmethod
    def _row(r) -> Dict[str, Any]:
        return {
            "alertId": r[0], "market": r[1], "symbol": r[2], "currency": r[3],
            "targetPrice": r[4], "direction": r[5], "active": r[6],
            "createdAt": r[7].isoformat() if r[7] else None,
            "triggeredAt": r[8].isoformat() if r[8] else None,
        }

import json
from typing import Any, Dict, Optional
from psycopg2.extras import Json

from app.db.database import get_db


class GridPlanRepository:
    """Persistence for a user's active grid plan — one row per user.

    The plan is stored as a JSONB document (the camelCase shape the web client
    owns); the server never queries inside it, so this stays fully decoupled
    from the trades/allocations tables. Fills are recorded as normal trades via
    the trade endpoints, not here.
    """

    def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT plan FROM grid_plans WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                    return None
                # psycopg2 usually decodes JSONB to a dict; guard the string case.
                return json.loads(row[0]) if isinstance(row[0], str) else row[0]

    def upsert(self, user_id: str, plan: Dict[str, Any]) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                # grid_plans FKs to users; ensure the row exists (a freshly
                # authed user may not have recorded any trade yet).
                cur.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, "User " + user_id),
                )
                cur.execute(
                    """
                    INSERT INTO grid_plans (user_id, plan, updated_at)
                    VALUES (%s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id)
                    DO UPDATE SET plan = EXCLUDED.plan, updated_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, Json(plan)),
                )

    def delete(self, user_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM grid_plans WHERE user_id = %s", (user_id,))
                return cur.rowcount > 0

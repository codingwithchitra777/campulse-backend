import json
from typing import Any, Dict, List, Optional

from psycopg2.extras import Json

from app.db.database import get_db


class GridPlanRepository:
    """Persistence for a user's grid plans — one row per grid, keyed by grid_id
    and scoped to the owning user_id.

    Each plan is stored as a JSONB document (the camelCase shape the web client
    owns, including its own `id` == grid_id); the server never queries inside
    it, so this stays fully decoupled from the trades/allocations tables. Fills
    are recorded as normal trades via the trade endpoints, not here.
    """

    @staticmethod
    def _to_plan(raw) -> Dict[str, Any]:
        # psycopg2 usually decodes JSONB to a dict; guard the string case.
        return json.loads(raw) if isinstance(raw, str) else raw

    def list_for(self, user_id: str) -> List[Dict[str, Any]]:
        """All of a user's grid plans, most-recently-updated first."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT plan FROM grid_plans WHERE user_id = %s ORDER BY updated_at DESC",
                    (user_id,),
                )
                return [self._to_plan(r[0]) for r in cur.fetchall()]

    def get(self, grid_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT plan FROM grid_plans WHERE grid_id = %s AND user_id = %s",
                    (grid_id, user_id),
                )
                row = cur.fetchone()
                return self._to_plan(row[0]) if row else None

    def upsert(self, grid_id: str, user_id: str, plan: Dict[str, Any]) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                # grid_plans FKs to users; ensure the row exists (a freshly
                # authed user may not have recorded any trade yet).
                cur.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, "User " + user_id),
                )
                # The WHERE on DO UPDATE stops one user from overwriting another
                # user's grid by guessing its grid_id.
                cur.execute(
                    """
                    INSERT INTO grid_plans (grid_id, user_id, plan, updated_at)
                    VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (grid_id)
                    DO UPDATE SET plan = EXCLUDED.plan, updated_at = CURRENT_TIMESTAMP
                    WHERE grid_plans.user_id = EXCLUDED.user_id
                    """,
                    (grid_id, user_id, Json(plan)),
                )

    def delete(self, grid_id: str, user_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM grid_plans WHERE grid_id = %s AND user_id = %s",
                    (grid_id, user_id),
                )
                return cur.rowcount > 0

from typing import Any, Dict, Optional

from app.db.database import get_db


class AIInsightRepository:
    def get(self, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT snapshot_hash, insight, model, generated_at "
                    "FROM ai_insights WHERE user_id = %s",
                    (user_id,)
                )
                r = cur.fetchone()
                if not r:
                    return None
                return {"snapshotHash": r[0], "insight": r[1], "model": r[2], "generatedAt": r[3]}

    def upsert(self, user_id: str, snapshot_hash: str, insight: str, model: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                # FKs to users; a freshly authed user may have no row yet.
                cur.execute(
                    "INSERT INTO users (user_id, user_name) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id, "User " + user_id)
                )
                cur.execute(
                    """
                    INSERT INTO ai_insights (user_id, snapshot_hash, insight, model, generated_at)
                    VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id) DO UPDATE SET
                        snapshot_hash = EXCLUDED.snapshot_hash,
                        insight = EXCLUDED.insight,
                        model = EXCLUDED.model,
                        generated_at = CURRENT_TIMESTAMP
                    """,
                    (user_id, snapshot_hash, insight, model)
                )

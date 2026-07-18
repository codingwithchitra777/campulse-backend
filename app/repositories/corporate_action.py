import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from app.db.database import get_db


def _row(r) -> Dict[str, Any]:
    return {
        "actionId": r[0], "market": r[1], "symbol": r[2], "actionType": r[3],
        "ratioNew": r[4], "ratioHeld": r[5], "exDate": r[6], "note": r[7],
        "createdBy": r[8], "createdAt": r[9], "appliedAt": r[10],
    }


_COLS = ("action_id, market, symbol, action_type, ratio_new, ratio_held, "
         "ex_date, note, created_by, created_at, applied_at")


class CorporateActionRepository:
    def create(self, market: str, symbol: str, action_type: str, ratio_new: int,
               ratio_held: int, ex_date: date, note: Optional[str],
               created_by: str) -> Dict[str, Any]:
        action_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO corporate_actions
                        (action_id, market, symbol, action_type, ratio_new, ratio_held,
                         ex_date, note, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING {_COLS}
                    """,
                    (action_id, market, symbol.upper(), action_type, ratio_new,
                     ratio_held, ex_date, note, created_by)
                )
                return _row(cur.fetchone())

    def get(self, action_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_COLS} FROM corporate_actions WHERE action_id = %s",
                            (action_id,))
                r = cur.fetchone()
                return _row(r) if r else None

    def list_all(self) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SELECT {_COLS} FROM corporate_actions ORDER BY ex_date DESC, created_at DESC")
                return [_row(r) for r in cur.fetchall()]

    def list_pending(self) -> List[Dict[str, Any]]:
        """Actions due (ex-date reached) and not yet applied — the daemon's queue.
        Catch-up is implicit: a missed ex-date is still <= CURRENT_DATE."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLS} FROM corporate_actions "
                    "WHERE applied_at IS NULL AND ex_date <= CURRENT_DATE "
                    "ORDER BY ex_date ASC"
                )
                return [_row(r) for r in cur.fetchall()]

    def mark_applied(self, action_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE corporate_actions SET applied_at = CURRENT_TIMESTAMP WHERE action_id = %s",
                    (action_id,)
                )

    def delete(self, action_id: str) -> bool:
        """Only unapplied actions may be deleted — an applied one already rewrote
        lots and must stay for the audit trail."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM corporate_actions WHERE action_id = %s AND applied_at IS NULL",
                    (action_id,)
                )
                return cur.rowcount > 0

    def users_already_applied(self, action_id: str) -> set:
        """Users whose bonus rows for this action already exist — the resume
        guard after a crash mid-apply (each user is one transaction)."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT user_id FROM trades WHERE corp_action_id = %s",
                            (action_id,))
                return {r[0] for r in cur.fetchall()}

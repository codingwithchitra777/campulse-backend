"""Account-linking persistence. An *alias* login identity (e.g. a Telegram user
id) points at a canonical *primary* account (a Google user id). See the identity
model: Google is canonical, Telegram links into it via a one-time code."""
import secrets
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from app.db.database import get_db

CODE_TTL_MINUTES = 15


class LinkRepository:
    # --- resolution ---

    def get_primary(self, user_id: str) -> str:
        """Map an alias to its canonical account. Unlinked ids map to themselves,
        so this is safe to call on every login/bot write."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT primary_user_id FROM user_links WHERE alias_user_id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                return row[0] if row else user_id

    # --- links ---

    def add_link(self, alias_user_id: str, primary_user_id: str) -> None:
        """Point alias at primary. Idempotent (re-linking updates the target)."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO user_links (alias_user_id, primary_user_id, linked_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (alias_user_id)
                    DO UPDATE SET primary_user_id = EXCLUDED.primary_user_id, linked_at = EXCLUDED.linked_at
                    """,
                    (alias_user_id, primary_user_id, datetime.utcnow()),
                )

    def remove_link(self, alias_user_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM user_links WHERE alias_user_id = %s", (alias_user_id,))
                return cur.rowcount > 0

    def list_links(self, primary_user_id: str) -> List[Dict[str, Any]]:
        """Aliases linked to a primary, joined to their user row for display."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT l.alias_user_id, u.user_name, u.chat_id, l.linked_at
                    FROM user_links l
                    LEFT JOIN users u ON u.user_id = l.alias_user_id
                    WHERE l.primary_user_id = %s
                    ORDER BY l.linked_at DESC
                    """,
                    (primary_user_id,),
                )
                return [
                    {
                        "aliasUserId": r[0],
                        "userName": r[1],
                        "chatId": r[2],
                        "linkedAt": r[3],
                    }
                    for r in cur.fetchall()
                ]

    def migrate_data(self, alias_id: str, primary_id: str) -> int:
        """Re-key the alias account's trades + allocations onto the primary. Trade
        seq is per-user (MAX+1), so alias trades are renumbered above the primary's
        current max to avoid collisions; allocations reference stable trade_ids, so
        match history is preserved. Returns the number of trades moved."""
        if alias_id == primary_id:
            return 0
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(seq), 0) FROM trades WHERE user_id = %s", (primary_id,))
                base = cur.fetchone()[0]
                cur.execute(
                    """
                    WITH ranked AS (
                        SELECT trade_id, ROW_NUMBER() OVER (ORDER BY seq) AS rn
                        FROM trades WHERE user_id = %s
                    )
                    UPDATE trades t
                    SET user_id = %s, seq = %s + ranked.rn
                    FROM ranked
                    WHERE t.trade_id = ranked.trade_id
                    """,
                    (alias_id, primary_id, base),
                )
                moved = cur.rowcount
                cur.execute("UPDATE allocations SET user_id = %s WHERE user_id = %s", (primary_id, alias_id))
                return moved

    def is_primary_of_others(self, user_id: str) -> bool:
        """True if this id is already the canonical target of some alias — such an
        account must not itself become an alias (no chains)."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM user_links WHERE primary_user_id = %s LIMIT 1", (user_id,))
                return cur.fetchone() is not None

    # --- one-time codes ---

    def create_code(self, primary_user_id: str, ttl_minutes: int = CODE_TTL_MINUTES) -> str:
        code = secrets.token_urlsafe(9)  # ~12 chars, URL/deeplink-safe
        expires = datetime.utcnow() + timedelta(minutes=ttl_minutes)
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO link_codes (code, primary_user_id, expires_at) VALUES (%s, %s, %s)",
                    (code, primary_user_id, expires),
                )
        return code

    def consume_code(self, code: str) -> Optional[str]:
        """Atomically redeem an unused, unexpired code. Returns the primary_user_id
        it belongs to, or None if invalid/expired/already used."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE link_codes
                    SET used_at = %s
                    WHERE code = %s AND used_at IS NULL AND expires_at > %s
                    RETURNING primary_user_id
                    """,
                    (datetime.utcnow(), code, datetime.utcnow()),
                )
                row = cur.fetchone()
                return row[0] if row else None

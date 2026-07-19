from typing import List, Optional, Dict, Any
from datetime import datetime
from app.db.database import get_db

class UserRepository:
    def upsert_user(self, user_id: str, user_name: str, chat_id: Optional[int] = None,
                    role: str = "user", email: Optional[str] = None) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                     """
                     INSERT INTO users (user_id, user_name, chat_id, register_date, role, email)
                     VALUES (%s, %s, %s, %s, %s, %s)
                     ON CONFLICT (user_id)
                     DO UPDATE SET user_name = EXCLUDED.user_name,
                                   chat_id = COALESCE(EXCLUDED.chat_id, users.chat_id),
                                   email = COALESCE(EXCLUDED.email, users.email)
                     """,
                     (user_id, user_name, chat_id, datetime.utcnow(), role, email)
                )

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, user_name, register_date, chat_id, role, email, market_overview_enabled FROM users WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                     return None
                return {
                     "userId": row[0],
                     "userName": row[1],
                     "registerDate": row[2],
                     "chat_id": row[3],
                     "role": row[4],
                     "email": row[5],
                     "market_overview_enabled": row[6]
                }

    def update_market_overview(self, user_id: str, enabled: bool) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET market_overview_enabled = %s WHERE user_id = %s",
                    (enabled, user_id)
                )

    def update_role(self, user_id: str, role: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE users SET role = %s WHERE user_id = %s
                    RETURNING user_id, user_name, register_date, chat_id, role
                    """,
                    (role, user_id)
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                     "userId": row[0],
                     "userName": row[1],
                     "registerDate": row[2],
                     "chat_id": row[3],
                     "role": row[4]
                }

    def get_all_users(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                 cur.execute(
                     "SELECT user_id, user_name, register_date, chat_id, role, market_overview_enabled FROM users "
                     "ORDER BY register_date DESC LIMIT %s OFFSET %s",
                     (limit, offset)
                 )
                 rows = cur.fetchall()
                 return [
                     {
                         "userId": r[0],
                         "userName": r[1],
                         "registerDate": r[2],
                         "chat_id": r[3],
                         "role": r[4],
                         "market_overview_enabled": r[5]
                     }
                     for r in rows
                 ]

    def count_users(self) -> int:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                return cur.fetchone()[0]

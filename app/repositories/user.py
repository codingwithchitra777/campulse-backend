from typing import List, Optional, Dict, Any
from datetime import datetime
from app.db.database import get_db

class UserRepository:
    def upsert_user(self, user_id: str, user_name: str, chat_id: Optional[int] = None) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                     """
                     INSERT INTO users (user_id, user_name, chat_id, register_date)
                     VALUES (%s, %s, %s, %s)
                     ON CONFLICT (user_id)
                     DO UPDATE SET user_name = EXCLUDED.user_name, chat_id = COALESCE(EXCLUDED.chat_id, users.chat_id)
                     """,
                     (user_id, user_name, chat_id, datetime.utcnow())
                )

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_id, user_name, register_date, chat_id FROM users WHERE user_id = %s", (user_id,))
                row = cur.fetchone()
                if not row:
                     return None
                return {
                     "userId": row[0],
                     "userName": row[1],
                     "registerDate": row[2],
                     "chat_id": row[3]
                }

    def get_all_users(self) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                 cur.execute("SELECT user_id, user_name, register_date, chat_id FROM users")
                 rows = cur.fetchall()
                 return [
                     {
                         "userId": r[0],
                         "userName": r[1],
                         "registerDate": r[2],
                         "chat_id": r[3]
                     }
                     for r in rows
                 ]

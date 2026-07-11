import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(user_id: str, role: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_expire_days),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])


TELEGRAM_AUTH_MAX_AGE_SECONDS = 86400


def verify_telegram_webapp_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """Validate a Telegram Mini App initData string per
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app.
    Unlike the Login Widget, the HMAC key is HMAC_SHA256("WebAppData", bot_token).
    Returns the parsed `user` object on success, None on failure."""
    from urllib.parse import parse_qsl

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    auth_date = pairs.get("auth_date")
    if not received_hash or not auth_date:
        return None
    if datetime.now(timezone.utc).timestamp() - int(auth_date) > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        return None
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        user = json.loads(pairs.get("user", ""))
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and user.get("id") else None


def verify_telegram_auth(data: dict, bot_token: str) -> bool:
    """Validate a Telegram Login Widget payload per https://core.telegram.org/widgets/login:
    HMAC-SHA256 over the sorted key=value lines, keyed with SHA256(bot_token)."""
    received_hash = data.get("hash")
    auth_date = data.get("auth_date")
    if not received_hash or not auth_date:
        return False
    if datetime.now(timezone.utc).timestamp() - int(auth_date) > TELEGRAM_AUTH_MAX_AGE_SECONDS:
        return False
    check_fields = {k: v for k, v in data.items() if k != "hash" and v is not None}
    data_check_string = "\n".join(f"{k}={check_fields[k]}" for k in sorted(check_fields))
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash)

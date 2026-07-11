"""Thin Telegram Bot API sender over `requests` (same approach as
endpoints/deploy.py). Used to reply to webhook updates. All failures are
logged and swallowed — never raise into the webhook handler, or Telegram
would retry the whole update."""
import io
import logging
from typing import Optional

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

_BASE = "https://api.telegram.org"


def _url(method: str) -> str:
    return f"{_BASE}/bot{settings.telegram_token}/{method}"


def send_message(chat_id: int, text: str, parse_mode: Optional[str] = None) -> None:
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        requests.post(_url("sendMessage"), json=payload, timeout=10).raise_for_status()
    except Exception as e:
        logger.error(f"Telegram sendMessage failed (chat {chat_id}): {e}")


def send_photo(chat_id: int, image: io.BytesIO, caption: Optional[str] = None) -> None:
    image.seek(0)
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    try:
        requests.post(
            _url("sendPhoto"),
            data=data,
            files={"photo": ("card.png", image, "image/png")},
            timeout=20,
        ).raise_for_status()
    except Exception as e:
        logger.error(f"Telegram sendPhoto failed (chat {chat_id}): {e}")

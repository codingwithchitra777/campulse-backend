"""Thin Telegram Bot API sender over `requests` (same approach as
endpoints/deploy.py). Used to reply to webhook updates. All failures are
logged and swallowed — never raise into the webhook handler, or Telegram
would retry the whole update."""
import io
import logging
from typing import Optional, Union
import time

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
        resp = requests.post(_url("sendMessage"), json=payload, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        if e.response and e.response.status_code == 429:
            retry_after = e.response.json().get("parameters", {}).get("retry_after", 3)
            logger.warning(f"Rate limited (429). Retry after {retry_after}s for chat {chat_id}")
            time.sleep(retry_after)
            return send_message(chat_id, text, parse_mode)
        logger.error(f"Telegram sendMessage failed (chat {chat_id}): {e}")
    except Exception as e:
        logger.error(f"Telegram sendMessage failed (chat {chat_id}): {e}")


def send_photo(chat_id: int, image: Union[io.BytesIO, str], caption: Optional[str] = None) -> Optional[str]:
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption
    try:
        if isinstance(image, str):
            data["photo"] = image
            resp = requests.post(_url("sendPhoto"), json=data, timeout=20)
        else:
            image.seek(0)
            resp = requests.post(
                _url("sendPhoto"),
                data=data,
                files={"photo": ("card.png", image, "image/png")},
                timeout=20,
            )
        resp.raise_for_status()
        
        result = resp.json().get("result", {})
        photos = result.get("photo", [])
        if photos:
            return photos[-1].get("file_id")
        return None
    except requests.exceptions.HTTPError as e:
        if e.response and e.response.status_code == 429:
            retry_after = e.response.json().get("parameters", {}).get("retry_after", 3)
            logger.warning(f"Rate limited (429). Retry after {retry_after}s for chat {chat_id}")
            time.sleep(retry_after)
            return send_photo(chat_id, image, caption)
        logger.error(f"Telegram sendPhoto failed (chat {chat_id}): {e}")
        return None
    except Exception as e:
        logger.error(f"Telegram sendPhoto failed (chat {chat_id}): {e}")
        return None

import logging
import threading

from fastapi import APIRouter, Request, Header, HTTPException

from app.core.config import settings
from app.services.telegram_bot import TelegramBotService

logger = logging.getLogger(__name__)
router = APIRouter()


def _handle_gold_image(user_id: str, chat_id: int, full_name: str, file_id: str, caption: str) -> None:
    # Runs off the request thread: OCR takes ~1–2s and shouldn't stall the webhook.
    try:
        TelegramBotService().handle_gold_image(user_id, chat_id, full_name, file_id, caption)
    except Exception as e:
        logger.error(f"Telegram gold-image handler error: {e}", exc_info=True)


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    # Telegram echoes the secret set via setWebhook(secret_token=...) in this header.
    if not settings.telegram_webhook_secret or \
            x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=403, detail="Forbidden")

    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    message = update.get("message") or update.get("edited_message") or {}
    frm = message.get("from") or {}
    chat = message.get("chat") or {}
    if not frm.get("id") or not chat.get("id"):
        return {"ok": True}

    full_name = " ".join(filter(None, [frm.get("first_name"), frm.get("last_name")])) or "Telegram User"
    user_id = str(frm["id"])
    chat_id = int(chat["id"])

    # Photo → admin gold-board OCR. Offload so the webhook acks immediately
    # (Telegram retries on a slow/non-200 response).
    photo = message.get("photo")
    if photo:
        file_id = (photo[-1] or {}).get("file_id")
        if file_id:
            threading.Thread(
                target=_handle_gold_image,
                args=(user_id, chat_id, full_name, file_id, message.get("caption") or ""),
                daemon=True,
            ).start()
        return {"ok": True}

    # Otherwise, plain text commands; ack everything else so Telegram won't retry.
    text = message.get("text")
    if not text:
        return {"ok": True}
    try:
        TelegramBotService().dispatch(user_id, chat_id, full_name, text)
    except Exception as e:
        logger.error(f"Telegram webhook dispatch error: {e}", exc_info=True)

    return {"ok": True}

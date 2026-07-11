import logging
from fastapi import APIRouter, Request, Header, HTTPException

from app.core.config import settings
from app.services.telegram_bot import TelegramBotService

logger = logging.getLogger(__name__)
router = APIRouter()


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

    # Only handle plain text messages; ack everything else so Telegram won't retry.
    message = update.get("message") or update.get("edited_message") or {}
    text = message.get("text")
    frm = message.get("from") or {}
    chat = message.get("chat") or {}
    if not text or not frm.get("id") or not chat.get("id"):
        return {"ok": True}

    full_name = " ".join(filter(None, [frm.get("first_name"), frm.get("last_name")])) or "Telegram User"
    try:
        TelegramBotService().dispatch(str(frm["id"]), int(chat["id"]), full_name, text)
    except Exception as e:
        logger.error(f"Telegram webhook dispatch error: {e}", exc_info=True)

    return {"ok": True}

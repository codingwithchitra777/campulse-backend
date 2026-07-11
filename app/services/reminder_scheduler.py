"""Daily trading-session reminder broadcasts, ported from campulse-bot's
job_queue. Runs as a background daemon thread (same pattern as PricingService)
so no external scheduler is needed on the single-instance FastAPI deploy.

Fires at 08:00 and 14:00 Asia/Phnom_Penh, broadcasting to every user with a
stored chat_id. Dormant unless TELEGRAM_WEBHOOK_SECRET is set, so it never runs
in tests/local by accident."""
import time
import logging
import threading
from datetime import datetime, timedelta

import pytz

from app.core.config import settings

logger = logging.getLogger(__name__)

PHNOM_PENH_TZ = pytz.timezone("Asia/Phnom_Penh")
# (hour, message) — 1h before session start / end.
REMINDERS = [
    (8, "\U0001F680 Trading session starts in 1 hour!"),
    (14, "\U0001F3C1 Trading session ends in 1 hour!"),
]

_started = False
_lock = threading.Lock()


def _seconds_until_next(now: datetime):
    """Return (delay_seconds, message) for the soonest upcoming reminder."""
    best = None
    for hour, text in REMINDERS:
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        if best is None or delay < best[0]:
            best = (delay, text)
    return best


def _loop():
    from app.services.telegram_bot import TelegramBotService

    logger.info("Telegram reminder scheduler started.")
    while True:
        delay, text = _seconds_until_next(datetime.now(PHNOM_PENH_TZ))
        time.sleep(max(1, delay))
        try:
            count = TelegramBotService().broadcast(text)
            logger.info(f"Sent session reminder to {count} users.")
        except Exception as e:
            logger.error(f"Reminder broadcast failed: {e}", exc_info=True)
        # Avoid re-firing within the same minute before the clock advances.
        time.sleep(60)


def start_reminder_scheduler():
    """Idempotently start the daemon thread. No-op unless the webhook secret is
    configured (i.e. the Telegram bot is actually deployed)."""
    global _started
    if not settings.telegram_webhook_secret:
        return
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True).start()
        _started = True

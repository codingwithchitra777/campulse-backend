"""Price alerts: notify a user via their linked Telegram when a symbol crosses
a target. One background daemon thread polls active alerts (same pattern as the
reminder scheduler); dormant unless the Telegram bot is configured.

Delivery needs a chat_id, which only Telegram-linked users have — a Google
account resolves to a linked Telegram alias's chat_id. Crossed-but-undeliverable
alerts stay active so they fire once the user links Telegram."""
import time
import logging
import threading
from decimal import Decimal
from typing import Optional, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 90
_started = False
_lock = threading.Lock()


def resolve_chat_id(user_id: str, user_repo, link_repo) -> Optional[int]:
    """The user's own chat_id, or the first linked Telegram alias's chat_id."""
    u = user_repo.get_user(user_id)
    if u and u.get("chat_id"):
        return int(u["chat_id"])
    for link in link_repo.list_links(user_id):
        cid = link.get("chatId")
        if cid:
            return int(cid)
    return None


def _crossed(direction: str, price: Decimal, target: Decimal) -> bool:
    return price >= target if direction == "above" else price <= target


def _format(alert: dict, price: Decimal) -> str:
    cur = (alert.get("currency") or "KHR").upper()
    unit = "$" if cur == "USD" else "៛"
    arrow = "▲" if alert["direction"] == "above" else "▼"
    tgt = alert["targetPrice"]
    
    if cur == "KHR":
        tgt_str = f"{int(tgt):,}"
        price_str = f"{int(price):,}"
    else:
        tgt_str = f"{tgt:,.2f}"
        price_str = f"{price:,.2f}"
        
    return (f"🔔 Price alert: {alert['symbol']} {arrow} {alert['direction']} "
            f"{unit}{tgt_str}\nNow: {unit}{price_str}")


class AlertService:
    def __init__(self, alert_repo, user_repo, link_repo, price_router, send_message: Callable[[int, str], None]):
        self.alert_repo = alert_repo
        self.user_repo = user_repo
        self.link_repo = link_repo
        self.price_router = price_router
        self.send_message = send_message

    def check_once(self) -> int:
        """One pass over active alerts. Returns how many were delivered+closed."""
        alerts = self.alert_repo.list_active()
        price_cache: dict = {}
        delivered = 0
        for a in alerts:
            key = (a["market"], a["symbol"])
            if key not in price_cache:
                res = self.price_router.get_latest_price(a["market"], a["symbol"])
                price_cache[key] = res
            
            res = price_cache[key]
            if res.price is None or (res.raw and res.raw.get("fallback")):
                continue
                
            price = Decimal(str(res.price))
            target = Decimal(str(a["targetPrice"]))
            if not _crossed(a["direction"], price, target):
                continue
            chat_id = resolve_chat_id(a["userId"], self.user_repo, self.link_repo)
            if chat_id is None:
                continue  # leave active; fires once the user links Telegram
            try:
                self.send_message(chat_id, _format(a, price))
                self.alert_repo.mark_triggered(a["alertId"])
                delivered += 1
            except Exception as e:
                logger.error(f"Alert delivery failed for {a['alertId']}: {e}")
        return delivered


def _loop():
    from app.repositories.alert import AlertRepository
    from app.repositories.user import UserRepository
    from app.repositories.link import LinkRepository
    from app.services.price_providers import price_router
    from app.services import telegram_client

    svc = AlertService(AlertRepository(), UserRepository(), LinkRepository(),
                       price_router, telegram_client.send_message)
    logger.info("Price-alert scheduler started.")
    while True:
        try:
            n = svc.check_once()
            if n:
                logger.info(f"Delivered {n} price alert(s).")
        except Exception as e:
            logger.error(f"Alert check failed: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_alert_scheduler():
    """Idempotently start the daemon thread. No-op unless the Telegram bot is
    configured (webhook secret set), so it stays dormant in tests/local."""
    global _started
    if not settings.telegram_webhook_secret:
        return
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True).start()
        _started = True

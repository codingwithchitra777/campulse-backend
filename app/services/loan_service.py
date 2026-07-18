"""Personal loan ledger — Telegram receipts + due-date reminders.

Two Telegram touch-points, both best-effort (never block the API, dormant unless
the bot is configured, silent when the user has no linked chat):

  1. On a recorded repayment, a **forwardable receipt** is sent to the user so
     they can pass it to the other party as acknowledgement (user's request).
  2. A daemon reminds about loans whose due date is near/overdue, once a day,
     mirroring alert_service / corporate_action_service.
"""
import time
import logging
import threading
from datetime import date
from decimal import Decimal
from typing import Any, Dict, Optional

from app.core.config import settings
from app.services.markets import format_money
from app.services.alert_service import resolve_chat_id

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 6 * 60 * 60   # a due date moves at most once a day
REMIND_WITHIN_DAYS = 3
_started = False
_lock = threading.Lock()


def _fmt_date(d) -> str:
    return d.strftime("%d %b %Y") if isinstance(d, date) else str(d)


def format_receipt(loan: Dict[str, Any], repayment: Dict[str, Any]) -> str:
    """A neutral, forward-ready acknowledgement of one repayment."""
    cur = loan["currency"]
    amt = format_money(repayment["amount"], cur)
    who = loan["counterparty"]
    when = _fmt_date(repayment["paidDate"])
    if loan["direction"] == "lent":
        head = f"{who} repaid {amt} on {when}."
    else:
        head = f"Repaid {amt} to {who} on {when}."
    if loan["status"] == "settled":
        tail = "✅ Fully settled. Thank you!"
    else:
        tail = (f"Outstanding: {format_money(loan['outstanding'], cur)} "
                f"of {format_money(loan['principal'], cur)}.")
    return f"✅ Repayment recorded\n\n{head}\n{tail}\n— via CamPulse"


def format_reminder(loan: Dict[str, Any]) -> str:
    cur = loan["currency"]
    who = loan["counterparty"]
    outstanding = format_money(loan["outstanding"], cur)
    due = loan["dueDate"]
    days = (due - date.today()).days if isinstance(due, date) else None
    if days is not None and days < 0:
        when = f"was due {_fmt_date(due)} ({-days}d overdue)"
    elif days == 0:
        when = "is due today"
    else:
        when = f"is due {_fmt_date(due)}" + (f" (in {days}d)" if days else "")
    if loan["direction"] == "lent":
        head = f"{who} owes you {outstanding}"
    else:
        head = f"You owe {who} {outstanding}"
    return f"⏰ Loan reminder\n\n{head} — {when}.\n— via CamPulse"


def send_repayment_receipt(loan: Dict[str, Any], repayment: Dict[str, Any],
                           user_id: str, user_repo, link_repo) -> bool:
    """Best-effort: returns whether a message was dispatched. Uses the module-level
    telegram_client.send_message so tests can monkeypatch it."""
    from app.services import telegram_client
    chat_id = resolve_chat_id(user_id, user_repo, link_repo)
    if chat_id is None:
        return False
    try:
        telegram_client.send_message(chat_id, format_receipt(loan, repayment))
        return True
    except Exception as e:
        logger.error(f"Loan receipt failed for {user_id}: {e}")
        return False


class LoanReminderService:
    def __init__(self, loan_repo, user_repo, link_repo, send_message):
        self.loan_repo = loan_repo
        self.user_repo = user_repo
        self.link_repo = link_repo
        self.send_message = send_message

    def check_once(self) -> int:
        sent = 0
        for loan in self.loan_repo.due_soon(REMIND_WITHIN_DAYS):
            chat_id = resolve_chat_id(loan["userId"], self.user_repo, self.link_repo)
            if chat_id is None:
                continue  # no linked chat; try again once they link Telegram
            try:
                self.send_message(chat_id, format_reminder(loan))
                self.loan_repo.mark_reminded(loan["loanId"])
                sent += 1
            except Exception as e:
                logger.error(f"Loan reminder failed for {loan['loanId']}: {e}")
        return sent


def _loop():
    from app.repositories.loan import LoanRepository
    from app.repositories.user import UserRepository
    from app.repositories.link import LinkRepository
    from app.services import telegram_client

    svc = LoanReminderService(LoanRepository(), UserRepository(), LinkRepository(),
                              telegram_client.send_message)
    logger.info("Loan-reminder scheduler started.")
    while True:
        try:
            n = svc.check_once()
            if n:
                logger.info(f"Sent {n} loan reminder(s).")
        except Exception as e:
            logger.error(f"Loan reminder check failed: {e}", exc_info=True)
        time.sleep(CHECK_INTERVAL_SECONDS)


def start_loan_reminder_scheduler():
    """Idempotent daemon start; dormant unless the Telegram bot is configured
    (same gate as the other schedulers, so tests/local never touch prod)."""
    global _started
    if not settings.telegram_webhook_secret:
        return
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, daemon=True).start()
        _started = True

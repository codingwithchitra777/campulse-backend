"""Personal loan ledger. Money-touching, so the core rules are asserted:
outstanding = principal - Σ repayments, status transitions open→partial→settled,
currencies never blended, and the Telegram repayment receipt is forwardable.

Runs on disposable pytest_ users, deleted by exact id on teardown (the shared DB
is prod) — loans/repayments cascade off the user row.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.db.database import get_db
from app.repositories.user import UserRepository
from app.repositories.link import LinkRepository
from app.repositories.loan import LoanRepository
from app.services import loan_service


@pytest.fixture
def uid():
    u = f"pytest_{uuid.uuid4().hex[:12]}"
    UserRepository().upsert_user(u, "Loan User")
    yield u
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (u,))  # cascades loans


def _repo():
    return LoanRepository()


def test_create_lent_loan_starts_open_with_full_outstanding(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "Sok", Decimal("500"), "USD", date.today(), None, None)
    assert loan["status"] == "open"
    assert loan["outstanding"] == Decimal("500")
    assert loan["repaid"] == Decimal("0")
    assert loan["direction"] == "lent"


def test_partial_then_full_repayment_transitions_status(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "Dara", Decimal("550"), "USD", date.today(), None, None)
    lid = loan["loanId"]

    repo.add_repayment(lid, Decimal("200"), date.today(), None)
    loan = repo.get(lid, uid)
    assert loan["status"] == "partial"
    assert loan["outstanding"] == Decimal("350")

    repo.add_repayment(lid, Decimal("350"), date.today(), None)
    loan = repo.get(lid, uid)
    assert loan["status"] == "settled"
    assert loan["outstanding"] == Decimal("0")


def test_overpayment_settles_and_never_goes_negative(uid):
    repo = _repo()
    loan = repo.create(uid, "borrowed", "Bank", Decimal("100"), "USD", date.today(), None, None)
    repo.add_repayment(loan["loanId"], Decimal("120"), date.today(), None)
    loan = repo.get(loan["loanId"], uid)
    assert loan["status"] == "settled"
    assert loan["outstanding"] == Decimal("0")  # floored, not -20


def test_deleting_a_repayment_recomputes_status(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "Nita", Decimal("300"), "USD", date.today(), None, None)
    lid = loan["loanId"]
    r = repo.add_repayment(lid, Decimal("300"), date.today(), None)
    assert repo.get(lid, uid)["status"] == "settled"

    assert repo.delete_repayment(lid, r["repaymentId"]) is True
    loan = repo.get(lid, uid)
    assert loan["status"] == "open"
    assert loan["outstanding"] == Decimal("300")


def test_summary_never_blends_currencies(uid):
    repo = _repo()
    repo.create(uid, "lent", "A", Decimal("500"), "USD", date.today(), None, None)
    repo.create(uid, "lent", "B", Decimal("2000000"), "KHR", date.today(), None, None)
    rows = {(r["direction"], r["currency"]): r for r in repo.summary(uid)}
    assert rows[("lent", "USD")]["outstanding"] == Decimal("500")
    assert rows[("lent", "KHR")]["outstanding"] == Decimal("2000000")
    assert len(rows) == 2  # two separate rows, not one summed number


def test_summary_splits_lent_and_borrowed(uid):
    repo = _repo()
    repo.create(uid, "lent", "A", Decimal("500"), "USD", date.today(), None, None)
    repo.create(uid, "borrowed", "C", Decimal("300"), "USD", date.today(), None, None)
    rows = {(r["direction"], r["currency"]): r for r in repo.summary(uid)}
    assert rows[("lent", "USD")]["outstanding"] == Decimal("500")
    assert rows[("borrowed", "USD")]["outstanding"] == Decimal("300")


def test_list_filters_by_direction_and_status(uid):
    repo = _repo()
    repo.create(uid, "lent", "A", Decimal("500"), "USD", date.today(), None, None)
    b = repo.create(uid, "borrowed", "C", Decimal("300"), "USD", date.today(), None, None)
    repo.add_repayment(b["loanId"], Decimal("300"), date.today(), None)

    assert all(l["direction"] == "lent" for l in repo.list_for_user(uid, direction="lent"))
    settled = repo.list_for_user(uid, status="settled")
    assert len(settled) == 1 and settled[0]["loanId"] == b["loanId"]


def test_delete_loan_is_user_scoped(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "A", Decimal("500"), "USD", date.today(), None, None)
    assert repo.delete(loan["loanId"], "pytest_someone_else") is False
    assert repo.delete(loan["loanId"], uid) is True
    assert repo.get(loan["loanId"], uid) is None


# --- Telegram repayment receipt ---

def test_receipt_is_forwardable_and_shows_outstanding(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "Sok", Decimal("550"), "USD", date.today(), None, None)
    repo.add_repayment(loan["loanId"], Decimal("200"), date(2026, 7, 18), None)
    loan = repo.get(loan["loanId"], uid)
    repayment = {"amount": Decimal("200"), "paidDate": date(2026, 7, 18)}

    msg = loan_service.format_receipt(loan, repayment)
    assert "Sok repaid $200.00 on 18 Jul 2026." in msg
    assert "Outstanding: $350.00 of $550.00." in msg


def test_receipt_marks_full_settlement(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "Sok", Decimal("200"), "USD", date.today(), None, None)
    repo.add_repayment(loan["loanId"], Decimal("200"), date(2026, 7, 18), None)
    loan = repo.get(loan["loanId"], uid)
    msg = loan_service.format_receipt(loan, {"amount": Decimal("200"), "paidDate": date(2026, 7, 18)})
    assert "Fully settled" in msg


def test_send_receipt_noop_without_linked_chat(uid, monkeypatch):
    sent = []
    from app.services import telegram_client
    monkeypatch.setattr(telegram_client, "send_message", lambda c, t: sent.append((c, t)))
    repo = _repo()
    loan = repo.create(uid, "lent", "Sok", Decimal("100"), "USD", date.today(), None, None)
    # uid has no chat_id and no link => nothing dispatched.
    ok = loan_service.send_repayment_receipt(
        loan, {"amount": Decimal("50"), "paidDate": date.today()},
        uid, UserRepository(), LinkRepository())
    assert ok is False
    assert sent == []


def test_send_receipt_dispatches_when_chat_linked(uid, monkeypatch):
    sent = []
    from app.services import telegram_client
    monkeypatch.setattr(telegram_client, "send_message", lambda c, t: sent.append((c, t)))
    # Give the user a chat_id directly (resolve_chat_id reads users.chat_id first).
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET chat_id = %s WHERE user_id = %s", (987654, uid))
    repo = _repo()
    loan = repo.create(uid, "lent", "Sok", Decimal("100"), "USD", date.today(), None, None)
    repo.add_repayment(loan["loanId"], Decimal("40"), date.today(), None)
    loan = repo.get(loan["loanId"], uid)
    ok = loan_service.send_repayment_receipt(
        loan, {"amount": Decimal("40"), "paidDate": date.today()},
        uid, UserRepository(), LinkRepository())
    assert ok is True
    assert len(sent) == 1 and sent[0][0] == 987654


# --- due-date reminders ---

def test_due_soon_picks_up_near_and_overdue_unsettled(uid):
    repo = _repo()
    soon = repo.create(uid, "lent", "Soon", Decimal("100"), "USD",
                       date.today(), date.today() + timedelta(days=2), None)
    repo.create(uid, "lent", "Later", Decimal("100"), "USD",
                date.today(), date.today() + timedelta(days=30), None)
    settled = repo.create(uid, "lent", "Paid", Decimal("100"), "USD",
                          date.today(), date.today() + timedelta(days=1), None)
    repo.add_repayment(settled["loanId"], Decimal("100"), date.today(), None)

    due_ids = {l["loanId"] for l in repo.due_soon(3)}
    assert soon["loanId"] in due_ids
    assert settled["loanId"] not in due_ids  # settled excluded


def test_mark_reminded_prevents_same_day_repeat(uid):
    repo = _repo()
    loan = repo.create(uid, "lent", "Soon", Decimal("100"), "USD",
                       date.today(), date.today() + timedelta(days=1), None)
    assert loan["loanId"] in {l["loanId"] for l in repo.due_soon(3)}
    repo.mark_reminded(loan["loanId"])
    assert loan["loanId"] not in {l["loanId"] for l in repo.due_soon(3)}

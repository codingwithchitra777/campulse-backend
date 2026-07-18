"""Personal loan ledger repo — money lent to / borrowed from a person.

Kept deliberately apart from trades/allocations: a loan never feeds P/L or the
equity curve. Outstanding is always computed (principal − Σ repayments), never
stored; `status` is a derived cache recomputed after any repayment change.
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.db.database import get_db


def _derive_status(principal: Decimal, repaid: Decimal) -> str:
    if repaid >= principal:
        return "settled"
    return "partial" if repaid > 0 else "open"


def _loan_row(r) -> Dict[str, Any]:
    principal = Decimal(r[4])
    repaid = Decimal(r[11] or 0)
    outstanding = principal - repaid
    return {
        "loanId": r[0],
        "direction": r[1],
        "counterparty": r[2],
        "principal": principal,
        "currency": r[3],
        "loanDate": r[5],
        "dueDate": r[6],
        "note": r[7],
        "status": r[8],
        "createdAt": r[9],
        "userId": r[10],
        "repaid": repaid,
        # Floor at 0 for display: an over-payment shouldn't read as negative debt.
        "outstanding": outstanding if outstanding > 0 else Decimal(0),
    }


# principal is column 4 so _loan_row can read it; repaid is the trailing agg col.
_SELECT = """
    SELECT l.loan_id, l.direction, l.counterparty, l.currency, l.principal,
           l.loan_date, l.due_date, l.note, l.status, l.created_at, l.user_id,
           COALESCE(r.repaid, 0)
    FROM loans l
    LEFT JOIN (SELECT loan_id, SUM(amount) AS repaid
               FROM loan_repayments GROUP BY loan_id) r ON r.loan_id = l.loan_id
"""


class LoanRepository:
    def create(self, user_id: str, direction: str, counterparty: str,
               principal: Decimal, currency: str, loan_date: date,
               due_date: Optional[date], note: Optional[str]) -> Dict[str, Any]:
        loan_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO loans (loan_id, user_id, direction, counterparty,
                                       principal, currency, loan_date, due_date, note, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
                    """,
                    (loan_id, user_id, direction, counterparty, principal, currency,
                     loan_date, due_date, note)
                )
        return self.get(loan_id, user_id)

    def get(self, loan_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT + " WHERE l.loan_id = %s AND l.user_id = %s",
                            (loan_id, user_id))
                r = cur.fetchone()
                return _loan_row(r) if r else None

    def list_for_user(self, user_id: str, direction: Optional[str] = None,
                      status: Optional[str] = None) -> List[Dict[str, Any]]:
        query = _SELECT + " WHERE l.user_id = %s"
        params: List[Any] = [user_id]
        if direction:
            query += " AND l.direction = %s"
            params.append(direction)
        if status:
            query += " AND l.status = %s"
            params.append(status)
        query += " ORDER BY (l.status = 'settled'), l.due_date NULLS LAST, l.loan_date DESC"
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(params))
                return [_loan_row(r) for r in cur.fetchall()]

    def summary(self, user_id: str) -> List[Dict[str, Any]]:
        """Per (direction, currency) outstanding + open-loan count. Currencies
        are never blended — a $ loan and a ៛ loan stay on separate rows."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT l.direction, l.currency,
                           SUM(l.principal) - COALESCE(SUM(r.repaid), 0) AS outstanding,
                           SUM(CASE WHEN l.status <> 'settled' THEN 1 ELSE 0 END) AS open_count
                    FROM loans l
                    LEFT JOIN (SELECT loan_id, SUM(amount) AS repaid
                               FROM loan_repayments GROUP BY loan_id) r ON r.loan_id = l.loan_id
                    WHERE l.user_id = %s
                    GROUP BY l.direction, l.currency
                    """,
                    (user_id,)
                )
                out = []
                for direction, currency, outstanding, open_count in cur.fetchall():
                    o = Decimal(outstanding or 0)
                    out.append({
                        "direction": direction,
                        "currency": currency,
                        "outstanding": o if o > 0 else Decimal(0),
                        "openCount": int(open_count or 0),
                    })
                return out

    def delete(self, loan_id: str, user_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM loans WHERE loan_id = %s AND user_id = %s",
                            (loan_id, user_id))
                return cur.rowcount > 0

    def list_repayments(self, loan_id: str) -> List[Dict[str, Any]]:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT repayment_id, amount, paid_date, note, created_at
                       FROM loan_repayments WHERE loan_id = %s
                       ORDER BY paid_date ASC, created_at ASC""",
                    (loan_id,)
                )
                return [
                    {"repaymentId": r[0], "amount": Decimal(r[1]), "paidDate": r[2],
                     "note": r[3], "createdAt": r[4]}
                    for r in cur.fetchall()
                ]

    def add_repayment(self, loan_id: str, amount: Decimal, paid_date: date,
                      note: Optional[str]) -> Optional[Dict[str, Any]]:
        """Insert a repayment and recompute the loan's status atomically. Caller
        must have verified the loan belongs to the user."""
        repayment_id = str(uuid.uuid4())
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT principal FROM loans WHERE loan_id = %s FOR UPDATE",
                            (loan_id,))
                row = cur.fetchone()
                if not row:
                    return None
                cur.execute(
                    """INSERT INTO loan_repayments (repayment_id, loan_id, amount, paid_date, note)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (repayment_id, loan_id, amount, paid_date, note)
                )
                self._recompute_status(cur, loan_id, Decimal(row[0]))
                return {"repaymentId": repayment_id, "amount": amount,
                        "paidDate": paid_date, "note": note}

    def delete_repayment(self, loan_id: str, repayment_id: str) -> bool:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT principal FROM loans WHERE loan_id = %s FOR UPDATE",
                            (loan_id,))
                row = cur.fetchone()
                if not row:
                    return False
                cur.execute(
                    "DELETE FROM loan_repayments WHERE repayment_id = %s AND loan_id = %s",
                    (repayment_id, loan_id)
                )
                deleted = cur.rowcount > 0
                if deleted:
                    self._recompute_status(cur, loan_id, Decimal(row[0]))
                return deleted

    def _recompute_status(self, cur, loan_id: str, principal: Decimal) -> None:
        cur.execute("SELECT COALESCE(SUM(amount), 0) FROM loan_repayments WHERE loan_id = %s",
                    (loan_id,))
        repaid = Decimal(cur.fetchone()[0])
        cur.execute("UPDATE loans SET status = %s WHERE loan_id = %s",
                    (_derive_status(principal, repaid), loan_id))

    # --- reminders (daemon) ---

    def due_soon(self, within_days: int) -> List[Dict[str, Any]]:
        """Unsettled loans with a due date within `within_days` (including
        overdue) not already reminded today. Cross-user — the daemon's queue."""
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _SELECT + """
                    WHERE l.status <> 'settled'
                      AND l.due_date IS NOT NULL
                      AND l.due_date <= CURRENT_DATE + %s
                      AND (l.last_reminded_date IS NULL OR l.last_reminded_date <> CURRENT_DATE)
                    """,
                    (within_days,)
                )
                return [_loan_row(r) for r in cur.fetchall()]

    def mark_reminded(self, loan_id: str) -> None:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE loans SET last_reminded_date = CURRENT_DATE WHERE loan_id = %s",
                            (loan_id,))

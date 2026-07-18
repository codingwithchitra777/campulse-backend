import logging
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.loans import LoanCreate, RepaymentCreate
from app.services import loan_service
from app.services.alert_service import resolve_chat_id
from app.api.deps import (get_current_user, get_loan_repo, get_user_repo, get_link_repo)

logger = logging.getLogger(__name__)
router = APIRouter()

_DIRECTIONS = ("lent", "borrowed")
_STATUSES = ("open", "partial", "settled")
_CURRENCIES = ("KHR", "USD")


@router.get("/loans")
def list_loans(
    direction: str = None,
    status: str = None,
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
    user_repo=Depends(get_user_repo),
    link_repo=Depends(get_link_repo),
):
    """The user's loans. `deliverable` is false when no linked Telegram exists,
    so the UI can nudge the user to connect one for repayment receipts."""
    try:
        if direction and direction not in _DIRECTIONS:
            raise HTTPException(status_code=400, detail="Invalid direction")
        if status and status not in _STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status")
        items = loan_repo.list_for_user(current_user.user_id, direction, status)
        deliverable = resolve_chat_id(current_user.user_id, user_repo, link_repo) is not None
        return {"items": items, "deliverable": deliverable}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in list_loans: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/loans/summary")
def loans_summary(
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
):
    """Per (direction, currency) outstanding totals — never blended across currencies."""
    try:
        return {"items": loan_repo.summary(current_user.user_id)}
    except Exception as e:
        logger.error(f"Error in loans_summary: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/loans")
def create_loan(
    req: LoanCreate,
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
):
    try:
        counterparty = req.counterparty.strip()
        if not counterparty:
            raise HTTPException(status_code=400, detail="Counterparty is required")
        if req.principal is None or req.principal <= 0:
            raise HTTPException(status_code=400, detail="Principal must be positive")
        currency = (req.currency or "KHR").upper()
        if currency not in _CURRENCIES:
            raise HTTPException(status_code=400, detail="Unsupported currency")
        loan_date = req.loanDate or date.today()
        if req.dueDate and req.dueDate < loan_date:
            raise HTTPException(status_code=400, detail="Due date is before the loan date")
        loan = loan_repo.create(current_user.user_id, req.direction, counterparty,
                                req.principal, currency, loan_date, req.dueDate, req.note)
        return {"success": True, "loan": loan}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in create_loan: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/loans/{loan_id}")
def delete_loan(
    loan_id: str,
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
):
    try:
        if not loan_repo.delete(loan_id, current_user.user_id):
            raise HTTPException(status_code=404, detail="Loan not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_loan: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/loans/{loan_id}/repayments")
def add_repayment(
    loan_id: str,
    req: RepaymentCreate,
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
    user_repo=Depends(get_user_repo),
    link_repo=Depends(get_link_repo),
):
    """Record a repayment and send the user a forwardable Telegram receipt."""
    try:
        if req.amount is None or req.amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
        # Ownership check before mutating.
        loan = loan_repo.get(loan_id, current_user.user_id)
        if not loan:
            raise HTTPException(status_code=404, detail="Loan not found")
        paid_date = req.paidDate or date.today()
        repayment = loan_repo.add_repayment(loan_id, req.amount, paid_date, req.note)
        if repayment is None:
            raise HTTPException(status_code=404, detail="Loan not found")
        loan = loan_repo.get(loan_id, current_user.user_id)  # refreshed outstanding/status
        receipt_sent = loan_service.send_repayment_receipt(
            loan, repayment, current_user.user_id, user_repo, link_repo)
        return {"success": True, "loan": loan, "repayment": repayment,
                "receiptSent": receipt_sent}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in add_repayment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/loans/{loan_id}/repayments")
def list_repayments(
    loan_id: str,
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
):
    try:
        if not loan_repo.get(loan_id, current_user.user_id):
            raise HTTPException(status_code=404, detail="Loan not found")
        return {"items": loan_repo.list_repayments(loan_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in list_repayments: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/loans/{loan_id}/repayments/{repayment_id}")
def delete_repayment(
    loan_id: str,
    repayment_id: str,
    current_user=Depends(get_current_user),
    loan_repo=Depends(get_loan_repo),
):
    try:
        if not loan_repo.get(loan_id, current_user.user_id):
            raise HTTPException(status_code=404, detail="Loan not found")
        if not loan_repo.delete_repayment(loan_id, repayment_id):
            raise HTTPException(status_code=404, detail="Repayment not found")
        return {"success": True, "loan": loan_repo.get(loan_id, current_user.user_id)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_repayment: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

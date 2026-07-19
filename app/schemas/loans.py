from datetime import date
from decimal import Decimal
from typing import Optional, Literal

from pydantic import BaseModel


class LoanCreate(BaseModel):
    direction: Literal["lent", "borrowed"]
    counterparty: str
    principal: Decimal
    currency: Optional[str] = None          # KHR (default) | USD
    loanDate: Optional[date] = None         # omitted => today
    dueDate: Optional[date] = None
    note: Optional[str] = None
    ratePct: Optional[Decimal] = None
    ratePeriod: Optional[str] = None
    termMonths: Optional[int] = None
    method: Optional[str] = None
    fixedPayment: Optional[Decimal] = None


class RepaymentCreate(BaseModel):
    amount: Decimal
    paidDate: Optional[date] = None         # omitted => today
    note: Optional[str] = None

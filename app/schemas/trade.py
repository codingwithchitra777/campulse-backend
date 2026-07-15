from pydantic import BaseModel, field_validator
from typing import Literal, Optional
from datetime import datetime, timezone
from decimal import Decimal

Side = Literal["BUY", "SELL"]

def _to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """The whole system stores naive-UTC datetimes (utcnow + TIMESTAMP column);
    an offset-aware value from an ISO string would break naive comparisons."""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value

class TradeCreate(BaseModel):
    ticker: str
    side: Side
    # Price/commission are Decimal so USD cents (and fractional gold) survive;
    # whole-riel CSX values coerce cleanly from JSON integers.
    price: Decimal
    qty: int
    commission: Optional[Decimal] = None
    orderDate: Optional[datetime] = None
    # Multi-market: omitted defaults to the CSX/riel behaviour (resolved in record_trade).
    market: Optional[str] = None
    currency: Optional[str] = None

    _normalize_order_date = field_validator("orderDate")(_to_naive_utc)

class TradeUpdate(BaseModel):
    ticker: str
    price: Decimal
    qty: int
    commission: Optional[Decimal] = None
    orderDate: Optional[datetime] = None

    _normalize_order_date = field_validator("orderDate")(_to_naive_utc)

class JournalUpdate(BaseModel):
    note: Optional[str] = None
    tags: Optional[str] = None

class Trade(BaseModel):
    tradeId: str
    userId: str
    seq: int
    ticker: str
    side: Side
    price: Decimal
    qty: int
    commission: Decimal = Decimal(0)
    orderDate: datetime
    market: str = "CSX"
    currency: str = "KHR"

from pydantic import BaseModel, field_validator
from typing import Literal, Optional
from datetime import datetime, timezone

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
    price: int
    qty: int
    commission: Optional[int] = None
    orderDate: Optional[datetime] = None
    # Multi-market: omitted defaults to the CSX/riel behaviour (resolved in record_trade).
    market: Optional[str] = None
    currency: Optional[str] = None

    _normalize_order_date = field_validator("orderDate")(_to_naive_utc)

class TradeUpdate(BaseModel):
    ticker: str
    price: int
    qty: int
    commission: Optional[int] = None
    orderDate: Optional[datetime] = None

    _normalize_order_date = field_validator("orderDate")(_to_naive_utc)

class Trade(BaseModel):
    tradeId: str
    userId: str
    seq: int
    ticker: str
    side: Side
    price: int
    qty: int
    commission: int = 0
    orderDate: datetime
    market: str = "CSX"
    currency: str = "KHR"

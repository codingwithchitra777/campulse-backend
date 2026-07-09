from pydantic import BaseModel
from typing import Literal, Optional
from datetime import datetime

Side = Literal["BUY", "SELL"]

class TradeCreate(BaseModel):
    ticker: str
    side: Side
    price: int
    qty: int
    commission: Optional[int] = None

class TradeUpdate(BaseModel):
    ticker: str
    price: int
    qty: int
    commission: Optional[int] = None

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

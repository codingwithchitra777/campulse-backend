from pydantic import BaseModel, Field
from datetime import datetime

class Allocation(BaseModel):
    allocId: str
    userId: str
    ticker: str
    sellTradeId: str
    buyTradeId: str
    qtyAllocated: int
    buyPrice: int
    buyCommission: int
    buyQty: int
    sellPrice: int
    sellCommission: int
    sellQty: int
    realisedPnl: int
    createdAt: datetime = Field(default_factory=datetime.utcnow)

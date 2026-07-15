from pydantic import BaseModel
from typing import Optional, Literal
from decimal import Decimal


class AlertCreate(BaseModel):
    symbol: str
    targetPrice: Decimal
    market: Optional[str] = None
    currency: Optional[str] = None
    # Omitted => inferred from the current price (above if target is higher).
    direction: Optional[Literal["above", "below"]] = None

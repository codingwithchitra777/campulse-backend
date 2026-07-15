from pydantic import BaseModel
from typing import Optional


class WatchlistAdd(BaseModel):
    symbol: str
    market: Optional[str] = None
    currency: Optional[str] = None

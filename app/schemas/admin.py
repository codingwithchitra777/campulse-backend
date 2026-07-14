from pydantic import BaseModel
from typing import Literal, Optional
from decimal import Decimal

Role = Literal["user", "admin"]

class RoleUpdateRequest(BaseModel):
    role: Role

class AdminStats(BaseModel):
    totalUsers: int
    totalTrades: int
    totalRealisedPnl: float

class ManualPriceRequest(BaseModel):
    """Set the admin board price for a manually-priced instrument (local gold).
    Defaults target the single local-gold instrument, XAU-KH in USD per chi."""
    price: Decimal
    market: str = "GOLD_KH"
    symbol: str = "XAU-KH"
    currency: str = "USD"
    change: Decimal = Decimal(0)

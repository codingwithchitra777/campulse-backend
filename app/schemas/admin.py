from pydantic import BaseModel
from typing import Literal, Optional
from decimal import Decimal
from datetime import date

Role = Literal["user", "admin"]

class RoleUpdateRequest(BaseModel):
    role: Role

class AdminStats(BaseModel):
    totalUsers: int
    totalTrades: int
    totalRealisedPnl: float

class CorporateActionCreate(BaseModel):
    """A bonus issue or forward split: ratioNew new shares per ratioHeld held
    (PPSP 1:1 bonus => ratioNew=1, ratioHeld=1). Applied to every holder's open
    lots on exDate by the corporate-action daemon."""
    market: str = "CSX"
    symbol: str
    actionType: Literal["bonus", "split"]
    ratioNew: int
    ratioHeld: int
    exDate: date
    note: Optional[str] = None


class ManualPriceRequest(BaseModel):
    """Set the admin board price for a manually-priced instrument (local gold).
    Defaults target the single local-gold instrument, XAU-KH in USD per chi."""
    price: Decimal
    bidPrice: Optional[Decimal] = None
    askPrice: Optional[Decimal] = None
    market: str = "GOLD_KH"
    symbol: str = "XAU-KH"
    currency: str = "USD"
    change: Decimal = Decimal(0)

class ExchangeRateCreate(BaseModel):
    baseCurrency: str = "USD"
    targetCurrency: str = "KHR"
    bidRate: float
    askRate: float
    effectiveDate: date

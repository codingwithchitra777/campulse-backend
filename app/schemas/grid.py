from pydantic import BaseModel
from typing import List, Literal, Optional

# Grid trading — request body for PUT /api/grid.
#
# CamPulse records data; it does not execute orders. A grid plan is a saved
# ladder of resting orders the user placed at their real broker. Each *fill*
# is recorded through the normal POST /api/trades/confirm (one trade per fill,
# matched cheapest-lot-first). This schema only validates the plan document
# that the web client (campulse-web GridService) persists so it follows the
# user across devices. Field names are camelCase to match the client payload,
# like the other schemas (see AlertCreate.targetPrice).

TradeSide = Literal["BUY", "SELL"]


class GridRung(BaseModel):
    id: int
    price: float
    side: TradeSide
    fills: int = 0


class GridFill(BaseModel):
    id: str
    side: TradeSide
    price: float
    qty: float
    pnl: float = 0.0
    running: float = 0.0
    at: str  # ISO datetime string, stamped client-side at fill time


class GridPlanBody(BaseModel):
    ticker: str
    market: Literal["CSX", "US", "GOLD_KH"]
    currency: Literal["KHR", "USD"]
    lower: float
    upper: float
    levels: int
    qty: float
    spacing: Literal["arith", "geo"]
    step: float
    rungs: List[GridRung]
    log: List[GridFill] = []
    realised: float = 0.0
    cycles: int = 0
    openLots: float = 0.0
    createdAt: str
    updatedAt: Optional[str] = None

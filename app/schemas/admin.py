from pydantic import BaseModel
from typing import Literal

Role = Literal["user", "admin"]

class RoleUpdateRequest(BaseModel):
    role: Role

class AdminStats(BaseModel):
    totalUsers: int
    totalTrades: int
    totalRealisedPnl: float

"""Manual price provider for instruments with no live feed — local Cambodian
gold (market == "GOLD_KH"). Prices come from the admin-maintained board stored
in `manual_prices`; unset symbols resolve to price=None (valued at cost)."""
from __future__ import annotations

from app.repositories.manual_price import ManualPriceRepository
from app.services.pricing import PriceResult


class ManualProvider:
    def __init__(self, market: str, repo: ManualPriceRepository = None):
        self.market = market
        self.repo = repo or ManualPriceRepository()

    def get_latest_price(self, symbol: str) -> PriceResult:
        symbol = symbol.upper()
        row = self.repo.get(self.market, symbol)
        if not row or row.get("price") is None:
            return PriceResult(ticker=symbol, price=None, raw={"error": "no manual price set"})
        change = row.get("change") or 0
        direction = "up" if change > 0 else "down" if change < 0 else "equal"
        return PriceResult(
            ticker=symbol,
            price=float(row["price"]),
            change=change,
            change_direction=direction,
            raw=row,
        )

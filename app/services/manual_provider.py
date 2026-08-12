"""Manual price provider for instruments with no live feed — local Cambodian
gold (market == "GOLD_KH"). Reads the latest `price_history` snapshot: the
same source `/gold`, the gold-OCR photo flow, `/setgold`, and the public
price-history feed all write to. (This used to read a separate `manual_prices`
board that only the old admin form wrote — that board goes stale the moment
anyone updates gold through Telegram/OCR instead, silently desyncing
portfolio valuation from the price shown everywhere else. Don't reintroduce
a second gold price source.)"""
from __future__ import annotations

from app.repositories.price_history import PriceHistoryRepository
from app.services.pricing import PriceResult


class ManualProvider:
    def __init__(self, market: str, repo: PriceHistoryRepository = None):
        self.market = market
        self.repo = repo or PriceHistoryRepository()

    def get_latest_price(self, symbol: str) -> PriceResult:
        symbol = symbol.upper()
        history = self.repo.get_history([symbol])
        latest = history[-1] if history else None  # get_history is ordered oldest -> newest
        if not latest or latest.get("price") is None:
            return PriceResult(ticker=symbol, price=None, raw={"error": "no price recorded yet"})
        return PriceResult(
            ticker=symbol,
            price=float(latest["price"]),
            change=0,
            change_direction="equal",
            raw=latest,
        )

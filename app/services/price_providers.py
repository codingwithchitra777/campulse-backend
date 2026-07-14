"""Price provider abstraction — the seam that lets each market resolve its own
prices while callers stay market-agnostic.

Phase 1 ships only the CSX provider (wrapping the existing PricingService); the
router therefore behaves identically to calling PricingService directly. Phase 2
registers a Finnhub provider for US equities, and Phase 3 a manual provider for
local gold, without touching any caller.
"""
from __future__ import annotations
from typing import Dict, List, Optional

from app.services.pricing import PricingService, PriceResult, pricing_service_instance
from app.services import markets


class CSXProvider:
    """Wraps the existing CSX PricingService behind the provider interface."""
    market = markets.CSX

    def __init__(self, pricing: PricingService):
        self.pricing = pricing

    def get_latest_price(self, symbol: str) -> PriceResult:
        return self.pricing.get_latest_price(symbol)

    def get_all_prices(self) -> List[dict]:
        return self.pricing.get_all_prices()


class PriceRouter:
    """Routes a (market, symbol) lookup to the provider registered for that market.
    Unknown markets fall back to the default (CSX) so pre-market data still resolves."""

    def __init__(self, providers: Dict[str, object], default_market: str = markets.CSX):
        self.providers = providers
        self.default_market = default_market

    def provider_for(self, market: Optional[str]):
        m = markets.normalize_market(market)
        return self.providers.get(m) or self.providers[self.default_market]

    def get_latest_price(self, market: Optional[str], symbol: str) -> PriceResult:
        return self.provider_for(market).get_latest_price(symbol)

    def get_all_prices(self, market: Optional[str] = markets.CSX) -> List[dict]:
        provider = self.provider_for(market)
        getter = getattr(provider, "get_all_prices", None)
        return getter() if getter else []


# Default router: CSX (native) + US (Finnhub). Phase 3 adds a manual gold provider.
# FinnhubProvider degrades to price=None when FINNHUB_API_KEY is unset, so this is
# safe to register unconditionally.
from app.services.finnhub_provider import FinnhubProvider  # noqa: E402  (avoid import cycle at module top)
from app.services.manual_provider import ManualProvider  # noqa: E402

price_router = PriceRouter({
    markets.CSX: CSXProvider(pricing_service_instance),
    markets.US: FinnhubProvider(),
    markets.GOLD_KH: ManualProvider(markets.GOLD_KH),
})

"""Market constants for the multi-market layer.

A trade belongs to a *market* and is denominated in a *currency*:
  - CSX     — Cambodia Securities Exchange equities, priced in KHR (riel).
  - US      — international/US equities via Finnhub, priced in USD (Phase 2).
  - GOLD_KH — local Cambodian gold, admin-priced board, quoted in USD (Phase 3).

Everything created before this layer existed is CSX/KHR, which is the default.
"""

CSX = "CSX"
US = "US"
GOLD_KH = "GOLD_KH"

DEFAULT_MARKET = CSX

# Native currency each market is denominated in.
MARKET_CURRENCY = {
    CSX: "KHR",
    US: "USD",
    GOLD_KH: "USD",
}


def normalize_market(market):
    m = (market or DEFAULT_MARKET).upper()
    return m if m in MARKET_CURRENCY else DEFAULT_MARKET


def default_currency(market):
    return MARKET_CURRENCY.get(normalize_market(market), "KHR")


def resolve_market_currency(market=None, currency=None):
    """Resolve a (market, currency) pair, filling in sensible defaults:
    unknown/None market -> CSX; currency defaults to the market's native one."""
    m = normalize_market(market)
    c = (currency or default_currency(m)).upper()
    return m, c

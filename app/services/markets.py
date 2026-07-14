"""Market constants for the multi-market layer.

A trade belongs to a *market* and is denominated in a *currency*:
  - CSX     — Cambodia Securities Exchange equities, priced in KHR (riel).
  - US      — international/US equities via Finnhub, priced in USD (Phase 2).
  - GOLD_KH — local Cambodian gold, admin-priced board, quoted in USD (Phase 3).

Everything created before this layer existed is CSX/KHR, which is the default.
"""

from decimal import Decimal, ROUND_HALF_UP

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


# --- Money precision ------------------------------------------------------
# How many decimal places each currency's minor unit uses. KHR (riel) is whole;
# USD has cents. Money is stored as NUMERIC and computed in Decimal, then
# quantized to the currency's precision so results are exact (no float drift).
CURRENCY_DP = {"KHR": 0, "USD": 2}


def money_precision(currency) -> int:
    return CURRENCY_DP.get((currency or "KHR").upper(), 2)


def quantize_money(value, currency) -> Decimal:
    """Round a Decimal (or number) to the currency's minor unit, half-up."""
    dp = money_precision(currency)
    q = Decimal(1) if dp == 0 else Decimal(1).scaleb(-dp)
    return Decimal(value).quantize(q, rounding=ROUND_HALF_UP)

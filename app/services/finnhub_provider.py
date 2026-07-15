"""Finnhub price provider for US/international equities (market == "US").

One quote per symbol (`GET /quote?symbol=AAPL`), cached ~45s to stay well under
the free tier's ~60 req/min. Degrades gracefully to price=None when no API key
is configured or the symbol is unknown, so the portfolio never crashes on it.
"""
from __future__ import annotations
import logging
from datetime import date, timedelta
from typing import List, Dict, Any

import requests
from cachetools import TTLCache

from app.core.config import settings
from app.services.pricing import PriceResult
from app.services import markets

logger = logging.getLogger(__name__)


class FinnhubProvider:
    market = markets.US

    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key if api_key is not None else settings.finnhub_api_key
        self.base_url = (base_url or settings.finnhub_base_url).rstrip("/")
        self.cache = TTLCache(maxsize=256, ttl=45)

    def get_latest_price(self, symbol: str) -> PriceResult:
        symbol = symbol.upper()
        if not self.api_key:
            return PriceResult(ticker=symbol, price=None, raw={"error": "FINNHUB_API_KEY not set"})
        if symbol in self.cache:
            return self.cache[symbol]

        try:
            r = requests.get(
                f"{self.base_url}/quote",
                params={"symbol": symbol, "token": self.api_key},
                timeout=4.0,
            )
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Finnhub quote error for {symbol}: {e}")
            return PriceResult(ticker=symbol, price=None, raw={"error": str(e)})

        res = self._parse_quote(symbol, data)
        self.cache[symbol] = res
        return res

    @staticmethod
    def _parse_quote(symbol: str, data: Dict[str, Any]) -> PriceResult:
        # Finnhub /quote: c=current, d=change, dp=%change, pc=prev close.
        # c == 0 means an unknown symbol or no data.
        current = data.get("c")
        if not current:
            return PriceResult(ticker=symbol, price=None, raw=data)
        change = data.get("d") or 0
        direction = "up" if change > 0 else "down" if change < 0 else "equal"
        return PriceResult(
            ticker=symbol,
            price=float(current),
            change=change,
            change_direction=direction,
            raw=data,
        )

    def get_company_news(self, symbol: str, days: int = 7) -> List[Dict[str, Any]]:
        """Recent company news for a US symbol (Finnhub free tier). Empty on error
        or when no key is configured."""
        if not self.api_key or not symbol:
            return []
        today = date.today()
        try:
            r = requests.get(
                f"{self.base_url}/company-news",
                params={
                    "symbol": symbol.upper(),
                    "from": (today - timedelta(days=days)).isoformat(),
                    "to": today.isoformat(),
                    "token": self.api_key,
                },
                timeout=5.0,
            )
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Finnhub news error for {symbol}: {e}")
            return []
        out = []
        for it in (data or [])[:15]:
            out.append({
                "headline": it.get("headline"),
                "summary": it.get("summary"),
                "source": it.get("source"),
                "url": it.get("url"),
                "image": it.get("image"),
                "datetime": it.get("datetime"),  # unix seconds
                "category": it.get("category"),
            })
        return out

    def search_symbols(self, query: str) -> List[Dict[str, Any]]:
        """Finnhub /search — used by the frontend to validate/pick US symbols."""
        if not self.api_key or not query:
            return []
        try:
            r = requests.get(
                f"{self.base_url}/search",
                params={"q": query, "token": self.api_key},
                timeout=4.0,
            )
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Finnhub search error for {query!r}: {e}")
            return []
        results = data.get("result") or []
        return [
            {"symbol": it.get("symbol"), "description": it.get("description"), "type": it.get("type")}
            for it in results[:20]
            if it.get("symbol")
        ]

from __future__ import annotations
import time
import requests
import logging
import threading
from dataclasses import dataclass
from typing import Optional, Dict, Any
from cachetools import TTLCache
from app.core.config import settings

logger = logging.getLogger(__name__)

# Standard CSX Stock Ticker Fallbacks (Riel prices)
FALLBACK_PRICES = {
    "PWSA": {"price": 7200.0, "change": 100, "change_direction": "up"},
    "GTI": {"price": 2800.0, "change": 20, "change_direction": "down"},
    "PPAP": {"price": 14000.0, "change": 0, "change_direction": "equal"},
    "PPSP": {"price": 2200.0, "change": 10, "change_direction": "up"},
    "PAS": {"price": 12500.0, "change": -100, "change_direction": "down"},
    "ABC": {"price": 7300.0, "change": 50, "change_direction": "up"},
    "PEPC": {"price": 2500.0, "change": 0, "change_direction": "equal"},
    "DBDE": {"price": 2100.0, "change": 5, "change_direction": "up"},
    "MJQE": {"price": 2100.0, "change": -10, "change_direction": "down"},
    "CGSM": {"price": 2500.0, "change": 15, "change_direction": "up"},
}

@dataclass
class PriceResult:
    ticker: str
    price: Optional[float]
    change: Optional[int] = None  # Change amount in riel
    change_direction: Optional[str] = None  # "up", "down", or "equal"
    raw: Dict[str, Any] = None

class PricingService:
    """
    CSX endpoint: POST /api/v1/website/market-data/stock/trade-summary
    Returns current market data for all stocks on a given board.
    """
    def __init__(self):
        # Cache results for 45 seconds to minimize hitting external API
        self.cache = TTLCache(maxsize=512, ttl=45)
        # Start a background daemon thread to periodically refresh prices
        threading.Thread(target=self._background_refresh_loop, daemon=True).start()

    def _background_refresh_loop(self):
        """Periodically refreshes the CSX prices cache in a separate thread."""
        logger.info("CSX pricing background refresh loop started.")
        while True:
            try:
                self._fetch_all_prices_from_api(force=True)
            except Exception as e:
                logger.error(f"Error in CSX price pre-fetch: {e}")
            time.sleep(30)

    def _fetch_all_prices_from_api(self, force: bool = False) -> list:
        """Helper to fetch from the CSX API and populate all caches in one go."""
        cache_key = "raw_api_data"
        if not force and cache_key in self.cache:
            data = self.cache[cache_key]
        else:
            url = f"{settings.csx_base_url}/api/v1/website/market-data/stock/trade-summary"
            payload = {
                "board": "all",
                "fromDate": None
            }
            try:
                # Fast timeout (3s) to prevent blocking
                r = requests.post(url, json=payload, timeout=3.0)
                r.raise_for_status()
                data = r.json()
                self.cache[cache_key] = data
            except requests.exceptions.RequestException as e:
                logger.warning(f"Error fetching from CSX API: {e}")
                return []

        all_stocks = []
        try:
            auction_data = data.get("data", {}).get("auctionTradingMethod", [])
            if isinstance(auction_data, list):
                for stock_data in auction_data:
                    try:
                        ticker = stock_data.get("stock", "").upper()
                        price_str = str(stock_data.get("close", "")).replace(",", "")
                        price = float(price_str)
                        
                        # Extract change info
                        change = 0
                        change_val = stock_data.get("change")
                        if change_val is not None:
                            if isinstance(change_val, str):
                                change_val = change_val.replace(",", "").strip()
                                change = int(float(change_val)) if change_val else 0
                            else:
                                change = int(change_val)
                                
                        change_direction = stock_data.get("changeUpDown", "equal")
                        
                        # Cache the individual PriceResult
                        latest_key = f"latest:{ticker}"
                        self.cache[latest_key] = PriceResult(
                            ticker=ticker,
                            price=price,
                            change=change,
                            change_direction=change_direction,
                            raw=data
                        )
                        
                        all_stocks.append({
                            "ticker": ticker,
                            "price": price,
                            "change": change,
                            "change_direction": change_direction
                        })
                    except (ValueError, TypeError):
                        continue
        except Exception as e:
            logger.error(f"Error parsing CSX API response: {e}")
            
        return all_stocks

    def get_latest_price(self, symbol: str) -> PriceResult:
        symbol = symbol.upper()
        key = f"latest:{symbol}"
        if key in self.cache:
            return self.cache[key]

        # Trigger shared API fetch to populate cache
        self._fetch_all_prices_from_api()

        if key in self.cache:
            return self.cache[key]

        # If API call failed or symbol not returned by API, use fallback prices
        if symbol in FALLBACK_PRICES:
            fb = FALLBACK_PRICES[symbol]
            res = PriceResult(
                ticker=symbol,
                price=fb["price"],
                change=fb["change"],
                change_direction=fb["change_direction"],
                raw={"fallback": True}
            )
            self.cache[key] = res
            return res

        # Otherwise return empty PriceResult
        res = PriceResult(ticker=symbol, price=None, raw={"error": "Price not found"})
        self.cache[key] = res
        return res

    def get_all_prices(self) -> list:
        """Get all stock prices from the API, falling back to mock values if offline."""
        key = "all_prices"
        if key in self.cache:
            return self.cache[key]

        all_prices = self._fetch_all_prices_from_api()
        if all_prices:
            self.cache[key] = all_prices
            return all_prices

        # If API is unreachable, build response from fallbacks
        fb_list = []
        for ticker, fb in FALLBACK_PRICES.items():
            fb_list.append({
                "ticker": ticker,
                "price": fb["price"],
                "change": fb["change"],
                "change_direction": fb["change_direction"]
            })
        self.cache[key] = fb_list
        return fb_list

# Instantiate a single global instance for dependency injection (Singleton)
pricing_service_instance = PricingService()

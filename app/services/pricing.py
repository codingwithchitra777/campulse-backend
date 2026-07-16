from __future__ import annotations
import time
import requests
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List
from cachetools import TTLCache
import pytz
from app.core.config import settings
from app.repositories.price_history import PriceHistoryRepository
from app.services.redis_service import RedisService

logger = logging.getLogger(__name__)

PHNOM_PENH_TZ = pytz.timezone("Asia/Phnom_Penh")
SNAPSHOT_INTERVAL_SECONDS = 30 * 60

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
    CSX endpoint: GET /api/v1/website/home/main-and-growth-board-stocks-trades
    Returns current market data for both main and growth board stocks.
    """
    def __init__(self):
        self._last_snapshot_ts = 0.0
        # Start a background daemon thread to periodically refresh prices
        threading.Thread(target=self._background_refresh_loop, daemon=True).start()

    def _background_refresh_loop(self):
        """Periodically refreshes the CSX prices cache in a separate thread."""
        logger.info("CSX pricing background refresh loop started.")
        self._startup_sync()
        while True:
            try:
                # Attempt to acquire lock to prevent thundering herd across workers
                if RedisService().acquire_lock("csx:poll_lock", timeout=10):
                    prices = self._fetch_all_prices_from_api(force=True)
                    # Persist real (never fallback) prices as daily snapshots,
                    # throttled — the upsert makes the day's last write the close.
                    if prices and time.time() - self._last_snapshot_ts >= SNAPSHOT_INTERVAL_SECONDS:
                        self.snapshot_prices(prices)
                        self._last_snapshot_ts = time.time()
            except Exception as e:
                logger.error(f"Error in CSX price pre-fetch: {e}")
            time.sleep(15)

    def _startup_sync(self):
        """Sync last 60 days from DB to Redis on startup."""
        try:
            repo = PriceHistoryRepository()
            redis_service = RedisService()
            history = repo.get_recent_history_all(days=60)
            for entry in history:
                redis_service.save_sparkline_price(
                    entry["ticker"], 
                    entry["date"], 
                    float(entry["price"])
                )
            logger.info("Startup sync of CSX sparkline data to Redis complete.")
        except Exception as e:
            logger.error(f"Error in CSX sparkline startup sync: {e}")

    def snapshot_prices(self, prices: List[dict]) -> None:
        """Upsert one price_history row per ticker for today's Phnom Penh
        trading date. DB errors are logged, never propagated to the loop."""
        snapshot_date = datetime.now(PHNOM_PENH_TZ).date()
        date_str = snapshot_date.strftime("%Y-%m-%d")
        repo = PriceHistoryRepository()
        redis_service = RedisService()
        
        for p in prices:
            ticker = p.get('ticker')
            if not ticker:
                continue
                
            price = p.get('price')
            if price is None:
                continue
                
            try:
                # 1) Save to Postgres
                repo.upsert_snapshot(ticker, snapshot_date, int(price))
                # 2) Save to Redis Hash for fast sparkline retrieval
                redis_service.save_sparkline_price(ticker, date_str, float(price))
            except Exception as e:
                logger.error(f"Error saving snapshot for {ticker}: {e}")

    def _fetch_all_prices_from_api(self, force: bool = False) -> list:
        """Helper to fetch from the CSX API and save to Redis."""
        url = f"{settings.csx_base_url}/api/v1/website/home/main-and-growth-board-stocks-trades"
        data = {}
        try:
            # Fast timeout (3s) to prevent blocking
            r = requests.get(url, timeout=3.0)
            r.raise_for_status()
            data = r.json()
        except requests.exceptions.RequestException as e:
            logger.warning(f"Error fetching from CSX API: {e}")
            return []

        all_stocks = []
        try:
            data_content = data.get("data", {})
            main_board = data_content.get("mainBoardStockTrades", []) or []
            growth_board = data_content.get("growthBoardStockTrades", []) or []
            combined_data = main_board + growth_board

            for stock_data in combined_data:
                try:
                    ticker = stock_data.get("issueName", "").strip().upper()
                    if not ticker:
                        continue
                    price_str = str(stock_data.get("currentPrice", "")).replace(",", "")
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
                    
                    all_stocks.append({
                        "ticker": ticker,
                        "price": price,
                        "change": change,
                        "change_direction": change_direction
                    })
                except (ValueError, TypeError):
                    continue
                    
            if all_stocks:
                RedisService().save_latest_prices(all_stocks)
                
        except Exception as e:
            logger.error(f"Error parsing CSX API response: {e}")
            
        return all_stocks

    def get_latest_price(self, symbol: str) -> PriceResult:
        symbol = symbol.upper()
        
        # Read strictly from Redis
        cached_prices = RedisService().get_latest_prices()
        for p in cached_prices:
            if p.get("ticker") == symbol:
                return PriceResult(
                    ticker=symbol,
                    price=p.get("price"),
                    change=p.get("change"),
                    change_direction=p.get("change_direction")
                )

        # Fallback
        if symbol in FALLBACK_PRICES:
            fb = FALLBACK_PRICES[symbol]
            return PriceResult(
                ticker=symbol,
                price=fb["price"],
                change=fb["change"],
                change_direction=fb["change_direction"],
                raw={"fallback": True}
            )

        return PriceResult(ticker=symbol, price=None, raw={"error": "Price not found"})

    def get_all_prices(self) -> list:
        """Get all stock prices from Redis (no external API calls on demand)."""
        cached_prices = RedisService().get_latest_prices()
        if cached_prices:
            return cached_prices

        # If Redis is empty, build response from fallbacks
        fb_list = []
        for ticker, fb in FALLBACK_PRICES.items():
            fb_list.append({
                "ticker": ticker,
                "price": fb["price"],
                "change": fb["change"],
                "change_direction": fb["change_direction"]
            })
        return fb_list

# Instantiate a single global instance for dependency injection (Singleton)
pricing_service_instance = PricingService()

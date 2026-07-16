import redis
import logging
from datetime import datetime, timedelta
from typing import List, Dict
from app.core.config import settings

logger = logging.getLogger(__name__)

class RedisService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisService, cls).__new__(cls)
            cls._instance.client = redis.from_url(settings.redis_url, decode_responses=True)
        return cls._instance

    def save_sparkline_price(self, ticker: str, date_str: str, price: float):
        """Save a closing price for a specific date and prune old data to keep only last 60 days."""
        try:
            key = f"sparkline:{ticker.upper()}"
            self.client.hset(key, date_str, str(price))
            
            # Prune data older than 60 days
            self._prune_sparkline(key)
        except Exception as e:
            logger.error(f"Error saving sparkline to Redis for {ticker}: {e}")

    def _prune_sparkline(self, key: str):
        """Removes entries older than 60 days from the hash."""
        try:
            cutoff_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
            all_fields = self.client.hkeys(key)
            fields_to_delete = [f for f in all_fields if f < cutoff_date]
            if fields_to_delete:
                self.client.hdel(key, *fields_to_delete)
        except Exception as e:
            logger.error(f"Error pruning sparkline for {key}: {e}")

    def get_sparkline(self, ticker: str) -> List[float]:
        """Get chronological array of prices for the last 60 days."""
        try:
            key = f"sparkline:{ticker.upper()}"
            data = self.client.hgetall(key)
            if not data:
                return []
            
            # Sort by date (keys) and return values as floats
            sorted_dates = sorted(data.keys())
            return [float(data[d]) for d in sorted_dates]
        except Exception as e:
            logger.error(f"Error getting sparkline from Redis for {ticker}: {e}")
            return []

    def get_sparklines_batch(self, tickers: List[str]) -> Dict[str, List[float]]:
        """Fetch sparklines for multiple tickers efficiently."""
        result = {}
        for ticker in tickers:
            result[ticker.upper()] = self.get_sparkline(ticker)
        return result

    def acquire_lock(self, lock_name: str, timeout: int = 10) -> bool:
        """Acquires a distributed lock with a timeout in seconds."""
        try:
            return bool(self.client.set(lock_name, "1", ex=timeout, nx=True))
        except Exception as e:
            logger.error(f"Error acquiring lock {lock_name}: {e}")
            return False

    def save_latest_prices(self, prices: List[Dict]):
        """Serializes and saves the latest market prices to Redis."""
        try:
            import json
            self.client.set("csx:latest_prices", json.dumps(prices))
        except Exception as e:
            logger.error(f"Error saving latest prices to Redis: {e}")

    def get_latest_prices(self) -> List[Dict]:
        """Retrieves and deserializes the latest market prices from Redis."""
        try:
            import json
            data = self.client.get("csx:latest_prices")
            if data:
                return json.loads(data)
            return []
        except Exception as e:
            logger.error(f"Error getting latest prices from Redis: {e}")
            return []

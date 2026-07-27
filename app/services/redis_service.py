import json
import redis
import logging
import time
from datetime import datetime, timedelta
from typing import List, Dict
from app.core.config import settings

logger = logging.getLogger(__name__)

# While Redis is failing, stop calling it for this long. Without this every
# request re-attempts a dead server (slow) and re-logs the same error (noisy).
CIRCUIT_OPEN_SECONDS = 30


class RedisService:
    """Fast path for prices/sparklines. Every method degrades to a neutral
    value (``[]`` / ``{}`` / ``False``) instead of raising, so callers can fall
    back to Postgres — see ``PricingService`` and ``/market/sparklines``.

    Redis is a *cache*, never the system of record: everything here is also
    written to ``price_history``.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            inst = super(RedisService, cls).__new__(cls)
            inst._healthy = True          # last known state, for transition logging
            inst._retry_at = 0.0          # circuit stays open until this timestamp
            inst.client = None
            if settings.redis_url:
                try:
                    inst.client = redis.from_url(settings.redis_url, decode_responses=True)
                except Exception as e:
                    logger.error("Invalid REDIS_URL, cache disabled: %s", e)
            else:
                logger.warning("REDIS_URL not configured — serving prices from Postgres only")
            cls._instance = inst
        return cls._instance

    # ── circuit breaker ────────────────────────────────────────────────
    @property
    def available(self) -> bool:
        """False when unconfigured, or while the circuit is open (recent failure)."""
        return self.client is not None and time.time() >= self._retry_at

    def _mark_down(self, op: str, exc: Exception) -> None:
        self._retry_at = time.time() + CIRCUIT_OPEN_SECONDS
        if self._healthy:
            # Log the full reason once per outage, not once per call.
            logger.error(
                "Redis unavailable (%s: %s) — serving from Postgres for the next %ss",
                op, exc, CIRCUIT_OPEN_SECONDS,
            )
            self._healthy = False
        else:
            logger.debug("Redis still down (%s: %s)", op, exc)

    def _mark_up(self) -> None:
        if not self._healthy:
            logger.info("Redis recovered — resuming cache reads/writes")
            self._healthy = True
        self._retry_at = 0.0

    def _exec(self, op: str, fn, default):
        """Run a Redis op, returning `default` if Redis is down/failing."""
        if not self.available:
            return default
        try:
            result = fn(self.client)
            self._mark_up()
            return result
        except Exception as e:
            self._mark_down(op, e)
            return default

    # ── sparklines ─────────────────────────────────────────────────────
    def save_sparkline_price(self, ticker: str, date_str: str, price: float):
        """Save a closing price for a specific date and prune old data to keep only last 60 days."""
        key = f"sparkline:{ticker.upper()}"

        def op(client):
            client.hset(key, date_str, str(price))
            self._prune_sparkline(key)

        self._exec(f"save_sparkline[{ticker}]", op, None)

    def _prune_sparkline(self, key: str):
        """Removes entries older than 60 days from the hash."""
        cutoff_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")

        def op(client):
            fields_to_delete = [f for f in client.hkeys(key) if f < cutoff_date]
            if fields_to_delete:
                client.hdel(key, *fields_to_delete)

        self._exec(f"prune_sparkline[{key}]", op, None)

    def get_sparkline(self, ticker: str) -> List[float]:
        """Get chronological array of prices for the last 60 days ([] if unavailable)."""
        key = f"sparkline:{ticker.upper()}"
        data = self._exec(f"get_sparkline[{ticker}]", lambda c: c.hgetall(key), None)
        if not data:
            return []
        return [float(data[d]) for d in sorted(data.keys())]

    def get_sparklines_batch(self, tickers: List[str]) -> Dict[str, List[float]]:
        """Fetch sparklines for multiple tickers efficiently."""
        if not tickers:
            return {}
        keys = [f"sparkline:{t.upper()}" for t in tickers]

        def op(client):
            pipe = client.pipeline()
            for k in keys:
                pipe.hgetall(k)
            return pipe.execute()

        rows = self._exec("get_sparklines_batch", op, None) or []
        result: Dict[str, List[float]] = {}
        for ticker, data in zip(tickers, rows):
            result[ticker.upper()] = (
                [float(data[d]) for d in sorted(data.keys())] if data else []
            )
        # Circuit open / pipeline failed: still return the expected shape.
        for t in tickers:
            result.setdefault(t.upper(), [])
        return result

    # ── locking ────────────────────────────────────────────────────────
    def acquire_lock(self, lock_name: str, timeout: int = 10) -> bool:
        """Acquires a distributed lock. Returns False when Redis is unavailable
        — callers must treat a lost lock as 'someone else may also run'."""
        return bool(self._exec(
            f"acquire_lock[{lock_name}]",
            lambda c: c.set(lock_name, "1", ex=timeout, nx=True),
            False,
        ))

    # ── latest prices ──────────────────────────────────────────────────
    def save_latest_prices(self, prices: List[Dict]):
        """Serializes and saves the latest market prices to Redis."""
        payload = json.dumps(prices)
        self._exec("save_latest_prices", lambda c: c.set("csx:latest_prices", payload), None)

    def get_latest_prices(self) -> List[Dict]:
        """Latest market prices, or [] when Redis is empty/unavailable."""
        data = self._exec("get_latest_prices", lambda c: c.get("csx:latest_prices"), None)
        if not data:
            return []
        try:
            return json.loads(data)
        except (ValueError, TypeError) as e:
            logger.error("Corrupt latest_prices payload in Redis: %s", e)
            return []

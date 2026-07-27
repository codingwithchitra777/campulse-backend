"""Redis is a cache, not the system of record: when it's down (or its
credentials are wrong, as happened in prod), prices and sparklines must still
be served from Postgres instead of going blank or stale-static.

Uses a disposable ticker so the real CSX history is never touched; cleans up by
exact ticker on teardown.
"""
import uuid
from datetime import date, timedelta

import pytest

from app.repositories.price_history import PriceHistoryRepository
from app.services.pricing import PricingService
from app.services.redis_service import RedisService

TICKER = f"ZZT{uuid.uuid4().hex[:5].upper()}"  # disposable, never a real CSX symbol


@pytest.fixture
def seeded_history():
    """Three days of closes in Postgres for a disposable ticker."""
    repo = PriceHistoryRepository()
    today = date.today()
    prices = [7000, 7100, 7250]
    for i, price in enumerate(prices):
        repo.upsert_snapshot(TICKER, today - timedelta(days=len(prices) - 1 - i), price)
    yield {"ticker": TICKER, "prices": prices, "latest": prices[-1]}
    repo.delete_snapshots(TICKER)  # exact ticker only


@pytest.fixture
def redis_down(monkeypatch):
    """Simulate a total Redis outage (the 'invalid username-password pair' case)."""
    def boom(*args, **kwargs):
        raise ConnectionError("invalid username-password pair")

    svc = RedisService()
    monkeypatch.setattr(svc, "_retry_at", 0.0, raising=False)
    monkeypatch.setattr(svc, "_healthy", True, raising=False)
    monkeypatch.setattr(svc, "_exec", lambda op, fn, default: default)
    yield svc


def _fresh_service() -> PricingService:
    """A PricingService with no warm in-memory cache, so the DB path is exercised."""
    svc = PricingService.__new__(PricingService)  # skip __init__: no background thread
    svc._last_snapshot_ts = 0.0
    svc._memory_prices = []
    from cachetools import TTLCache
    svc._db_fallback_cache = TTLCache(maxsize=1, ttl=60)
    return svc


def test_latest_price_falls_back_to_postgres(seeded_history, redis_down):
    svc = _fresh_service()
    result = svc.get_latest_price(seeded_history["ticker"])

    assert result.price == seeded_history["latest"], "should serve the newest stored close"
    assert result.raw and result.raw.get("stale") is True


def test_all_prices_falls_back_to_postgres(seeded_history, redis_down):
    svc = _fresh_service()
    prices = svc.get_all_prices()

    row = next((p for p in prices if p["ticker"] == seeded_history["ticker"]), None)
    assert row is not None, "DB-backed tickers must appear, not just the 10 static fallbacks"
    assert row["price"] == seeded_history["latest"]


def test_sparklines_fall_back_to_postgres(seeded_history, redis_down):
    svc = _fresh_service()
    series = svc.get_sparklines([seeded_history["ticker"]])

    assert series[seeded_history["ticker"]] == [
        float(p) for p in seeded_history["prices"]
    ], "chronological closes from Postgres"


def test_memory_cache_preferred_over_db(seeded_history, redis_down):
    """Last-good live fetch beats stored closes — it's fresher."""
    svc = _fresh_service()
    svc._memory_prices = [
        {"ticker": seeded_history["ticker"], "price": 9999.0, "change": 5, "change_direction": "up"}
    ]
    assert svc.get_latest_price(seeded_history["ticker"]).price == 9999.0


def test_redis_failure_opens_circuit_and_returns_neutral(monkeypatch):
    """A failing Redis must not raise, and must stop being called while down."""
    svc = RedisService()
    monkeypatch.setattr(svc, "_retry_at", 0.0, raising=False)
    monkeypatch.setattr(svc, "_healthy", True, raising=False)

    calls = {"n": 0}

    class Boom:
        def get(self, *a, **k):
            calls["n"] += 1
            raise ConnectionError("invalid username-password pair")

    monkeypatch.setattr(svc, "client", Boom())

    assert svc.get_latest_prices() == []      # degraded, not raised
    assert svc.available is False             # circuit opened
    assert svc.get_latest_prices() == []
    assert calls["n"] == 1, "circuit breaker should stop hammering a dead Redis"

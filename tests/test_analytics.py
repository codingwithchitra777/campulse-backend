"""Analytics: descriptive stats computed from the user's own trades."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token

client = TestClient(app)


def auth_headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id, role='user')}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


def _buy(uid, ticker, price, qty):
    return client.post("/api/trades", headers=auth_headers(uid),
                       json={"ticker": ticker, "side": "BUY", "price": price, "qty": qty, "commission": 0})


def _sell(uid, ticker, price, qty):
    return client.post("/api/trades", headers=auth_headers(uid),
                       json={"ticker": ticker, "side": "SELL", "price": price, "qty": qty, "commission": 0})


def test_analytics_empty_user(user_id):
    a = client.get("/api/analytics", headers=auth_headers(user_id)).json()
    assert a["tradeCount"] == 0
    assert a["winRate"] == 0
    assert a["bestTrade"] is None


def test_analytics_win_rate_and_best_worst(user_id):
    _buy(user_id, "abc", 100, 100)
    _sell(user_id, "abc", 150, 50)   # +2500 win
    _buy(user_id, "xyz", 200, 100)
    _sell(user_id, "xyz", 150, 50)   # -2500 loss

    a = client.get("/api/analytics", headers=auth_headers(user_id)).json()
    assert a["tradeCount"] == 4
    assert a["buyCount"] == 2 and a["sellCount"] == 2
    assert a["closedTradeCount"] == 2
    assert a["wins"] == 1 and a["losses"] == 1
    assert a["winRate"] == 50.0
    assert a["bestTrade"]["ticker"] == "ABC"
    assert float(a["bestTrade"]["realisedPnl"]) == 2500
    assert a["worstTrade"]["ticker"] == "XYZ"
    # Same-day buy/sell => zero hold time.
    assert a["avgHoldDays"] == 0
    # Net realised across the single (KHR) currency nets to zero.
    khr = next(g for g in a["byCurrency"] if g["currency"] == "KHR")
    assert float(khr["realisedPnl"]) == 0
    assert khr["wins"] == 1 and khr["losses"] == 1


def test_analytics_separates_currencies(user_id):
    _buy(user_id, "abc", 7000, 10)   # CSX / KHR
    client.post("/api/trades", headers=auth_headers(user_id),
                json={"ticker": "aapl", "side": "BUY", "price": 100, "qty": 5,
                      "commission": 0, "market": "US", "currency": "USD"})
    client.post("/api/trades", headers=auth_headers(user_id),
                json={"ticker": "aapl", "side": "SELL", "price": 120, "qty": 5,
                      "commission": 0, "market": "US", "currency": "USD"})
    a = client.get("/api/analytics", headers=auth_headers(user_id)).json()
    usd = next(g for g in a["byCurrency"] if g["currency"] == "USD")
    assert float(usd["realisedPnl"]) == 100   # 5 * (120 - 100)
    assert any(g["currency"] == "KHR" for g in a["byCurrency"])

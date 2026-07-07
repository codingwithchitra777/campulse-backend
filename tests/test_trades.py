import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


def test_health_check():
    resp = client.get("/api/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "service": "campulse-backend"}


def test_get_trades_empty_for_new_user(user_id):
    resp = client.get("/api/trades", headers={"X-User-Id": user_id})
    assert resp.status_code == 200
    assert resp.json() == []


def test_buy_trade_is_persisted(user_id):
    resp = client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["trade"]["ticker"] == "ABC"
    assert body["trade"]["side"] == "BUY"
    assert body["trade"]["commission"] == int(7000 * 100 * 0.0047)
    assert body["realisedPnl"] == 0

    listed = client.get("/api/trades", headers={"X-User-Id": user_id}).json()
    assert len(listed) == 1
    assert listed[0]["tradeId"] == body["trade"]["tradeId"]


def test_sell_lifo_matches_against_buy(user_id):
    client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    resp = client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["allocations"]) == 1
    alloc = body["allocations"][0]
    assert alloc["qtyAllocated"] == 50
    assert alloc["buyPrice"] == 7000
    assert alloc["sellPrice"] == 7500
    assert body["realisedPnl"] == alloc["realisedPnl"]
    # Gross P&L before commission is 50 * (7500 - 7000) = 25000; the matcher
    # deducts prorated buy commission + full sell commission from that.
    assert 0 < body["realisedPnl"] < 50 * (7500 - 7000)


def test_sell_without_matching_buy_warns_instead_of_erroring(user_id):
    resp = client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "xyz", "side": "SELL", "price": 100, "qty": 10},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["allocations"] == []
    assert body["warning"] is not None


@pytest.mark.parametrize("side", ["buy", "sell", "BUYY", ""])
def test_invalid_side_rejected_by_schema(user_id, side):
    resp = client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": side, "price": 100, "qty": 10},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("price,qty", [(0, 10), (-5, 10), (100, 0), (100, -10)])
def test_non_positive_price_or_qty_rejected(user_id, price, qty):
    resp = client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": "BUY", "price": price, "qty": qty},
    )
    assert resp.status_code == 400


def test_trades_scoped_per_user(user_id):
    other_user = f"pytest_{uuid.uuid4().hex[:12]}"
    client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 1},
    )
    resp = client.get("/api/trades", headers={"X-User-Id": other_user})
    assert resp.status_code == 200
    assert resp.json() == []


def test_ticker_filter(user_id):
    client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 1},
    )
    client.post(
        "/api/trades",
        headers={"X-User-Id": user_id},
        json={"ticker": "def", "side": "BUY", "price": 200, "qty": 1},
    )
    resp = client.get("/api/trades", headers={"X-User-Id": user_id}, params={"ticker": "abc"})
    assert resp.status_code == 200
    tickers = {t["ticker"] for t in resp.json()}
    assert tickers == {"ABC"}

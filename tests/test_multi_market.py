"""Phase 2: Decimal money + Finnhub US provider + market-keyed matching.

The API tests hit the real DB under disposable pytest_ users (no teardown here,
consistent with the rest of the suite). FinnhubProvider tests never hit the
network — they exercise the pure parser or monkeypatch requests.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.services.finnhub_provider import FinnhubProvider

client = TestClient(app)


def auth_headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id, role='user')}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


# ---- FinnhubProvider (no network) ----

def test_finnhub_parse_valid_quote():
    res = FinnhubProvider._parse_quote("AAPL", {"c": 172.34, "d": 1.5, "dp": 0.88, "pc": 170.84})
    assert res.ticker == "AAPL"
    assert res.price == 172.34
    assert res.change_direction == "up"


def test_finnhub_parse_unknown_symbol_is_none():
    # Finnhub returns c=0 for an unknown symbol.
    res = FinnhubProvider._parse_quote("NOPE", {"c": 0, "d": 0})
    assert res.price is None


def test_finnhub_no_api_key_degrades_gracefully():
    assert FinnhubProvider(api_key="").get_latest_price("AAPL").price is None


def test_finnhub_search_parses(monkeypatch):
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"count": 1, "result": [
                {"symbol": "AAPL", "description": "APPLE INC", "type": "Common Stock"}]}

    monkeypatch.setattr("app.services.finnhub_provider.requests.get", lambda *a, **k: FakeResp())
    out = FinnhubProvider(api_key="test").search_symbols("apple")
    assert out and out[0]["symbol"] == "AAPL"


# ---- Decimal money round-trips (USD cents survive) ----

def test_usd_price_keeps_cents(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "aapl", "side": "BUY", "price": 172.34, "qty": 10,
              "commission": 0, "market": "US", "currency": "USD"},
    )
    assert resp.status_code == 200
    trade = resp.json()["trade"]
    assert float(trade["price"]) == 172.34   # not truncated to 172
    assert trade["currency"] == "USD"

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"]
    assert float(listed[0]["price"]) == 172.34


def test_usd_sell_pnl_is_cent_accurate(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "aapl", "side": "BUY", "price": 100.00, "qty": 10,
              "commission": 0, "market": "US", "currency": "USD"},
    )
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "aapl", "side": "SELL", "price": 150.55, "qty": 10,
              "commission": 0, "market": "US", "currency": "USD"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 10 * (150.55 - 100.00) = 505.50, kept to the cent (not floored to 505).
    assert float(body["realisedPnl"]) == 505.5
    assert float(body["allocations"][0]["sellPrice"]) == 150.55


# ---- Market-keyed matching (the ABC collision fix) ----

def test_same_ticker_different_markets_do_not_cross_match(user_id):
    # A CSX "ABC" lot and a US "ABC" lot must never match each other.
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 5},  # CSX/KHR default
    )
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 5,
              "commission": 0, "market": "US", "currency": "USD"},
    )
    # Sell US ABC — must consume the US lot (100), never the CSX lot (7000).
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 150, "qty": 5,
              "commission": 0, "market": "US", "currency": "USD"},
    )
    assert resp.status_code == 200
    allocs = resp.json()["allocations"]
    assert len(allocs) == 1
    assert float(allocs[0]["buyPrice"]) == 100
    assert float(resp.json()["realisedPnl"]) == 250  # 5 * (150 - 100)

    # The CSX position is untouched; the US position is fully sold.
    csx = client.get("/api/position/abc", headers=auth_headers(user_id), params={"market": "CSX"}).json()
    us = client.get("/api/position/abc", headers=auth_headers(user_id), params={"market": "US"}).json()
    assert csx["remainingQty"] == 5
    assert us["remainingQty"] == 0


def test_portfolio_groups_two_currencies(user_id):
    client.post("/api/trades", headers=auth_headers(user_id),
                json={"ticker": "pwsa", "side": "BUY", "price": 7000, "qty": 10})
    client.post("/api/trades", headers=auth_headers(user_id),
                json={"ticker": "aapl", "side": "BUY", "price": 172.34, "qty": 3,
                      "commission": 0, "market": "US", "currency": "USD"})
    portfolio = client.get("/api/portfolio", headers=auth_headers(user_id)).json()
    by_ticker = {p["ticker"]: p for p in portfolio}
    assert by_ticker["PWSA"]["currency"] == "KHR"
    assert by_ticker["AAPL"]["currency"] == "USD"
    assert by_ticker["AAPL"]["market"] == "US"

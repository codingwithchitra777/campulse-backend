import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.repositories.price_history import PriceHistoryRepository

client = TestClient(app)

# Fake ticker so tests never touch real snapshot rows.
TEST_TICKER = "ZZT"


def auth_headers(user_id: str) -> dict:
    token = create_access_token(user_id=user_id, role="user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def price_repo():
    repo = PriceHistoryRepository()
    yield repo
    repo.delete_snapshots(TEST_TICKER)


def test_upsert_same_day_keeps_latest_price(price_repo):
    day = date(2020, 1, 6)
    price_repo.upsert_snapshot(TEST_TICKER, day, 1000)
    price_repo.upsert_snapshot(TEST_TICKER, day, 1050)

    rows = price_repo.get_history([TEST_TICKER])
    same_day = [r for r in rows if r["date"] == "2020-01-06"]
    assert len(same_day) == 1
    assert same_day[0]["price"] == 1050


def test_timeline_includes_equity_from_snapshots(user_id, price_repo):
    # BUY 10 @ 2000 on 2020-01-06; snapshots on the 6th, 7th (and a pre-trade one to skip).
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": TEST_TICKER, "side": "BUY", "price": 2000, "qty": 10,
              "commission": 0, "orderDate": "2020-01-06"},
    )
    price_repo.upsert_snapshot(TEST_TICKER, date(2020, 1, 3), 1900)  # before first trade
    price_repo.upsert_snapshot(TEST_TICKER, date(2020, 1, 6), 2100)
    price_repo.upsert_snapshot(TEST_TICKER, date(2020, 1, 7), 2200)

    body = client.get("/api/charts/timeline", headers=auth_headers(user_id)).json()
    assert body["equity"] == [
        {"date": "2020-01-06", "value": 10 * 2100},
        {"date": "2020-01-07", "value": 10 * 2200},
    ]


def test_timeline_equity_sell_reduces_holdings(user_id, price_repo):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": TEST_TICKER, "side": "BUY", "price": 2000, "qty": 10,
              "commission": 0, "orderDate": "2020-01-06"},
    )
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": TEST_TICKER, "side": "SELL", "price": 2150, "qty": 4,
              "commission": 0, "orderDate": "2020-01-07"},
    )
    price_repo.upsert_snapshot(TEST_TICKER, date(2020, 1, 6), 2100)
    price_repo.upsert_snapshot(TEST_TICKER, date(2020, 1, 8), 2300)

    body = client.get("/api/charts/timeline", headers=auth_headers(user_id)).json()
    # Jan 6: 10 held @ 2100; Jan 8: 6 held @ 2300 (snapshot forward-fills past the sell).
    assert body["equity"] == [
        {"date": "2020-01-06", "value": 10 * 2100},
        {"date": "2020-01-08", "value": 6 * 2300},
    ]


def test_timeline_equity_empty_without_snapshots(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "NOSNAP", "side": "BUY", "price": 100, "qty": 1, "commission": 0},
    )
    body = client.get("/api/charts/timeline", headers=auth_headers(user_id)).json()
    assert body["equity"] == []

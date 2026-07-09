import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository

client = TestClient(app)


def auth_headers(user_id: str) -> dict:
    token = create_access_token(user_id=user_id, role="user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


def _insert_matched_pair(user_id: str, ticker: str, when: datetime, seq: int,
                         buy_price: int, sell_price: int, qty: int) -> int:
    """Backdated BUY+SELL pair with its allocation, inserted via the repos —
    POST /api/trades always stamps utcnow(). Returns the realised P/L."""
    trade_repo = TradeRepository()
    alloc_repo = AllocationRepository()
    buy_id = str(uuid.uuid4())
    sell_id = str(uuid.uuid4())
    trade_repo.add_trade({
        "tradeId": buy_id, "userId": user_id, "seq": seq, "ticker": ticker,
        "side": "BUY", "price": buy_price, "qty": qty, "commission": 0,
        "orderDate": when,
    })
    trade_repo.add_trade({
        "tradeId": sell_id, "userId": user_id, "seq": seq + 1, "ticker": ticker,
        "side": "SELL", "price": sell_price, "qty": qty, "commission": 0,
        "orderDate": when,
    })
    realised = qty * (sell_price - buy_price)
    alloc_repo.add_allocation({
        "allocId": str(uuid.uuid4()), "userId": user_id, "ticker": ticker,
        "sellTradeId": sell_id, "buyTradeId": buy_id, "qtyAllocated": qty,
        "buyPrice": buy_price, "buyCommission": 0, "buyQty": qty,
        "sellPrice": sell_price, "sellCommission": 0, "sellQty": qty,
        "realisedPnl": realised, "createdAt": when,
    })
    return realised


def test_charts_timeline_requires_auth():
    resp = client.get("/api/charts/timeline")
    assert resp.status_code in (401, 403)


def test_charts_timeline_empty_for_new_user(user_id):
    resp = client.get("/api/charts/timeline", headers=auth_headers(user_id))
    assert resp.status_code == 200
    assert resp.json() == {"investment": [], "pnl": []}


def test_charts_timeline_via_api(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100, "commission": 500},
    )
    sell = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 40, "commission": 300},
    ).json()

    resp = client.get("/api/charts/timeline", headers=auth_headers(user_id))
    assert resp.status_code == 200
    body = resp.json()

    # Both trades happened today, so they merge into a single point.
    today = datetime.utcnow().date().isoformat()
    assert body["investment"] == [{
        "date": today,
        "invested": 7000 * 100 + 500,
        "recovered": 7500 * 40 - 300,
    }]
    assert body["pnl"] == [{"date": today, "cumulativePnl": sell["realisedPnl"]}]


def test_charts_timeline_cumulative_across_days(user_id):
    pnl_a = _insert_matched_pair(user_id, "AAA", datetime(2024, 3, 10), seq=1,
                                 buy_price=100, sell_price=150, qty=10)
    pnl_b = _insert_matched_pair(user_id, "BBB", datetime(2025, 8, 20), seq=3,
                                 buy_price=200, sell_price=170, qty=5)

    resp = client.get("/api/charts/timeline", headers=auth_headers(user_id))
    assert resp.status_code == 200
    body = resp.json()

    assert [p["date"] for p in body["investment"]] == ["2024-03-10", "2025-08-20"]
    assert body["investment"][0]["invested"] == 100 * 10
    assert body["investment"][0]["recovered"] == 150 * 10
    assert body["investment"][1]["invested"] == 100 * 10 + 200 * 5
    assert body["investment"][1]["recovered"] == 150 * 10 + 170 * 5

    assert body["pnl"] == [
        {"date": "2024-03-10", "cumulativePnl": pnl_a},
        {"date": "2025-08-20", "cumulativePnl": pnl_a + pnl_b},
    ]


def test_charts_timeline_scoped_per_user(user_id):
    other_user = f"pytest_{uuid.uuid4().hex[:12]}"
    _insert_matched_pair(user_id, "ABC", datetime(2024, 1, 5), seq=1,
                         buy_price=100, sell_price=150, qty=10)

    resp = client.get("/api/charts/timeline", headers=auth_headers(other_user))
    assert resp.status_code == 200
    assert resp.json() == {"investment": [], "pnl": []}

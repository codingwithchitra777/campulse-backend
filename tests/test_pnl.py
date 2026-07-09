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


def _insert_matched_pair(user_id: str, ticker: str, year: int, seq: int,
                         buy_price: int, sell_price: int, qty: int) -> int:
    """Insert a backdated BUY+SELL pair with its allocation directly via the
    repositories — POST /api/trades always stamps utcnow(), so multi-year
    fixtures can't be created through the API. Returns the realised P/L."""
    trade_repo = TradeRepository()
    alloc_repo = AllocationRepository()
    order_date = datetime(year, 6, 15, 10, 0, 0)
    buy_id = str(uuid.uuid4())
    sell_id = str(uuid.uuid4())
    trade_repo.add_trade({
        "tradeId": buy_id, "userId": user_id, "seq": seq, "ticker": ticker,
        "side": "BUY", "price": buy_price, "qty": qty, "commission": 0,
        "orderDate": order_date,
    })
    trade_repo.add_trade({
        "tradeId": sell_id, "userId": user_id, "seq": seq + 1, "ticker": ticker,
        "side": "SELL", "price": sell_price, "qty": qty, "commission": 0,
        "orderDate": order_date,
    })
    realised = qty * (sell_price - buy_price)
    alloc_repo.add_allocation({
        "allocId": str(uuid.uuid4()), "userId": user_id, "ticker": ticker,
        "sellTradeId": sell_id, "buyTradeId": buy_id, "qtyAllocated": qty,
        "buyPrice": buy_price, "buyCommission": 0, "buyQty": qty,
        "sellPrice": sell_price, "sellCommission": 0, "sellQty": qty,
        "realisedPnl": realised, "createdAt": order_date,
    })
    return realised


def test_pnl_yearly_requires_auth():
    resp = client.get("/api/pnl/yearly")
    assert resp.status_code in (401, 403)


def test_pnl_yearly_empty_for_new_user(user_id):
    resp = client.get("/api/pnl/yearly", headers=auth_headers(user_id))
    assert resp.status_code == 200
    assert resp.json() == []


def test_pnl_yearly_current_year_via_api(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100, "commission": 0},
    )
    sell = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 50, "commission": 0},
    ).json()
    expected_pnl = sell["realisedPnl"]
    assert expected_pnl > 0

    resp = client.get("/api/pnl/yearly", headers=auth_headers(user_id))
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["year"] == datetime.utcnow().year
    assert row["realisedPnl"] == expected_pnl
    assert row["sellCount"] == 1
    assert row["tickers"] == [{"ticker": "ABC", "realisedPnl": expected_pnl, "sellCount": 1}]


def test_pnl_yearly_groups_by_sell_year(user_id):
    pnl_2023 = _insert_matched_pair(user_id, "OLD", 2023, seq=1,
                                    buy_price=100, sell_price=150, qty=10)
    pnl_2024_a = _insert_matched_pair(user_id, "AAA", 2024, seq=3,
                                      buy_price=200, sell_price=180, qty=5)
    pnl_2024_b = _insert_matched_pair(user_id, "BBB", 2024, seq=5,
                                      buy_price=50, sell_price=90, qty=20)

    resp = client.get("/api/pnl/yearly", headers=auth_headers(user_id))
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["year"] for r in rows] == [2024, 2023]

    y2024 = rows[0]
    assert y2024["realisedPnl"] == pnl_2024_a + pnl_2024_b
    assert y2024["sellCount"] == 2
    # Tickers ordered by realised P/L descending: BBB (+800) before AAA (-100).
    assert [t["ticker"] for t in y2024["tickers"]] == ["BBB", "AAA"]
    assert y2024["tickers"][0]["realisedPnl"] == pnl_2024_b
    assert y2024["tickers"][1]["realisedPnl"] == pnl_2024_a

    y2023 = rows[1]
    assert y2023["realisedPnl"] == pnl_2023
    assert y2023["sellCount"] == 1
    assert y2023["tickers"] == [{"ticker": "OLD", "realisedPnl": pnl_2023, "sellCount": 1}]


def test_pnl_yearly_scoped_per_user(user_id):
    other_user = f"pytest_{uuid.uuid4().hex[:12]}"
    _insert_matched_pair(user_id, "ABC", 2024, seq=1,
                         buy_price=100, sell_price=150, qty=10)

    resp = client.get("/api/pnl/yearly", headers=auth_headers(other_user))
    assert resp.status_code == 200
    assert resp.json() == []

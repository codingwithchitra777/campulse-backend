import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token

client = TestClient(app)


def auth_headers(user_id: str) -> dict:
    token = create_access_token(user_id=user_id, role="user")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


def test_health_check():
    resp = client.get("/api/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "service": "campulse-backend"}


def test_get_trades_empty_for_new_user(user_id):
    resp = client.get("/api/trades", headers=auth_headers(user_id))
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_buy_trade_is_persisted(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["trade"]["ticker"] == "ABC"
    assert body["trade"]["side"] == "BUY"
    assert body["trade"]["commission"] == int(7000 * 100 * 0.0047)
    assert body["realisedPnl"] == 0

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()
    assert listed["total"] == 1
    assert len(listed["items"]) == 1
    assert listed["items"][0]["tradeId"] == body["trade"]["tradeId"]


def test_sell_matches_against_buy(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
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


def test_sell_matches_cheapest_lot_first_for_best_profit(user_id):
    # Older cheap lot, then newer expensive lot. Best-profit matching must
    # consume the CHEAP lot (7000) even though the expensive one is newer —
    # LIFO would have picked 7300 here.
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7300, "qty": 100},
    )
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 50},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["allocations"]) == 1
    assert body["allocations"][0]["buyPrice"] == 7000

    # Selling past the cheap lot spills into the expensive one, cheapest first.
    resp2 = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 100},
    )
    allocs = resp2.json()["allocations"]
    assert [a["buyPrice"] for a in allocs] == [7000, 7300]
    assert [a["qtyAllocated"] for a in allocs] == [50, 50]


def test_sell_without_matching_buy_warns_instead_of_erroring(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
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
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": side, "price": 100, "qty": 10},
    )
    assert resp.status_code == 422


@pytest.mark.parametrize("price,qty", [(0, 10), (-5, 10), (100, 0), (100, -10)])
def test_non_positive_price_or_qty_rejected(user_id, price, qty):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": price, "qty": qty},
    )
    assert resp.status_code == 400


def test_trades_scoped_per_user(user_id):
    other_user = f"pytest_{uuid.uuid4().hex[:12]}"
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 1},
    )
    resp = client.get("/api/trades", headers=auth_headers(other_user))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_ticker_filter(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 1},
    )
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "def", "side": "BUY", "price": 200, "qty": 1},
    )
    resp = client.get("/api/trades", headers=auth_headers(user_id), params={"ticker": "abc"})
    assert resp.status_code == 200
    body = resp.json()
    tickers = {t["ticker"] for t in body["items"]}
    assert tickers == {"ABC"}
    # total must reflect the filter, not the user's overall trade count.
    assert body["total"] == 1


def test_trades_pagination_limit_offset(user_id):
    for i in range(5):
        client.post(
            "/api/trades",
            headers=auth_headers(user_id),
            json={"ticker": "abc", "side": "BUY", "price": 100 + i, "qty": 1},
        )

    page1 = client.get("/api/trades", headers=auth_headers(user_id), params={"limit": 2, "offset": 0}).json()
    assert page1["total"] == 5
    assert page1["limit"] == 2
    assert page1["offset"] == 0
    assert len(page1["items"]) == 2

    page2 = client.get("/api/trades", headers=auth_headers(user_id), params={"limit": 2, "offset": 2}).json()
    assert page2["total"] == 5
    assert len(page2["items"]) == 2
    assert {t["tradeId"] for t in page1["items"]}.isdisjoint({t["tradeId"] for t in page2["items"]})

    last_page = client.get("/api/trades", headers=auth_headers(user_id), params={"limit": 2, "offset": 4}).json()
    assert len(last_page["items"]) == 1


def test_edit_untouched_buy_trade_persists(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    trade_id = resp.json()["trade"]["tradeId"]

    patch_resp = client.patch(
        f"/api/trades/{trade_id}",
        headers=auth_headers(user_id),
        json={"ticker": "def", "price": 200, "qty": 20, "commission": 5},
    )
    assert patch_resp.status_code == 200
    updated = patch_resp.json()
    assert updated["ticker"] == "DEF"
    assert updated["price"] == 200
    assert updated["qty"] == 20
    assert updated["commission"] == 5

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"]
    assert listed[0]["ticker"] == "DEF"
    assert listed[0]["price"] == 200


def test_edit_touched_buy_trade_returns_409(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    buy_trade_id = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"][0]["tradeId"]

    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 150, "qty": 5},
    )

    resp = client.patch(
        f"/api/trades/{buy_trade_id}",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "price": 100, "qty": 10},
    )
    assert resp.status_code == 409


def test_edit_sell_trade_returns_409(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 100, "qty": 10},
    )
    sell_trade_id = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"][0]["tradeId"]

    resp = client.patch(
        f"/api/trades/{sell_trade_id}",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "price": 100, "qty": 10},
    )
    assert resp.status_code == 409


def test_edit_other_users_trade_returns_404(user_id):
    other_user = f"pytest_{uuid.uuid4().hex[:12]}"
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    trade_id = resp.json()["trade"]["tradeId"]

    patch_resp = client.patch(
        f"/api/trades/{trade_id}",
        headers=auth_headers(other_user),
        json={"ticker": "abc", "price": 100, "qty": 10},
    )
    assert patch_resp.status_code == 404


def test_delete_untouched_buy_trade(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    trade_id = resp.json()["trade"]["tradeId"]

    delete_resp = client.delete(f"/api/trades/{trade_id}", headers=auth_headers(user_id))
    assert delete_resp.status_code == 200
    assert delete_resp.json()["success"] is True

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()
    assert listed["items"] == []


def test_delete_touched_buy_trade_returns_409(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    buy_trade_id = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"][0]["tradeId"]

    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 150, "qty": 5},
    )

    resp = client.delete(f"/api/trades/{buy_trade_id}", headers=auth_headers(user_id))
    assert resp.status_code == 409


def test_delete_matched_sell_trade_restores_position_qty(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    sell_resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 150, "qty": 5},
    )
    sell_trade_id = sell_resp.json()["trade"]["tradeId"]

    pos_before = client.get("/api/position/abc", headers=auth_headers(user_id)).json()
    assert pos_before["remainingQty"] == 5

    delete_resp = client.delete(f"/api/trades/{sell_trade_id}", headers=auth_headers(user_id))
    assert delete_resp.status_code == 200

    pos_after = client.get("/api/position/abc", headers=auth_headers(user_id)).json()
    assert pos_after["remainingQty"] == 10


def test_backdated_trade_persists_and_flows_to_reports(user_id):
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10,
              "commission": 0, "orderDate": "2024-03-10"},
    )
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 150, "qty": 10,
              "commission": 0, "orderDate": "2024-03-12"},
    )
    assert resp.status_code == 200
    assert resp.json()["trade"]["orderDate"].startswith("2024-03-12")

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"]
    assert {t["orderDate"][:10] for t in listed} == {"2024-03-10", "2024-03-12"}

    # Backdated trades group under their own year in the reports.
    yearly = client.get("/api/pnl/yearly", headers=auth_headers(user_id)).json()
    assert [y["year"] for y in yearly] == [2024]
    assert yearly[0]["realisedPnl"] == 10 * (150 - 100)

    timeline = client.get("/api/charts/timeline", headers=auth_headers(user_id)).json()
    assert [p["date"] for p in timeline["investment"]] == ["2024-03-10", "2024-03-12"]


def test_future_order_date_rejected(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10, "orderDate": "2199-01-01"},
    )
    assert resp.status_code == 400


def test_edit_trade_order_date(user_id):
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10, "orderDate": "2024-05-01"},
    )
    trade_id = resp.json()["trade"]["tradeId"]

    patched = client.patch(
        f"/api/trades/{trade_id}",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "price": 100, "qty": 10, "orderDate": "2023-11-20"},
    )
    assert patched.status_code == 200
    assert patched.json()["orderDate"].startswith("2023-11-20")

    # Omitting orderDate leaves the date unchanged.
    patched2 = client.patch(
        f"/api/trades/{trade_id}",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "price": 120, "qty": 10},
    )
    assert patched2.status_code == 200
    assert patched2.json()["orderDate"].startswith("2023-11-20")
    assert patched2.json()["price"] == 120

    future = client.patch(
        f"/api/trades/{trade_id}",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "price": 100, "qty": 10, "orderDate": "2199-01-01"},
    )
    assert future.status_code == 400


def test_trade_defaults_to_csx_market(user_id):
    """A trade recorded without an explicit market is CSX/KHR (backward compatible)."""
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    assert resp.status_code == 200
    trade = resp.json()["trade"]
    assert trade["market"] == "CSX"
    assert trade["currency"] == "KHR"

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"]
    assert listed[0]["market"] == "CSX"
    assert listed[0]["currency"] == "KHR"


def test_trade_accepts_explicit_market_and_currency(user_id):
    """An explicit market/currency is persisted and echoed back."""
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "aapl", "side": "BUY", "price": 172, "qty": 10,
              "commission": 1, "market": "US", "currency": "USD"},
    )
    assert resp.status_code == 200
    trade = resp.json()["trade"]
    assert trade["market"] == "US"
    assert trade["currency"] == "USD"


def test_unknown_market_falls_back_to_csx(user_id):
    """A bogus market normalizes to CSX rather than persisting garbage."""
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 1, "market": "NASDAQ_TYPO"},
    )
    assert resp.status_code == 200
    assert resp.json()["trade"]["market"] == "CSX"


def test_portfolio_position_exposes_currency(user_id):
    """Portfolio positions carry market/currency so the UI can group per currency."""
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    portfolio = client.get("/api/portfolio", headers=auth_headers(user_id)).json()
    assert len(portfolio) == 1
    assert portfolio[0]["ticker"] == "ABC"
    assert portfolio[0]["market"] == "CSX"
    assert portfolio[0]["currency"] == "KHR"


def test_sell_allocation_inherits_market_currency(user_id):
    """Allocations created by matching inherit the trade's market/currency."""
    client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 100},
    )
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 50},
    )
    alloc = resp.json()["allocations"][0]
    assert alloc["market"] == "CSX"
    assert alloc["currency"] == "KHR"


def test_delete_other_users_trade_returns_404(user_id):
    other_user = f"pytest_{uuid.uuid4().hex[:12]}"
    resp = client.post(
        "/api/trades",
        headers=auth_headers(user_id),
        json={"ticker": "abc", "side": "BUY", "price": 100, "qty": 10},
    )
    trade_id = resp.json()["trade"]["tradeId"]

    delete_resp = client.delete(f"/api/trades/{trade_id}", headers=auth_headers(other_user))
    assert delete_resp.status_code == 404

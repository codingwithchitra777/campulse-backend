"""Top Lots endpoints (GET /api/lots/open, GET /api/lots/closed).

Open lots rank a user's still-held buy lots by current unrealised P/L; closed
lots rank fully/partially sold buy lots by realised P/L (from allocations).
Both return the {items, total, hasMore} envelope.

Follows the disposable-id + delete-by-exact-id fixture pattern (tests run
against prod Postgres — see CLAUDE.md).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import get_db

client = TestClient(app)


def auth_headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id, role='user')}"}


def _cleanup(uid: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM allocations WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM trades WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


@pytest.fixture
def user_id():
    uid = f"pytest_{uuid.uuid4().hex[:12]}"
    yield uid
    _cleanup(uid)


def _buy(uid, ticker, price, qty):
    return client.post("/api/trades", headers=auth_headers(uid),
                       json={"ticker": ticker, "side": "BUY", "price": price, "qty": qty})


def _sell(uid, ticker, price, qty):
    return client.post("/api/trades", headers=auth_headers(uid),
                       json={"ticker": ticker, "side": "SELL", "price": price, "qty": qty})


def test_open_lots_empty(user_id):
    r = client.get("/api/lots/open", headers=auth_headers(user_id))
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "hasMore": False}


def test_closed_lots_empty(user_id):
    r = client.get("/api/lots/closed", headers=auth_headers(user_id))
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "hasMore": False}


def test_open_lots_lists_held_buy_lots(user_id):
    _buy(user_id, "abc", 7000, 100)
    _buy(user_id, "def", 6000, 50)

    body = client.get("/api/lots/open", headers=auth_headers(user_id)).json()
    assert body["total"] == 2
    assert body["hasMore"] is False
    tickers = {row["ticker"] for row in body["items"]}
    assert tickers == {"ABC", "DEF"}
    row = next(r for r in body["items"] if r["ticker"] == "ABC")
    # Envelope fields every open lot carries.
    for key in ("buyTradeId", "seq", "market", "currency", "buyPrice",
                "qtyOpen", "costBasis"):
        assert key in row
    assert row["market"] == "CSX"
    assert row["currency"] == "KHR"
    assert row["qtyOpen"] == 100
    assert row["costBasis"] == 7000 * 100


def test_open_lots_excludes_fully_sold_lot(user_id):
    _buy(user_id, "abc", 7000, 100)
    _sell(user_id, "abc", 7500, 100)  # fully closes the only lot

    body = client.get("/api/lots/open", headers=auth_headers(user_id)).json()
    assert body["items"] == []
    assert body["total"] == 0


def test_open_lots_pagination(user_id):
    for t in ("aaa", "bbb", "ccc"):
        _buy(user_id, t, 5000, 10)

    page1 = client.get("/api/lots/open", headers=auth_headers(user_id),
                       params={"limit": 2, "offset": 0}).json()
    assert page1["total"] == 3
    assert len(page1["items"]) == 2
    assert page1["hasMore"] is True

    page2 = client.get("/api/lots/open", headers=auth_headers(user_id),
                       params={"limit": 2, "offset": 2}).json()
    assert page2["total"] == 3
    assert len(page2["items"]) == 1
    assert page2["hasMore"] is False


def test_closed_lots_ranked_by_realised_pnl(user_id):
    # Two lots, cheaper one first. A sell spanning both closes both; the
    # cheap lot (7000) realises more than the pricier lot (7300).
    _buy(user_id, "abc", 7000, 100)
    _buy(user_id, "abc", 7300, 100)
    _sell(user_id, "abc", 7500, 200)

    body = client.get("/api/lots/closed", headers=auth_headers(user_id)).json()
    assert body["total"] == 2
    assert body["hasMore"] is False
    # Ranked most-profitable first — the cheaper buy lot leads.
    assert body["items"][0]["buyPrice"] == 7000
    assert body["items"][1]["buyPrice"] == 7300
    assert body["items"][0]["realisedPnl"] >= body["items"][1]["realisedPnl"]
    lead = body["items"][0]
    for key in ("buyTradeId", "seq", "ticker", "market", "currency",
                "qtySold", "avgSellPrice", "realisedPnl", "lastSellDate"):
        assert key in lead
    assert lead["ticker"] == "ABC"
    assert lead["qtySold"] == 100
    assert lead["avgSellPrice"] == 7500


def test_closed_lots_pagination(user_id):
    _buy(user_id, "abc", 7000, 100)
    _buy(user_id, "abc", 7300, 100)
    _sell(user_id, "abc", 7500, 200)

    page1 = client.get("/api/lots/closed", headers=auth_headers(user_id),
                       params={"limit": 1, "offset": 0}).json()
    assert page1["total"] == 2
    assert len(page1["items"]) == 1
    assert page1["hasMore"] is True

    page2 = client.get("/api/lots/closed", headers=auth_headers(user_id),
                       params={"limit": 1, "offset": 1}).json()
    assert len(page2["items"]) == 1
    assert page2["hasMore"] is False
    assert page1["items"][0]["buyTradeId"] != page2["items"][0]["buyTradeId"]


def test_lots_scoped_per_user(user_id):
    other = f"pytest_{uuid.uuid4().hex[:12]}"
    try:
        _buy(user_id, "abc", 7000, 100)
        assert client.get("/api/lots/open", headers=auth_headers(other)).json()["items"] == []
        assert client.get("/api/lots/closed", headers=auth_headers(other)).json()["items"] == []
    finally:
        _cleanup(other)


def test_lots_require_auth():
    assert client.get("/api/lots/open").status_code == 401
    assert client.get("/api/lots/closed").status_code == 401

"""Top Lots — open (unrealised) and closed (realised) rankings, paginated.

Uses the disposable-id / delete-by-exact-id fixture pattern (tests run against
prod Postgres — see CLAUDE.md). Closed lots come straight from allocations, so
recording a BUY then a matched SELL produces one closed lot.
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


@pytest.fixture
def user_id():
    uid = f"pytest_{uuid.uuid4().hex[:12]}"
    yield uid
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))  # cascades to trades/allocations


def _buy(uid, ticker, price, qty):
    return client.post("/api/trades/confirm", headers=auth_headers(uid),
                       json={"ticker": ticker, "side": "BUY", "price": price, "qty": qty,
                             "commission": 0, "market": "CSX", "currency": "KHR"})


def _sell(uid, ticker, price, qty):
    return client.post("/api/trades/confirm", headers=auth_headers(uid),
                       json={"ticker": ticker, "side": "SELL", "price": price, "qty": qty,
                             "commission": 0, "market": "CSX", "currency": "KHR"})


def test_lots_require_auth():
    assert client.get("/api/lots/open").status_code == 401
    assert client.get("/api/lots/closed").status_code == 401


def test_open_lots_shape_and_pagination(user_id):
    _buy(user_id, "ABCD", 1000, 10)
    r = client.get("/api/lots/open", headers=auth_headers(user_id), params={"limit": 10, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert "items" in body and "total" in body and "hasMore" in body
    assert body["total"] >= 1
    lot = next(i for i in body["items"] if i["ticker"] == "ABCD")
    assert lot["qtyOpen"] == 10
    assert lot["buyPrice"] == 1000
    assert "unrealisedPnl" in lot  # may be null if the CSX feed has no quote here


def test_closed_lot_ranks_by_realised(user_id):
    # Buy cheap, sell higher -> a profitable closed lot.
    _buy(user_id, "WINR", 1000, 10)
    _sell(user_id, "WINR", 1500, 10)
    r = client.get("/api/lots/closed", headers=auth_headers(user_id), params={"limit": 10, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    lot = next(i for i in body["items"] if i["ticker"] == "WINR")
    assert lot["qtySold"] == 10
    assert lot["realisedPnl"] == 5000  # (1500-1000)*10
    assert lot["avgSellPrice"] == 1500


def test_closed_pagination_offset(user_id):
    _buy(user_id, "AAA", 100, 5)
    _sell(user_id, "AAA", 200, 5)
    _buy(user_id, "BBB", 100, 5)
    _sell(user_id, "BBB", 150, 5)
    first = client.get("/api/lots/closed", headers=auth_headers(user_id),
                       params={"limit": 1, "offset": 0}).json()
    second = client.get("/api/lots/closed", headers=auth_headers(user_id),
                        params={"limit": 1, "offset": 1}).json()
    assert first["total"] == 2
    assert first["hasMore"] is True
    assert len(first["items"]) == 1 and len(second["items"]) == 1
    # Ranked by realised desc: AAA (500) before BBB (250).
    assert first["items"][0]["ticker"] == "AAA"
    assert second["items"][0]["ticker"] == "BBB"
    assert second["hasMore"] is False

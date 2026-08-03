"""Grid plan CRUD (GET/PUT/DELETE /api/grid).

The plan is a client-owned JSON document; the server just stores one per user.
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


@pytest.fixture
def user_id():
    uid = f"pytest_{uuid.uuid4().hex[:12]}"
    yield uid
    with get_db() as conn:
        with conn.cursor() as cur:
            # grid_plans ON DELETE CASCADE with users, but delete the row too in
            # case the FK ever changes.
            cur.execute("DELETE FROM grid_plans WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


def sample_plan() -> dict:
    return {
        "ticker": "AAPL",
        "market": "US",
        "currency": "USD",
        "lower": 180,
        "upper": 210,
        "levels": 7,
        "qty": 5,
        "spacing": "arith",
        "step": 5,
        "rungs": [
            {"id": 0, "price": 180, "side": "BUY", "fills": 0},
            {"id": 1, "price": 205, "side": "SELL", "fills": 1},
        ],
        "log": [
            {"id": "a", "side": "SELL", "price": 205, "qty": 5, "pnl": 75, "running": 75,
             "at": "2026-08-03T09:31:00Z"},
        ],
        "realised": 75,
        "cycles": 1,
        "openLots": 0,
        "createdAt": "2026-08-03T09:30:00Z",
    }


def test_get_grid_empty(user_id):
    r = client.get("/api/grid", headers=auth_headers(user_id))
    assert r.status_code == 200
    assert r.json() == {"success": True, "plan": None}


def test_put_then_get_roundtrips(user_id):
    r = client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan())
    assert r.status_code == 200
    assert r.json()["success"] is True

    got = client.get("/api/grid", headers=auth_headers(user_id)).json()["plan"]
    assert got["ticker"] == "AAPL"
    assert got["market"] == "US"
    assert got["realised"] == 75
    assert got["openLots"] == 0
    assert len(got["rungs"]) == 2
    assert got["rungs"][1]["side"] == "SELL"
    assert got["log"][0]["pnl"] == 75
    assert got["updatedAt"]  # server-stamped on write


def test_put_is_upsert(user_id):
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan())
    plan2 = sample_plan()
    plan2["ticker"] = "MSFT"
    plan2["realised"] = 120
    client.put("/api/grid", headers=auth_headers(user_id), json=plan2)

    got = client.get("/api/grid", headers=auth_headers(user_id)).json()["plan"]
    assert got["ticker"] == "MSFT"
    assert got["realised"] == 120


def test_delete_grid(user_id):
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan())
    r = client.delete("/api/grid", headers=auth_headers(user_id))
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert client.get("/api/grid", headers=auth_headers(user_id)).json()["plan"] is None


def test_grid_requires_auth():
    assert client.get("/api/grid").status_code == 401
    assert client.put("/api/grid", json=sample_plan()).status_code == 401


def test_put_rejects_bad_plan(user_id):
    bad = sample_plan()
    del bad["ticker"]  # required field missing
    r = client.put("/api/grid", headers=auth_headers(user_id), json=bad)
    assert r.status_code == 422

"""Grid plan CRUD (GET /api/grid, PUT /api/grid, DELETE /api/grid/{id}).

Each plan is a client-owned JSON document; the server stores several per user
(one row per grid_id) and scopes every read/write/delete to the owner. Follows
the disposable-id + delete-by-exact-id fixture pattern (tests run against prod
Postgres — see CLAUDE.md).
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
            cur.execute("DELETE FROM grid_plans WHERE user_id = %s", (uid,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


@pytest.fixture
def user_id():
    uid = f"pytest_{uuid.uuid4().hex[:12]}"
    yield uid
    _cleanup(uid)


def sample_plan(grid_id: str = "g1", ticker: str = "AAPL") -> dict:
    return {
        "id": grid_id,
        "ticker": ticker,
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
    assert r.json() == {"success": True, "plans": []}


def test_put_then_get_roundtrips(user_id):
    r = client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan())
    assert r.status_code == 200
    assert r.json()["success"] is True

    plans = client.get("/api/grid", headers=auth_headers(user_id)).json()["plans"]
    assert len(plans) == 1
    got = plans[0]
    assert got["id"] == "g1"
    assert got["ticker"] == "AAPL"
    assert got["realised"] == 75
    assert len(got["rungs"]) == 2
    assert got["log"][0]["pnl"] == 75
    assert got["updatedAt"]  # server-stamped on write


def test_multiple_grids_per_user(user_id):
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan("g1", "AAPL"))
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan("g2", "MSFT"))
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan("g3", "NVDA"))

    plans = client.get("/api/grid", headers=auth_headers(user_id)).json()["plans"]
    assert len(plans) == 3
    assert {p["ticker"] for p in plans} == {"AAPL", "MSFT", "NVDA"}


def test_put_is_upsert_per_grid(user_id):
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan("g1", "AAPL"))
    updated = sample_plan("g1", "AAPL")
    updated["realised"] = 120
    client.put("/api/grid", headers=auth_headers(user_id), json=updated)

    plans = client.get("/api/grid", headers=auth_headers(user_id)).json()["plans"]
    assert len(plans) == 1  # same id → updated, not duplicated
    assert plans[0]["realised"] == 120


def test_delete_one_grid_leaves_others(user_id):
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan("g1", "AAPL"))
    client.put("/api/grid", headers=auth_headers(user_id), json=sample_plan("g2", "MSFT"))

    r = client.delete("/api/grid/g1", headers=auth_headers(user_id))
    assert r.status_code == 200 and r.json()["deleted"] is True

    plans = client.get("/api/grid", headers=auth_headers(user_id)).json()["plans"]
    assert [p["id"] for p in plans] == ["g2"]


def test_cannot_touch_another_users_grid():
    owner = f"pytest_{uuid.uuid4().hex[:12]}"
    attacker = f"pytest_{uuid.uuid4().hex[:12]}"
    try:
        client.put("/api/grid", headers=auth_headers(owner), json=sample_plan("shared_id", "AAPL"))

        # Attacker tries to overwrite the same grid_id — must not clobber it.
        hijack = sample_plan("shared_id", "HACK")
        client.put("/api/grid", headers=auth_headers(attacker), json=hijack)
        owner_plan = client.get("/api/grid", headers=auth_headers(owner)).json()["plans"][0]
        assert owner_plan["ticker"] == "AAPL"  # untouched

        # Attacker tries to delete it — reports nothing deleted, owner keeps it.
        d = client.delete("/api/grid/shared_id", headers=auth_headers(attacker)).json()
        assert d["deleted"] is False
        assert len(client.get("/api/grid", headers=auth_headers(owner)).json()["plans"]) == 1
    finally:
        _cleanup(owner)
        _cleanup(attacker)


def test_grid_requires_auth():
    assert client.get("/api/grid").status_code == 401
    assert client.put("/api/grid", json=sample_plan()).status_code == 401
    assert client.delete("/api/grid/g1").status_code == 401


def test_put_rejects_bad_plan(user_id):
    bad = sample_plan()
    del bad["ticker"]  # required field missing
    r = client.put("/api/grid", headers=auth_headers(user_id), json=bad)
    assert r.status_code == 422

    no_id = sample_plan()
    del no_id["id"]  # id is now required
    assert client.put("/api/grid", headers=auth_headers(user_id), json=no_id).status_code == 422

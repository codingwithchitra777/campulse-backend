"""Trade journal: a note + tags per trade, editable on any of the user's trades."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token

client = TestClient(app)


def auth_headers(user_id: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id, role='user')}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


def _buy(uid):
    return client.post("/api/trades", headers=auth_headers(uid),
                       json={"ticker": "abc", "side": "BUY", "price": 7000, "qty": 10, "commission": 0})


def test_add_and_read_journal_note(user_id):
    trade_id = _buy(user_id).json()["trade"]["tradeId"]
    r = client.patch(f"/api/trades/{trade_id}/journal", headers=auth_headers(user_id),
                     json={"note": "Bought the dip after earnings", "tags": "earnings, conviction"})
    assert r.status_code == 200
    assert r.json()["note"] == "Bought the dip after earnings"
    assert r.json()["tags"] == "earnings, conviction"

    listed = client.get("/api/trades", headers=auth_headers(user_id)).json()["items"]
    assert listed[0]["note"] == "Bought the dip after earnings"
    assert listed[0]["tags"] == "earnings, conviction"


def test_journal_allowed_on_sell_trade(user_id):
    _buy(user_id)
    sell = client.post("/api/trades", headers=auth_headers(user_id),
                       json={"ticker": "abc", "side": "SELL", "price": 7500, "qty": 5, "commission": 0})
    sell_id = sell.json()["trade"]["tradeId"]
    # Price edits are blocked on SELLs, but journal notes are metadata and allowed.
    r = client.patch(f"/api/trades/{sell_id}/journal", headers=auth_headers(user_id),
                     json={"note": "Took profit", "tags": "win"})
    assert r.status_code == 200
    assert r.json()["note"] == "Took profit"


def test_clearing_note_sets_null(user_id):
    trade_id = _buy(user_id).json()["trade"]["tradeId"]
    client.patch(f"/api/trades/{trade_id}/journal", headers=auth_headers(user_id), json={"note": "x", "tags": "y"})
    r = client.patch(f"/api/trades/{trade_id}/journal", headers=auth_headers(user_id), json={"note": "  ", "tags": ""})
    assert r.status_code == 200
    assert r.json()["note"] is None
    assert r.json()["tags"] is None


def test_journal_on_other_users_trade_404(user_id):
    trade_id = _buy(user_id).json()["trade"]["tradeId"]
    other = f"pytest_{uuid.uuid4().hex[:12]}"
    r = client.patch(f"/api/trades/{trade_id}/journal", headers=auth_headers(other), json={"note": "hax"})
    assert r.status_code == 404

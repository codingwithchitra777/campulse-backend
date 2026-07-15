"""Price alerts: CRUD endpoints + the checker's cross/deliver logic.

The delivery test injects a fake alert repo so check_once() only ever sees this
test's alert — it must never evaluate other users' real alerts in the shared DB.
"""
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import get_db
from app.repositories.user import UserRepository
from app.repositories.link import LinkRepository
from app.services.alert_service import AlertService, _crossed, resolve_chat_id

client = TestClient(app)


def auth_headers(uid: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=uid, role='user')}"}


@pytest.fixture
def uid():
    u = f"pytest_{uuid.uuid4().hex[:12]}"
    yield u
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (u,))


class _Res:
    def __init__(self, price):
        self.price = price
        self.change = None
        self.change_direction = None


class _Router:
    def __init__(self, price):
        self._price = price

    def get_latest_price(self, market, symbol):
        return _Res(self._price)


class _FakeAlertRepo:
    def __init__(self, alerts):
        self._alerts = alerts
        self.triggered = []

    def list_active(self):
        return list(self._alerts)

    def mark_triggered(self, alert_id):
        self.triggered.append(alert_id)


# ---- pure logic ----

def test_crossed_logic():
    assert _crossed("above", Decimal(320), Decimal(300)) is True
    assert _crossed("above", Decimal(290), Decimal(300)) is False
    assert _crossed("below", Decimal(290), Decimal(300)) is True
    assert _crossed("below", Decimal(320), Decimal(300)) is False


# ---- CRUD endpoints ----

def test_alert_crud_and_deliverable_flag(uid):
    r = client.post("/api/alerts", headers=auth_headers(uid),
                    json={"symbol": "aapl", "targetPrice": 300, "market": "US",
                          "currency": "USD", "direction": "above"})
    assert r.status_code == 200
    body = r.json()
    assert body["alert"]["symbol"] == "AAPL"
    assert body["alert"]["direction"] == "above"
    assert body["deliverable"] is False  # a JWT-only user has no linked Telegram
    alert_id = body["alert"]["alertId"]

    listed = client.get("/api/alerts", headers=auth_headers(uid)).json()
    assert any(a["alertId"] == alert_id for a in listed["items"])

    assert client.delete(f"/api/alerts/{alert_id}", headers=auth_headers(uid)).status_code == 200
    listed2 = client.get("/api/alerts", headers=auth_headers(uid)).json()
    assert not any(a["alertId"] == alert_id for a in listed2["items"])


def test_delete_missing_alert_404(uid):
    r = client.delete("/api/alerts/does-not-exist", headers=auth_headers(uid))
    assert r.status_code == 404


# ---- checker delivery (isolated fake repo) ----

def test_check_once_delivers_when_crossed(uid):
    UserRepository().upsert_user(uid, "Tg User", chat_id=999123)
    alert = {"alertId": "t1", "userId": uid, "market": "US", "symbol": "AAPL",
             "currency": "USD", "targetPrice": Decimal("300"), "direction": "above"}
    fake = _FakeAlertRepo([alert])
    captured = []
    svc = AlertService(fake, UserRepository(), LinkRepository(), _Router(320.0),
                       lambda cid, text: captured.append((cid, text)))

    delivered = svc.check_once()
    assert delivered == 1
    assert captured and captured[0][0] == 999123
    assert "AAPL" in captured[0][1]
    assert fake.triggered == ["t1"]


def test_check_once_skips_when_not_crossed(uid):
    UserRepository().upsert_user(uid, "Tg User", chat_id=999124)
    alert = {"alertId": "t2", "userId": uid, "market": "US", "symbol": "AAPL",
             "currency": "USD", "targetPrice": Decimal("500"), "direction": "above"}
    fake = _FakeAlertRepo([alert])
    captured = []
    svc = AlertService(fake, UserRepository(), LinkRepository(), _Router(320.0),
                       lambda cid, text: captured.append((cid, text)))

    assert svc.check_once() == 0
    assert captured == []
    assert fake.triggered == []


def test_check_once_leaves_active_when_undeliverable(uid):
    # No chat_id anywhere for this user => cannot deliver => stays active.
    UserRepository().upsert_user(uid, "Web User")
    alert = {"alertId": "t3", "userId": uid, "market": "US", "symbol": "AAPL",
             "currency": "USD", "targetPrice": Decimal("300"), "direction": "above"}
    fake = _FakeAlertRepo([alert])
    svc = AlertService(fake, UserRepository(), LinkRepository(), _Router(320.0), lambda c, t: None)

    assert svc.check_once() == 0
    assert fake.triggered == []
    assert resolve_chat_id(uid, UserRepository(), LinkRepository()) is None

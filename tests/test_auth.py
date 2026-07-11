import hashlib
import hmac
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.db.database import get_db

client = TestClient(app)


def sign_telegram_payload(payload: dict) -> dict:
    """Sign a widget payload exactly like Telegram does, using the configured token."""
    fields = {k: v for k, v in payload.items() if v is not None}
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret_key = hashlib.sha256(settings.telegram_token.encode()).digest()
    payload["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return payload


@pytest.fixture
def tg_id():
    # Large random id far outside real Telegram id ranges seen in this DB.
    tg_id = int(uuid.uuid4().int % 10**9) + 9 * 10**14
    yield tg_id
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (str(tg_id),))


def _payload(tg_id: int, **overrides) -> dict:
    base = {
        "id": tg_id,
        "first_name": "Pytest",
        "last_name": "Telegram",
        "username": "pytest_tg",
        "photo_url": None,
        "auth_date": int(time.time()),
    }
    base.update(overrides)
    return base


def test_telegram_login_success(tg_id):
    resp = client.post("/api/auth/telegram", json=sign_telegram_payload(_payload(tg_id)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["userId"] == str(tg_id)
    assert body["userName"] == "Pytest Telegram"
    assert body["role"] == "user"

    # The issued JWT works against a protected endpoint.
    trades = client.get("/api/trades", headers={"Authorization": f"Bearer {body['token']}"})
    assert trades.status_code == 200


def test_telegram_login_tampered_payload_rejected(tg_id):
    payload = sign_telegram_payload(_payload(tg_id))
    payload["first_name"] = "Mallory"
    resp = client.post("/api/auth/telegram", json=payload)
    assert resp.status_code == 401


def test_telegram_login_stale_auth_date_rejected(tg_id):
    payload = sign_telegram_payload(_payload(tg_id, auth_date=int(time.time()) - 90000))
    resp = client.post("/api/auth/telegram", json=payload)
    assert resp.status_code == 401


def sign_webapp_init_data(tg_id: int, auth_date: int = None, first_name: str = "Pytest") -> str:
    """Build a Mini App initData string signed exactly like Telegram does."""
    import json
    from urllib.parse import urlencode

    pairs = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAtest",
        "user": json.dumps({"id": tg_id, "first_name": first_name, "last_name": "WebApp"}),
    }
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", settings.telegram_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_telegram_webapp_login_success(tg_id):
    resp = client.post("/api/auth/telegram-webapp", json={"initData": sign_webapp_init_data(tg_id)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["userId"] == str(tg_id)
    assert body["userName"] == "Pytest WebApp"

    trades = client.get("/api/trades", headers={"Authorization": f"Bearer {body['token']}"})
    assert trades.status_code == 200


def test_telegram_webapp_tampered_rejected(tg_id):
    init_data = sign_webapp_init_data(tg_id).replace("Pytest", "Mallory")
    resp = client.post("/api/auth/telegram-webapp", json={"initData": init_data})
    assert resp.status_code == 401


def test_telegram_webapp_stale_rejected(tg_id):
    init_data = sign_webapp_init_data(tg_id, auth_date=int(time.time()) - 90000)
    resp = client.post("/api/auth/telegram-webapp", json={"initData": init_data})
    assert resp.status_code == 401


def test_telegram_login_is_idempotent_upsert(tg_id):
    first = client.post("/api/auth/telegram", json=sign_telegram_payload(_payload(tg_id)))
    second = client.post(
        "/api/auth/telegram",
        json=sign_telegram_payload(_payload(tg_id, first_name="Renamed")),
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["userId"] == second.json()["userId"]
    assert second.json()["userName"] == "Renamed Telegram"

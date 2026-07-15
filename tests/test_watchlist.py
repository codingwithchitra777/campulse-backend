"""Watchlist CRUD + the news endpoint shape (live news needs FINNHUB_API_KEY)."""
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
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


def test_watchlist_add_list_remove(user_id):
    r = client.post("/api/watchlist", headers=auth_headers(user_id),
                    json={"symbol": "aapl", "market": "US", "currency": "USD"})
    assert r.status_code == 200

    items = client.get("/api/watchlist", headers=auth_headers(user_id)).json()["items"]
    aapl = next(i for i in items if i["symbol"] == "AAPL")
    assert aapl["market"] == "US"
    assert aapl["currency"] == "USD"
    assert "price" in aapl  # quote resolved through the router (may be None without a key)

    r = client.delete("/api/watchlist/AAPL", headers=auth_headers(user_id), params={"market": "US"})
    assert r.status_code == 200
    items2 = client.get("/api/watchlist", headers=auth_headers(user_id)).json()["items"]
    assert "AAPL" not in [i["symbol"] for i in items2]


def test_watchlist_defaults_to_csx(user_id):
    client.post("/api/watchlist", headers=auth_headers(user_id), json={"symbol": "pwsa"})
    items = client.get("/api/watchlist", headers=auth_headers(user_id)).json()["items"]
    it = next(i for i in items if i["symbol"] == "PWSA")
    assert it["market"] == "CSX"
    assert it["currency"] == "KHR"


def test_watchlist_add_is_idempotent(user_id):
    client.post("/api/watchlist", headers=auth_headers(user_id), json={"symbol": "pwsa"})
    client.post("/api/watchlist", headers=auth_headers(user_id), json={"symbol": "pwsa"})
    items = client.get("/api/watchlist", headers=auth_headers(user_id)).json()["items"]
    assert sum(1 for i in items if i["symbol"] == "PWSA") == 1


def test_remove_missing_returns_404(user_id):
    r = client.delete("/api/watchlist/NOPE", headers=auth_headers(user_id), params={"market": "US"})
    assert r.status_code == 404


def test_news_endpoint_shape():
    r = client.get("/api/market/news/AAPL")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert isinstance(body["news"], list)

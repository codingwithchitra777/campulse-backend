"""Phase 3: local Cambodian gold — an admin-priced (manual) instrument.

Uses a disposable GOLD_KH symbol (XAU-TEST) so the real XAU-KH board is never
touched. Cleans up the manual price row and the pytest_ user on teardown.
"""
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import get_db
from app.repositories.manual_price import ManualPriceRepository
from app.services.manual_provider import ManualProvider

client = TestClient(app)

MARKET = "GOLD_KH"
SYMBOL = "XAU-TEST"


def auth_headers(user_id: str, role: str = "user") -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=user_id, role=role)}"}


@pytest.fixture
def gold():
    user_id = f"pytest_{uuid.uuid4().hex[:12]}"
    yield user_id
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM manual_prices WHERE market = %s AND symbol = %s", (MARKET, SYMBOL))


# ---- ManualProvider ----

def test_manual_provider_reads_admin_price(gold):
    ManualPriceRepository().upsert(MARKET, SYMBOL, Decimal("512.5"), currency="USD", change=Decimal("2.5"))
    res = ManualProvider(MARKET).get_latest_price(SYMBOL)
    assert res.price == 512.5
    assert res.change_direction == "up"


def test_manual_provider_unset_is_none(gold):
    assert ManualProvider(MARKET).get_latest_price("XAU-NOPE-" + uuid.uuid4().hex[:6]).price is None


# ---- Admin board endpoints ----

def test_admin_sets_gold_price_and_lists(gold):
    admin = auth_headers("pytest_admin_" + uuid.uuid4().hex[:8], role="admin")
    r = client.put("/api/admin/manual-price", headers=admin,
                   json={"market": MARKET, "symbol": SYMBOL, "price": 505.25, "currency": "USD"})
    assert r.status_code == 200
    assert r.json()["success"] is True

    listed = client.get("/api/admin/manual-prices", headers=admin).json()["items"]
    row = next(x for x in listed if x["symbol"] == SYMBOL)
    assert float(row["price"]) == 505.25
    assert row["currency"] == "USD"


def test_non_admin_cannot_set_gold_price(gold):
    r = client.put("/api/admin/manual-price", headers=auth_headers(gold),
                   json={"market": MARKET, "symbol": SYMBOL, "price": 500})
    assert r.status_code == 403


def test_manual_price_rejects_non_manual_market(gold):
    admin = auth_headers("pytest_admin_" + uuid.uuid4().hex[:8], role="admin")
    r = client.put("/api/admin/manual-price", headers=admin,
                   json={"market": "US", "symbol": "AAPL", "price": 100})
    assert r.status_code == 400


# ---- Gold recorded as a normal trade, valued by the board ----

def test_gold_trade_valued_in_usd(gold):
    # Buy 2 chi of local gold at 500 USD/chi.
    resp = client.post("/api/trades", headers=auth_headers(gold),
                       json={"ticker": SYMBOL, "side": "BUY", "price": 500, "qty": 2,
                             "commission": 0, "market": MARKET, "currency": "USD"})
    assert resp.status_code == 200
    assert resp.json()["trade"]["market"] == MARKET
    assert resp.json()["trade"]["currency"] == "USD"

    # Admin board marks gold up to 550/chi.
    ManualPriceRepository().upsert(MARKET, SYMBOL, Decimal("550"), currency="USD")

    portfolio = client.get("/api/portfolio", headers=auth_headers(gold)).json()
    pos = next(p for p in portfolio if p["ticker"] == SYMBOL)
    assert pos["market"] == MARKET
    assert pos["currency"] == "USD"
    assert float(pos["lastPrice"]) == 550.0
    # 2 chi * (550 - 500) = 100 USD unrealised.
    assert float(pos["unrealisedPnl"]) == 100.0

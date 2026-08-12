"""Phase 3: local Cambodian gold — an admin-priced instrument, valued off the
`price_history` board. That's the single write path shared by `/setgold`, the
gold-OCR photo flow, and `/api/admin/gold-history` — there is no separate
"manual price" table anymore (it used to exist and silently desync from
whichever of those flows was used most recently; don't reintroduce it).

Uses a disposable GOLD_KH symbol (XAU-TEST) so the real XAU-KH board is never
touched. Cleans up its price_history rows and the pytest_ user on teardown.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import get_db
from app.repositories.price_history import PriceHistoryRepository
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
            cur.execute("DELETE FROM price_history WHERE ticker = %s", (SYMBOL,))


# ---- ManualProvider (portfolio valuation) reads price_history ----

def test_manual_provider_reads_price_history(gold):
    PriceHistoryRepository().upsert_snapshot(SYMBOL, date.today(), Decimal("512.5"), market=MARKET,
                                             bid_price=Decimal("515"), ask_price=Decimal("510"))
    res = ManualProvider(MARKET).get_latest_price(SYMBOL)
    assert res.price == 512.5
    assert res.raw["bidPrice"] == Decimal("515")


def test_manual_provider_unset_is_none(gold):
    assert ManualProvider(MARKET).get_latest_price("XAU-NOPE-" + uuid.uuid4().hex[:6]).price is None


# ---- Admin gold-history endpoint (the one true write path) ----

def test_non_admin_cannot_add_gold_price(gold):
    r = client.post("/api/admin/gold-history", headers=auth_headers(gold),
                    json={"bidPrice": 500, "askPrice": 495, "effectiveDate": str(date.today())})
    assert r.status_code == 403


# ---- Gold recorded as a normal trade, valued off the price_history board ----

def test_gold_trade_valued_in_usd(gold):
    # Buy 2 chi of local gold at 500 USD/chi.
    resp = client.post("/api/trades", headers=auth_headers(gold),
                       json={"ticker": SYMBOL, "side": "BUY", "price": 500, "qty": 2,
                             "commission": 0, "market": MARKET, "currency": "USD"})
    assert resp.status_code == 200
    assert resp.json()["trade"]["market"] == MARKET
    assert resp.json()["trade"]["currency"] == "USD"

    # Today's gold snapshot marks it up to 550/chi (same table /setgold and the
    # OCR flow write to).
    PriceHistoryRepository().upsert_snapshot(SYMBOL, date.today(), Decimal("550"), market=MARKET)

    portfolio = client.get("/api/portfolio", headers=auth_headers(gold)).json()
    pos = next(p for p in portfolio if p["ticker"] == SYMBOL)
    assert pos["market"] == MARKET
    assert pos["currency"] == "USD"
    assert float(pos["lastPrice"]) == 550.0
    # 2 chi * (550 - 500) = 100 USD unrealised.
    assert float(pos["unrealisedPnl"]) == 100.0

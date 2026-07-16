"""AI Coach: snapshot safety, caching, and the guardrails.

No test here ever calls Anthropic — the client is injected. The load-bearing test
is test_snapshot_leaks_no_pii: build_snapshot() is what leaves our server, so if
it ever starts passing a source dict through, that test must fail.
"""
import json
import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token
from app.db.database import get_db
from app.services import ai_coach
from app.services.ai_coach import (AICoachService, build_snapshot, snapshot_hash,
                                   thin_data_message, MIN_CLOSED_TRADES)

client = TestClient(app)


def auth_headers(uid: str) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id=uid, role='user')}"}


@pytest.fixture
def uid():
    u = f"pytest_{uuid.uuid4().hex[:12]}"
    yield u
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ai_insights WHERE user_id = %s", (u,))
            cur.execute("DELETE FROM users WHERE user_id = %s", (u,))


def _stats(closed=8, **over):
    """Shaped like AnalyticsService.compute() output."""
    base = {
        "tradeCount": 20, "buyCount": 12, "sellCount": 8,
        "closedTradeCount": closed, "wins": 5, "losses": 3, "winRate": 62.5,
        "avgHoldDays": 14.25,
        "bestTrade": {"ticker": "PPSP", "market": "CSX", "currency": "KHR",
                      "realisedPnl": Decimal("120000.0000"), "sellDate": "2026-05-01T00:00:00"},
        "worstTrade": {"ticker": "AAPL", "market": "US", "currency": "USD",
                       "realisedPnl": Decimal("-40.5000"), "sellDate": "2026-06-01T00:00:00"},
        "byCurrency": [{"currency": "KHR", "realisedPnl": Decimal("90000"),
                        "unrealisedPnl": Decimal("5000"), "invested": Decimal("400000"),
                        "value": Decimal("405000"), "wins": 4, "losses": 1}],
        "byMarket": [{"market": "CSX", "currency": "KHR", "positions": 3,
                      "invested": Decimal("400000")}],
        "byTag": [{"tag": "plan", "trades": 7, "wins": 5, "losses": 2, "winRate": 71.4},
                  {"tag": "fomo", "trades": 5, "wins": 1, "losses": 4, "winRate": 20.0}],
    }
    base.update(over)
    return base


class _FakeMessages:
    def __init__(self, text="Your #plan trades win 71% versus 20% on #fomo.", stop_reason="end_turn"):
        self._text = text
        self._stop = stop_reason
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        block = type("B", (), {"type": "text", "text": self._text})()
        return type("R", (), {"content": [block], "stop_reason": self._stop,
                              "model": kw["model"]})()


class _FakeClient:
    def __init__(self, **kw):
        self.messages = _FakeMessages(**kw)


# ---- snapshot safety (the important one) ----

def test_snapshot_leaks_no_pii():
    """Anything identifying must be dropped, including fields we never expect."""
    stats = _stats()
    # Simulate a future column accidentally reaching the analytics output.
    stats["userId"] = "u_secret_42"
    stats["userName"] = "Chitra Sem"
    stats["email"] = "chitrasem001@gmail.com"
    stats["chat_id"] = 123456789
    stats["bestTrade"]["userId"] = "u_secret_42"

    blob = json.dumps(build_snapshot(stats), default=str)
    for leak in ("u_secret_42", "Chitra", "chitrasem001", "123456789", "userId", "email"):
        assert leak not in blob, f"snapshot leaked {leak!r}"


def test_snapshot_keeps_the_signal():
    s = build_snapshot(_stats())
    assert s["winRatePct"] == 62.5
    assert s["avgHoldDays"] == 14.2  # rounded to 1dp in the snapshot
    assert s["bestTrade"]["ticker"] == "PPSP"
    # Decimals must survive as JSON numbers, not "Decimal('120000.0000')".
    assert s["bestTrade"]["realisedPnl"] == 120000.0
    assert {t["tag"] for t in s["byTag"]} == {"plan", "fomo"}
    json.dumps(s)  # must be serialisable with no custom encoder


def test_snapshot_hash_is_stable_and_change_sensitive():
    a = build_snapshot(_stats())
    b = build_snapshot(_stats())
    assert snapshot_hash(a) == snapshot_hash(b)
    assert snapshot_hash(a) != snapshot_hash(build_snapshot(_stats(closed=9)))


# ---- guardrails ----

def test_thin_data_short_circuits_before_spending_a_token():
    fake = _FakeClient()
    svc = AICoachService(client=fake, model="claude-opus-4-8")
    out = svc.generate(build_snapshot(_stats(closed=MIN_CLOSED_TRADES - 1)))
    assert "closed trade" in out
    assert fake.messages.calls == []  # never called the API


def test_generate_calls_model_and_returns_text():
    fake = _FakeClient()
    svc = AICoachService(client=fake, model="claude-opus-4-8")
    out = svc.generate(build_snapshot(_stats()))
    assert "#plan" in out
    assert len(fake.messages.calls) == 1
    sent = fake.messages.calls[0]
    assert sent["model"] == "claude-opus-4-8"
    assert "MUST NOT" in sent["system"]  # the no-advice guardrail is in the prompt
    assert "u_secret" not in json.dumps(sent["messages"])


def test_refusal_is_handled_not_raised():
    svc = AICoachService(client=_FakeClient(stop_reason="refusal"), model="claude-opus-4-8")
    out = svc.generate(build_snapshot(_stats()))
    assert "could not summarise" in out


def test_disclaimer_is_server_side():
    assert "not financial" in ai_coach.DISCLAIMER.lower()


# ---- endpoints ----

def test_get_insights_works_with_no_key_and_no_credit(uid, monkeypatch):
    """The free coach is the product: GET must never depend on a key or billing."""
    monkeypatch.setattr(ai_coach, "is_configured", lambda: False)
    r = client.get("/api/ai/insights", headers=auth_headers(uid))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "rules"
    assert body["aiEnabled"] is False
    assert body["ai"] is None
    assert body["insight"]  # a brand-new user still gets the thin-data note
    assert body["disclaimer"]


def test_refresh_returns_503_when_no_key(uid, monkeypatch):
    monkeypatch.setattr(ai_coach, "is_configured", lambda: False)
    r = client.post("/api/ai/insights", headers=auth_headers(uid))
    assert r.status_code == 503


def test_refresh_rejects_thin_data_without_caching(uid, monkeypatch):
    """A fresh user (0 closed trades) must not mint a cached 'AI insight'."""
    monkeypatch.setattr(ai_coach, "is_configured", lambda: True)
    r = client.post("/api/ai/insights", headers=auth_headers(uid))
    assert r.status_code == 409
    assert "closed trade" in r.json()["detail"]
    from app.repositories.ai_insight import AIInsightRepository
    assert AIInsightRepository().get(uid) is None


def test_insights_require_auth():
    assert client.get("/api/ai/insights").status_code == 401


def test_health_requires_admin(uid):
    assert client.get("/api/ai/health", headers=auth_headers(uid)).status_code == 403

"""Account-linking tests: Google is canonical, a Telegram id links into it via a
one-time code redeemed by the bot's /start <code>. Covers the LinkRepository,
resolve_primary, the bot link+route-to-primary flow, and the link endpoints.

All rows are created under disposable ids and deleted by exact id on teardown
(deleting a user cascades to user_links/link_codes/trades/allocations)."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.db.database import get_db
from app.repositories.user import UserRepository
from app.repositories.trade import TradeRepository
from app.repositories.link import LinkRepository
from app.services.identity import resolve_primary
from app.services.telegram_bot import TelegramBotService
from app.core.security import create_access_token

client = TestClient(app)


def _uid(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class CaptureClient:
    def __init__(self):
        self.messages = []
        self.photos = []

    def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id, image, caption=None):
        if hasattr(image, "read"):
            image.read()
        self.photos.append((chat_id, caption))


@pytest.fixture
def ids():
    """A disposable (google-primary, telegram-alias) pair; cleaned by exact id."""
    primary = _uid("gtest")
    alias = str(int(uuid.uuid4().int % 10**9) + 7 * 10**14)  # telegram-shaped id
    yield primary, alias
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = ANY(%s)", ([primary, alias],))


# ---- LinkRepository ----

def test_code_is_single_use(ids):
    primary, _ = ids
    UserRepository().upsert_user(primary, "Primary")
    repo = LinkRepository()
    code = repo.create_code(primary)
    assert repo.consume_code(code) == primary
    assert repo.consume_code(code) is None  # already used


def test_code_expires(ids):
    primary, _ = ids
    UserRepository().upsert_user(primary, "Primary")
    repo = LinkRepository()
    code = repo.create_code(primary, ttl_minutes=-1)  # already expired
    assert repo.consume_code(code) is None


def test_add_and_resolve_and_remove(ids):
    primary, alias = ids
    ur = UserRepository()
    ur.upsert_user(primary, "Primary")
    ur.upsert_user(alias, "Alias", chat_id=int(alias))
    repo = LinkRepository()

    assert resolve_primary(alias, repo) == alias  # unlinked -> self
    repo.add_link(alias, primary)
    assert resolve_primary(alias, repo) == primary
    assert repo.remove_link(alias) is True
    assert resolve_primary(alias, repo) == alias


def test_migrate_data_renumbers_seq(ids):
    primary, alias = ids
    ur = UserRepository()
    ur.upsert_user(primary, "Primary")
    ur.upsert_user(alias, "Alias", chat_id=int(alias))
    tr = TradeRepository()
    from app.services.trade_service import record_trade
    alloc_repo = None
    from app.repositories.allocation import AllocationRepository
    alloc_repo = AllocationRepository()
    # primary already has 1 trade (seq 1); alias has 2 trades (seq 1,2)
    record_trade(tr, alloc_repo, primary, "ABC", "BUY", 100, 10)
    record_trade(tr, alloc_repo, alias, "XYZ", "BUY", 50, 5)
    record_trade(tr, alloc_repo, alias, "XYZ", "BUY", 60, 5)

    moved = LinkRepository().migrate_data(alias, primary)
    assert moved == 2
    seqs = sorted(t["seq"] for t in tr.list_trades(primary))
    assert seqs == [1, 2, 3]          # alias trades renumbered above primary's max
    assert tr.list_trades(alias) == []  # nothing left on the alias


# ---- Bot linking flow ----

def test_start_code_links_and_trade_routes_to_primary(ids):
    primary, alias = ids
    UserRepository().upsert_user(primary, "Primary")
    code = LinkRepository().create_code(primary)

    cap = CaptureClient()
    svc = TelegramBotService(client=cap)

    # /start <code> from the deep link links this telegram id to the primary.
    svc.dispatch(alias, int(alias), "Tg User", f"/start {code}")
    assert any("Linked" in m[1] for m in cap.messages)
    assert svc.link_repo.get_primary(alias) == primary

    # A subsequent /buy from the bot lands on the PRIMARY account, not the alias.
    svc.dispatch(alias, int(alias), "Tg User", "/buy$ABC 100 10")
    assert svc.trade_repo.list_trades(primary)          # primary got the trade
    assert svc.trade_repo.list_trades(alias) == []      # alias has none


def test_start_bad_code_does_not_link(ids):
    primary, alias = ids
    cap = CaptureClient()
    svc = TelegramBotService(client=cap)
    svc.dispatch(alias, int(alias), "Tg User", "/start deadbeefnope")
    assert any("invalid or expired" in m[1] for m in cap.messages)
    assert svc.link_repo.get_primary(alias) == alias  # unchanged


# ---- Link endpoints ----

def test_link_endpoints_roundtrip(ids):
    primary, alias = ids
    ur = UserRepository()
    ur.upsert_user(primary, "Primary")
    ur.upsert_user(alias, "Alias", chat_id=int(alias))
    token = create_access_token(user_id=primary, role="user")
    headers = {"Authorization": f"Bearer {token}"}

    # mint a code + deep link
    r = client.post("/api/auth/link/code", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["code"] and body["deepLink"].endswith(body["code"])

    # link it (simulate the bot consuming the code), then it shows up
    LinkRepository().add_link(alias, primary)
    r = client.get("/api/auth/links", headers=headers)
    assert r.status_code == 200
    aliases = [l["aliasUserId"] for l in r.json()["links"]]
    assert alias in aliases

    # disconnect
    r = client.delete(f"/api/auth/links/{alias}", headers=headers)
    assert r.status_code == 200
    assert resolve_primary(alias) == alias


def test_cannot_remove_others_link(ids):
    primary, alias = ids
    ur = UserRepository()
    ur.upsert_user(primary, "Primary")
    ur.upsert_user(alias, "Alias", chat_id=int(alias))
    LinkRepository().add_link(alias, primary)

    # a different user must not be able to unlink someone else's alias
    other = _uid("gother")
    ur.upsert_user(other, "Other")
    token = create_access_token(user_id=other, role="user")
    try:
        r = client.delete(f"/api/auth/links/{alias}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404
        assert resolve_primary(alias) == primary  # still linked
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE user_id = %s", (other,))

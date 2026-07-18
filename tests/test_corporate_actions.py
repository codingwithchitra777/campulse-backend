"""Corporate actions (bonus/split). All money-touching, so the invariant matters:
total invested must be identical before and after, to the riel, and no zero-cost
lot may ever be created (it would poison the cheapest-first matcher).

Everything runs on disposable pytest_ users and a fake symbol, deleted by exact
id on teardown — never touches real holdings in the shared prod DB.
"""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.db.database import get_db
from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.repositories.alert import AlertRepository
from app.repositories.user import UserRepository
from app.repositories.link import LinkRepository
from app.repositories.corporate_action import CorporateActionRepository
from app.services.corporate_action_service import CorporateActionService, action_multiplier
from app.services.best_profit_matcher import BestProfitMatcherService

FAKE = "ZZTESTCO"  # not a real CSX ticker


@pytest.fixture
def uid():
    u = f"pytest_{uuid.uuid4().hex[:12]}"
    UserRepository().upsert_user(u, "CA User")
    yield u
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (u,))  # cascades trades/allocs/alerts


@pytest.fixture
def action():
    """A pending 1:1 bonus, ex-date today, on the fake ticker."""
    repo = CorporateActionRepository()
    a = repo.create("CSX", FAKE, "bonus", 1, 1, date.today(), "test bonus", "pytest_admin")
    yield a
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM corporate_actions WHERE action_id = %s", (a["actionId"],))


def _buy(uid, seq, price, qty, days_ago=10):
    TradeRepository().add_trade({
        "tradeId": str(uuid.uuid4()), "userId": uid, "seq": seq, "ticker": FAKE,
        "side": "BUY", "price": price, "qty": qty, "commission": 0,
        "orderDate": datetime.utcnow() - timedelta(days=days_ago),
        "market": "CSX", "currency": "KHR",
    })


def _svc():
    """No send_message => no Telegram in tests."""
    return CorporateActionService(CorporateActionRepository(), UserRepository(),
                                  LinkRepository(), AlertRepository(), send_message=None)


def _open_lots(uid):
    """(price, open_qty) for the fake ticker, from the same view the matcher uses."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT t.price, t.qty - COALESCE(a.alloc, 0)
                FROM trades t
                LEFT JOIN (SELECT buy_trade_id, SUM(qty_allocated) alloc FROM allocations GROUP BY buy_trade_id) a
                  ON a.buy_trade_id = t.trade_id
                WHERE t.user_id = %s AND t.ticker = %s AND t.side = 'BUY'
                ORDER BY t.seq
                """, (uid, FAKE))
            return [(Decimal(r[0]), int(r[1])) for r in cur.fetchall()]


def _invested(uid):
    return sum(p * q for p, q in _open_lots(uid))


# ---- multiplier ----

def test_multiplier_math():
    assert action_multiplier({"actionType": "bonus", "ratioNew": 1, "ratioHeld": 1}) == 2
    assert action_multiplier({"actionType": "split", "ratioNew": 3, "ratioHeld": 1}) == 3
    assert action_multiplier({"actionType": "bonus", "ratioNew": 1, "ratioHeld": 4}) == Decimal("1.25")


# ---- the invariant ----

def test_invested_unchanged_and_shares_doubled(uid, action):
    _buy(uid, 1, 2200, 100)
    before = _invested(uid)
    _svc().apply_action(action)

    lots = _open_lots(uid)
    total_qty = sum(q for _, q in lots)
    assert total_qty == 200                      # doubled
    assert _invested(uid) == before              # value identical to the riel
    assert all(p > 0 for p, _ in lots)           # NO zero-cost lot
    assert any(p == Decimal("1100.0000") for p, _ in lots)  # 2200 / 2


def test_multiple_lots_each_split(uid, action):
    _buy(uid, 1, 2000, 50)
    _buy(uid, 2, 3000, 30)
    before = _invested(uid)
    _svc().apply_action(action)
    assert sum(q for _, q in _open_lots(uid)) == 160  # (50+30) * 2
    assert _invested(uid) == before


def test_partially_sold_lot_only_open_portion_splits(uid, action):
    # Buy 100 @ 2000, sell 40 first => 60 open. After 1:1: 120 open, sold 40 untouched.
    _buy(uid, 1, 2000, 100)
    trades = TradeRepository().list_trades_by_side(uid, FAKE, "BUY", market="CSX")
    sell = {"tradeId": str(uuid.uuid4()), "userId": uid, "seq": 2, "ticker": FAKE,
            "side": "SELL", "price": 2500, "qty": 40, "commission": 0,
            "orderDate": datetime.utcnow() - timedelta(days=5), "market": "CSX", "currency": "KHR"}
    TradeRepository().add_trade(sell)
    BestProfitMatcherService(TradeRepository(), AllocationRepository()).match_sell(sell)

    assert sum(q for _, q in _open_lots(uid)) == 60
    _svc().apply_action(action)
    assert sum(q for _, q in _open_lots(uid)) == 120  # 60 open -> 120

    # The already-booked allocation keeps the original buy price (2000), not 1000.
    allocs = AllocationRepository().list_allocations(uid, ticker=FAKE)
    assert allocs and all(Decimal(a["buyPrice"]) == 2000 for a in allocs)


def test_realised_pnl_after_bonus_uses_adjusted_basis(uid, action):
    # 100 @ 2000, bonus 1:1 -> 200 @ 1000. Sell 200 @ 1100 => profit 200*(1100-1000)=20000.
    _buy(uid, 1, 2000, 100)
    _svc().apply_action(action)
    sell = {"tradeId": str(uuid.uuid4()), "userId": uid, "seq": 99, "ticker": FAKE,
            "side": "SELL", "price": 1100, "qty": 200, "commission": 0,
            "orderDate": datetime.utcnow(), "market": "CSX", "currency": "KHR"}
    TradeRepository().add_trade(sell)
    allocs = BestProfitMatcherService(TradeRepository(), AllocationRepository()).match_sell(sell)
    total = sum(Decimal(a["realisedPnl"]) for a in allocs)
    assert total == Decimal("20000")  # honest profit, not a fake loss


def test_non_holder_untouched(uid, action):
    other = f"pytest_{uuid.uuid4().hex[:12]}"
    UserRepository().upsert_user(other, "Other")
    try:
        _buy(uid, 1, 2000, 10)
        _svc().apply_action(action)
        # `other` never held the ticker => no rows created for them.
        assert _open_lots(other) == []
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE user_id = %s", (other,))


# ---- idempotency ----

def test_apply_twice_is_noop(uid, action):
    _buy(uid, 1, 2000, 100)
    svc = _svc()
    svc.apply_action(action)
    after_first = sorted(_open_lots(uid))
    # Re-fetch the (now applied) action and run again — must not double-apply.
    reloaded = CorporateActionRepository().get(action["actionId"])
    svc.apply_action(reloaded)
    assert sorted(_open_lots(uid)) == after_first


def test_check_once_skips_future_and_applies_due(uid):
    repo = CorporateActionRepository()
    future = repo.create("CSX", FAKE, "bonus", 1, 1, date.today() + timedelta(days=5), None, "pytest_admin")
    due = repo.create("CSX", FAKE, "bonus", 1, 1, date.today(), None, "pytest_admin")
    try:
        _buy(uid, 1, 2000, 10)
        _svc().check_once()
        assert repo.get(due["actionId"])["appliedAt"] is not None
        assert repo.get(future["actionId"])["appliedAt"] is None  # not yet due
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM corporate_actions WHERE action_id = ANY(%s)",
                            ([future["actionId"], due["actionId"]],))


# ---- alerts ----

def test_alert_target_rescaled(uid, action):
    _buy(uid, 1, 2000, 100)
    created = AlertRepository().create(uid, "CSX", FAKE, "KHR", Decimal("2000"), "below")
    _svc().apply_action(action)
    rows = AlertRepository().list_for_user(uid)
    got = next(r for r in rows if r["alertId"] == created["alertId"])
    assert Decimal(got["targetPrice"]) == Decimal("1000.0000")  # 2000 / 2


# ---- floor rounding (documented) ----

def test_fractional_bonus_floors_per_lot(uid):
    repo = CorporateActionRepository()
    a = repo.create("CSX", FAKE, "bonus", 1, 10, date.today(), None, "pytest_admin")  # 1:10 -> +10%
    try:
        _buy(uid, 1, 1000, 55)   # +10% of 55 = 5.5 -> floor 5
        _svc().apply_action(a)
        assert sum(q for _, q in _open_lots(uid)) == 60  # 55 + 5
    finally:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM corporate_actions WHERE action_id = %s", (a["actionId"],))

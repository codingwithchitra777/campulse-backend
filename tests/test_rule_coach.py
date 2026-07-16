"""The free rule-based coach. No key, no credit, no network — so these tests
assert the text itself: it must say the useful thing, render money per currency,
and never stray into advice.
"""
from decimal import Decimal

import pytest

from app.services.ai_coach import build_snapshot, MIN_CLOSED_TRADES
from app.services.rule_coach import build_insight, MIN_TAG_TRADES, TAG_GAP_PCT

from tests.test_ai_coach import _stats  # same shaped fixture as the AI coach


def insight(**over):
    return build_insight(build_snapshot(_stats(**over)))


def test_headline_reports_the_real_counts():
    out = insight()
    assert "closed 8 trades" in out
    assert "5 winners and 3 losers" in out
    assert "62.5% win rate" in out


def test_tag_gap_is_surfaced():
    """The single most valuable line — and it needs no model to produce."""
    out = insight()
    assert "#plan wins 71.4%" in out
    assert "#fomo wins just 20.0%" in out


def test_tag_gap_suppressed_when_too_few_trades():
    tags = [{"tag": "plan", "trades": MIN_TAG_TRADES - 1, "wins": 2, "losses": 0, "winRate": 100.0},
            {"tag": "fomo", "trades": MIN_TAG_TRADES - 1, "wins": 0, "losses": 2, "winRate": 0.0}]
    assert "journal tag" not in insight(byTag=tags)


def test_tag_gap_suppressed_when_gap_is_noise():
    tags = [{"tag": "plan", "trades": 5, "wins": 3, "losses": 2, "winRate": 60.0},
            {"tag": "swing", "trades": 5, "wins": 3, "losses": 2, "winRate": 55.0}]
    assert 60.0 - 55.0 < TAG_GAP_PCT
    assert "journal tag" not in insight(byTag=tags)


def test_money_is_rendered_per_currency_never_blended():
    out = insight()
    assert "2,050" not in out           # sanity: not leaking another test's numbers
    assert "120,000 ៛" in out           # KHR: whole, trailing symbol
    assert "$40.50" in out              # USD: leading $, cents
    assert "120000.0" not in out        # never a raw float


def test_khr_and_usd_are_reported_separately():
    two = [{"currency": "KHR", "realisedPnl": Decimal("90000"), "unrealisedPnl": Decimal("5000"),
            "invested": Decimal("400000"), "value": Decimal("405000"), "wins": 4, "losses": 1},
           {"currency": "USD", "realisedPnl": Decimal("-120.25"), "unrealisedPnl": Decimal("0"),
            "invested": Decimal("2000"), "value": Decimal("1880"), "wins": 1, "losses": 2}]
    out = insight(byCurrency=two)
    assert "In KHR you've made 90,000 ៛" in out
    assert "In USD you've lost $120.25" in out


def test_thin_data_says_so_instead_of_inventing_a_pattern():
    out = insight(closed=MIN_CLOSED_TRADES - 1)
    assert "closed trade" in out
    # It may name win rate as something it *could* compare later, but it must not
    # claim any actual figure off 4 trades.
    assert "62.5%" not in out and "#plan" not in out


def test_never_gives_advice():
    """CamPulse is real money and we're not advisers — the free coach must be as
    strictly descriptive as the AI one."""
    out = insight().lower()
    for word in ("you should", "buy ", "sell ", "recommend", "target", "will rise",
                 "will fall", "consider buying", "undervalued", "overvalued"):
        assert word not in out, f"rule coach gave advice: {word!r}"

"""Telegram admin gold-board photo -> OCR -> save (same path as the manual form).

The OCR-parse test runs real RapidOCR on a synthetic board. The handler tests
mock the client + OCR + the DB write, so they never run slow OCR or touch the
real XAU-KH gold row. Disposable ids, cleaned up by exact id.
"""
import uuid

import numpy as np
import pytest

from app.repositories.user import UserRepository
from app.repositories.price_history import PriceHistoryRepository
from app.services.telegram_bot import TelegramBotService
from app.db.database import get_db


class FakeClient:
    def __init__(self, image: bytes = b"img"):
        self.messages = []
        self.image = image

    def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append(text)

    def download_file(self, file_id):
        return self.image


def _make_synthetic_board() -> bytes:
    import cv2
    h, w = 1250, 1000
    img = np.full((h, w, 3), (55, 48, 42), np.uint8)
    yel = (60, 200, 240)

    def put(t, x, y, s=1.6):
        cv2.putText(img, t, (x, y), cv2.FONT_HERSHEY_SIMPLEX, s, yel, 3, cv2.LINE_AA)

    put("085 999 279", 60, 200, 1.1)     # decoys that must be filtered
    put("10/08/2026", 90, 480, 1.2)
    for i, (sell, buy) in enumerate([("519", "514"), ("519", "509"),
                                     ("320", "270"), ("295", "245")]):
        y = 580 + i * 105
        put(sell, 480, y, 2.0)
        put(buy, 760, y, 2.0)
    put("428TE & 428UE  245", 120, 1090, 1.0)
    return cv2.imencode(".png", img)[1].tobytes()


def _mk_user(role: str) -> str:
    uid = f"pytest_{uuid.uuid4().hex[:12]}"
    UserRepository().upsert_user(uid, "Test", role=role)
    return uid


def _cleanup(uid: str):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


def test_gold_board_ocr_parses_synthetic():
    from app.services.gold_ocr import parse_gold_board
    rows = parse_gold_board(_make_synthetic_board())
    rows = [r for r in rows if r.get("sell_out") and r.get("buy_in")]
    assert len(rows) == 4
    assert (rows[0]["sell_out"], rows[0]["buy_in"]) == (519, 514)
    assert (rows[3]["sell_out"], rows[3]["buy_in"]) == (295, 245)


def test_gold_photo_rejects_non_admin(monkeypatch):
    uid = _mk_user("user")
    called = {"upsert": False}
    monkeypatch.setattr(PriceHistoryRepository, "upsert_snapshot",
                        lambda *a, **k: called.__setitem__("upsert", True))
    client = FakeClient()
    try:
        TelegramBotService(client=client).handle_gold_image(uid, 111, "Test", "fid", "")
        assert called["upsert"] is False
        assert any("admin" in m.lower() for m in client.messages)
    finally:
        _cleanup(uid)


def test_gold_photo_admin_saves(monkeypatch):
    uid = _mk_user("admin")
    monkeypatch.setattr(
        "app.services.gold_ocr.parse_gold_board",
        lambda b, **k: [
            {"type_km": "x", "type_en": "Gold bar (dom)", "sell_out": 519, "buy_in": 514},
            {"type_km": "y", "type_en": "Gold jewelry (kreung)", "sell_out": 519, "buy_in": 509},
        ],
    )
    captured = {}

    def fake_upsert(self, ticker, snapshot_date, price, market="CSX", bid_price=None, ask_price=None):
        captured.update(ticker=ticker, market=market, bid=bid_price, ask=ask_price)

    monkeypatch.setattr(PriceHistoryRepository, "upsert_snapshot", fake_upsert)
    client = FakeClient()
    try:
        TelegramBotService(client=client).handle_gold_image(uid, 111, "Admin", "fid", "")
        # bid = higher លក់ចេញ (sell-out); ask = ទិញចូល (buy-in); default row 1 (bar).
        assert captured == {"ticker": "XAU-KH", "market": "GOLD_KH", "bid": 519, "ask": 514}
        assert any("saved" in m.lower() for m in client.messages)
    finally:
        _cleanup(uid)


def test_gold_command_shows_latest(monkeypatch):
    monkeypatch.setattr(
        PriceHistoryRepository, "get_history",
        lambda self, tickers: [
            {"date": "2026-01-01", "price": 400, "bidPrice": 405, "askPrice": 395},
            {"date": "2026-08-10", "price": 516.5, "bidPrice": 519, "askPrice": 514},
        ],
    )
    client = FakeClient()
    TelegramBotService(client=client)._gold(111)
    msg = "\n".join(client.messages)
    assert "2026-08-10" in msg           # newest, not the older row
    assert "$519.00" in msg and "$514.00" in msg and "$516.50" in msg


def test_gold_command_empty(monkeypatch):
    monkeypatch.setattr(PriceHistoryRepository, "get_history", lambda self, tickers: [])
    client = FakeClient()
    TelegramBotService(client=client)._gold(111)
    assert any("no gold" in m.lower() for m in client.messages)


def test_gold_photo_caption_picks_row(monkeypatch):
    uid = _mk_user("admin")
    monkeypatch.setattr(
        "app.services.gold_ocr.parse_gold_board",
        lambda b, **k: [
            {"type_km": "x", "type_en": "Gold bar (dom)", "sell_out": 519, "buy_in": 514},
            {"type_km": "y", "type_en": "Gold jewelry (kreung)", "sell_out": 519, "buy_in": 509},
        ],
    )
    captured = {}
    monkeypatch.setattr(
        PriceHistoryRepository, "upsert_snapshot",
        lambda self, t, d, p, market="CSX", bid_price=None, ask_price=None:
            captured.update(bid=bid_price, ask=ask_price))
    client = FakeClient()
    try:
        TelegramBotService(client=client).handle_gold_image(uid, 111, "Admin", "fid", "2")
        assert captured == {"bid": 519, "ask": 509}  # row 2 (jewelry)
    finally:
        _cleanup(uid)

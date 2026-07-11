import io
import uuid

import pytest
from fastapi.testclient import TestClient

import app.core.config as config_module
from app.main import app
from app.services.telegram_bot import CommandParser, TelegramBotService
from app.db.database import get_db

client = TestClient(app)

SECRET = "test-webhook-secret"


class CaptureClient:
    """Stand-in for telegram_client — records outbound calls instead of hitting Telegram."""
    def __init__(self):
        self.messages = []
        self.photos = []

    def send_message(self, chat_id, text, parse_mode=None):
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id, image, caption=None):
        # Drain the buffer like the real client would.
        if hasattr(image, "read"):
            image.read()
        self.photos.append((chat_id, caption))


@pytest.fixture(autouse=True)
def _set_secret():
    # settings is a frozen dataclass singleton (shared by reference with the
    # webhook endpoint), so bypass the freeze to set + restore the secret.
    original = config_module.settings.telegram_webhook_secret
    object.__setattr__(config_module.settings, "telegram_webhook_secret", SECRET)
    yield
    object.__setattr__(config_module.settings, "telegram_webhook_secret", original)


@pytest.fixture
def tg_user():
    tg_id = int(uuid.uuid4().int % 10**9) + 8 * 10**14
    yield str(tg_id), tg_id
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s", (str(tg_id),))


# ---- CommandParser ----

@pytest.mark.parametrize("text,expected", [
    ("/price$ABC", ("price", "$ABC")),
    ("/price ABC", ("price", "ABC")),
    ("/buy$ABC 7300 100", ("buy", "$ABC 7300 100")),
    ("/top_orders", ("top_orders", "")),
    ("/start@CamboPulseBot", ("start", "")),
    ("hello", (None, "")),
])
def test_command_parser_split(text, expected):
    assert CommandParser.split(text) == expected


@pytest.mark.parametrize("remainder,expected", [
    ("$ABC 7300 100", ("ABC", 7300, 100)),
    ("abc 7300 100", ("ABC", 7300, 100)),
    ("ABC 7300", None),
    ("ABC x y", None),
])
def test_command_parser_trade_args(remainder, expected):
    assert CommandParser.parse_trade_args(remainder) == expected


# ---- Webhook auth ----

def _update(tg_id, text):
    return {
        "message": {
            "text": text,
            "from": {"id": tg_id, "first_name": "Pytest", "last_name": "Bot"},
            "chat": {"id": tg_id},
        }
    }


def test_webhook_rejects_missing_secret(tg_user):
    _, tg_id = tg_user
    resp = client.post("/api/telegram/webhook", json=_update(tg_id, "/portfolio"))
    assert resp.status_code == 403


def test_webhook_rejects_wrong_secret(tg_user):
    _, tg_id = tg_user
    resp = client.post(
        "/api/telegram/webhook",
        json=_update(tg_id, "/portfolio"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "nope"},
    )
    assert resp.status_code == 403


def test_webhook_accepts_valid_secret_and_non_text(tg_user):
    resp = client.post(
        "/api/telegram/webhook",
        json={"channel_post": {"foo": "bar"}},
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ---- Dispatch via CaptureClient ----

def test_price_replies_with_photo(tg_user):
    user_id, tg_id = tg_user
    cap = CaptureClient()
    svc = TelegramBotService(client=cap)
    svc.dispatch(user_id, tg_id, "Pytest", "/price$ABC")
    assert len(cap.photos) == 1
    assert cap.photos[0][0] == tg_id


def test_buy_then_sell_persists_and_reports_pnl(tg_user):
    user_id, tg_id = tg_user
    cap = CaptureClient()
    svc = TelegramBotService(client=cap)

    svc.dispatch(user_id, tg_id, "Pytest", "/buy$ABC 100 10")
    svc.dispatch(user_id, tg_id, "Pytest", "/sell$ABC 150 10")

    # Both replied with a trade card.
    assert len(cap.photos) == 2
    sell_caption = cap.photos[-1][1]
    assert "SELL confirmed" in sell_caption
    assert "P/L:" in sell_caption

    # Trades actually persisted for this telegram user id.
    trades = svc.trade_repo.list_trades(user_id)
    assert {t["side"] for t in trades} == {"BUY", "SELL"}


def test_start_captures_chat_id(tg_user):
    user_id, tg_id = tg_user
    cap = CaptureClient()
    svc = TelegramBotService(client=cap)
    svc.dispatch(user_id, tg_id, "Pytest Bot", "/start")
    assert len(cap.messages) == 1
    user = svc.user_repo.get_user(user_id)
    assert user is not None and user["chat_id"] == tg_id


def test_portfolio_empty_user_gets_text(tg_user):
    user_id, tg_id = tg_user
    cap = CaptureClient()
    TelegramBotService(client=cap).dispatch(user_id, tg_id, "Pytest", "/portfolio")
    assert cap.messages and "No trades yet" in cap.messages[0][1]
    assert not cap.photos


def test_unknown_command_gets_help(tg_user):
    user_id, tg_id = tg_user
    cap = CaptureClient()
    TelegramBotService(client=cap).dispatch(user_id, tg_id, "Pytest", "/wat")
    assert cap.messages and "CamPulse" in cap.messages[0][1]

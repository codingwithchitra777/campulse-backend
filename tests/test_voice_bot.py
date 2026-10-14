"""Telegram voice message -> Khmer Whisper transcription -> text reply.

The handler tests mock the client + the transcriber so they never load the
model. The decode test runs the real audio path on a synthetic OGG/Opus clip
(Telegram's voice format) when the `stt` extra is installed.
"""
import io

import pytest

from app.services import transcriber
from app.services.telegram_bot import TelegramBotService


class FakeClient:
    def __init__(self, audio: bytes = b"ogg"):
        self.messages = []
        self.audio = audio

    def send_message(self, chat_id, text, parse_mode=None, reply_to=None):
        self.messages.append((text, reply_to))

    def download_file(self, file_id):
        return self.audio


def test_voice_replies_with_transcript(monkeypatch):
    monkeypatch.setattr(transcriber, "transcribe", lambda data: "សួស្តី")
    client = FakeClient()
    TelegramBotService(client=client).handle_voice(1, "fid", message_id=42)
    assert client.messages == [("សួស្តី", 42)]


def test_voice_empty_transcript(monkeypatch):
    monkeypatch.setattr(transcriber, "transcribe", lambda data: "")
    client = FakeClient()
    TelegramBotService(client=client).handle_voice(1, "fid", message_id=42)
    assert "No speech" in client.messages[0][0]


def test_voice_download_failure():
    client = FakeClient(audio=None)
    TelegramBotService(client=client).handle_voice(1, "fid")
    assert "download" in client.messages[0][0]


def test_voice_stt_not_installed(monkeypatch):
    def missing(data):
        raise ImportError("torch")
    monkeypatch.setattr(transcriber, "transcribe", missing)
    client = FakeClient()
    TelegramBotService(client=client).handle_voice(1, "fid")
    assert "isn't available" in client.messages[0][0]


def test_decode_ogg_opus_to_16k_mono():
    np = pytest.importorskip("numpy")
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("scipy")
    sr = 48000
    t = np.arange(sr * 3) / sr
    x = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    buf = io.BytesIO()
    sf.write(buf, np.stack([x, x], axis=1), sr, format="OGG", subtype="OPUS")
    audio = transcriber._decode(buf.getvalue())
    assert audio.ndim == 1 and audio.dtype == np.float32
    assert abs(len(audio) / transcriber.SAMPLE_RATE - 3) < 0.1

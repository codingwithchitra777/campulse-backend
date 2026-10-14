"""Khmer speech-to-text for Telegram voice messages, using a fine-tuned Whisper
model (chitra168/whisper-small-khmer-mini).

Heavy deps (torch, transformers, soundfile, scipy) are imported lazily so the
backend still boots (and every other feature works) if they're not installed —
the Telegram voice handler just reports the feature is unavailable. Install
them with the `stt` extra. The model is downloaded from Hugging Face and
loaded once, on the first voice message.
"""
import io
import logging
import threading
from math import gcd

logger = logging.getLogger(__name__)

MODEL_ID = "chitra168/whisper-small-khmer-mini"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 10  # the model was trained on clips of at most ~10s

_processor = None
_model = None
_lock = threading.Lock()


def _load():
    global _processor, _model
    with _lock:
        if _model is None:
            from transformers import WhisperForConditionalGeneration, WhisperProcessor  # lazy: heavy import
            logger.info(f"Loading Whisper model {MODEL_ID}")
            _processor = WhisperProcessor.from_pretrained(MODEL_ID)
            model = WhisperForConditionalGeneration.from_pretrained(MODEL_ID).eval()
            # The repo stores eos_token_id as a list, which breaks the
            # timestamp logits processor (it slices with it).
            eos = model.generation_config.eos_token_id
            if isinstance(eos, list):
                model.generation_config.eos_token_id = eos[0]
            _model = model
    return _processor, _model


def _decode(data: bytes):
    """OGG/Opus (Telegram voice), MP3, WAV, ... -> 16 kHz mono float32."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    audio, sr = sf.read(io.BytesIO(data), dtype="float32", always_2d=True)
    audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE:
        g = gcd(sr, SAMPLE_RATE)
        audio = resample_poly(audio, SAMPLE_RATE // g, sr // g).astype(np.float32)
    return audio


def transcribe(data: bytes) -> str:
    """Blocking (seconds on CPU) — call off the request thread. Raises
    ImportError if the `stt` extra isn't installed."""
    import torch

    processor, model = _load()
    audio = _decode(data)
    step = SAMPLE_RATE * CHUNK_SECONDS
    chunks = [audio[i:i + step] for i in range(0, len(audio), step)]
    # A tiny trailing fragment (<0.3s) only produces hallucinations.
    if len(chunks) > 1 and len(chunks[-1]) < SAMPLE_RATE * 0.3:
        chunks.pop()

    texts = []
    for chunk in chunks:
        features = processor(chunk, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features
        with torch.inference_mode():
            ids = model.generate(features, language="khmer", task="transcribe",
                                 return_timestamps=True, max_new_tokens=440)
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        if text:
            texts.append(text)
    return " ".join(texts)

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
import time
from math import gcd

logger = logging.getLogger(__name__)

MODEL_ID = "chitra168/whisper-small-khmer-mini"
SAMPLE_RATE = 16000
CHUNK_SECONDS = 10  # the model was trained on clips of at most ~10s

_processor = None
_model = None
_lock = threading.Lock()
# One transcription at a time: concurrent generate() calls would multiply the
# activation memory on a box that only just fits the model.
_run_lock = threading.Lock()


def _load():
    global _processor, _model
    with _lock:
        if _model is None:
            import torch
            from transformers import WhisperForConditionalGeneration, WhisperProcessor  # lazy: heavy import
            logger.info(f"Loading Whisper model {MODEL_ID}")
            _processor = WhisperProcessor.from_pretrained(MODEL_ID)
            # bfloat16 halves the weights (~1.5 GB -> ~0.75 GB peak) so it fits
            # the cloud instance; fp32 got the container OOM-killed.
            model = WhisperForConditionalGeneration.from_pretrained(
                MODEL_ID, dtype=torch.bfloat16, low_cpu_mem_usage=True).eval()
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
    ImportError if the speech deps aren't installed."""
    processor, model = _load()
    audio = _decode(data)
    step = SAMPLE_RATE * CHUNK_SECONDS
    chunks = [audio[i:i + step] for i in range(0, len(audio), step)]
    # A tiny trailing fragment (<0.3s) only produces hallucinations.
    if len(chunks) > 1 and len(chunks[-1]) < SAMPLE_RATE * 0.3:
        chunks.pop()

    started = time.monotonic()
    with _run_lock:
        texts = _generate(processor, model, chunks)
    logger.info(f"Transcribed {len(audio) / SAMPLE_RATE:.1f}s of audio in {time.monotonic() - started:.1f}s")
    return " ".join(texts)


def _generate(processor, model, chunks):
    import torch

    texts = []
    for chunk in chunks:
        features = processor(chunk, sampling_rate=SAMPLE_RATE, return_tensors="pt").input_features.to(model.dtype)
        with torch.inference_mode():
            ids = model.generate(features, language="khmer", task="transcribe",
                                 return_timestamps=True, max_new_tokens=440)
        text = processor.batch_decode(ids, skip_special_tokens=True)[0].strip()
        if text:
            texts.append(text)
    return texts

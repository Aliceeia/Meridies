"""The system voice: Gemini TTS, generated once and cached on disk. SYSTEM SPEECH ONLY.

Memories are never synthesised: they are the person's own recordings. This module speaks descriptions, "where am I",
arrivals and status lines.

Gemini TTS returns raw PCM: 24 kHz, mono, 16-bit little-endian (audio/L16;rate=24000). We never hand raw PCM to a
player that guesses the rate: every clip is written as a WAV whose header says 24000 Hz, so the browser resamples it
correctly (decoding 24 kHz PCM as 16 or 44.1 kHz is what makes speech slow, thick and metallic).

Delivery is steered per line with a style instruction (the TTS model takes it as part of the prompt):
  calm   descriptions, where am I, arrivals: relaxed, natural, real pauses
  alert  hazards and warnings: clear and quick, a little urgent, never alarmed
  plain  short status lines

Voice: settings.json "voice" (A/B from the gallery's profile menu), default Sulafat. Clips are cached by
voice + style + text under connect/data/cache/voice/; runtime only plays files.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import gemini

VOICES = ["Sulafat", "Aoede", "Zephyr", "Puck"]      # warm, light, bright, lively
DEFAULT_VOICE = "Sulafat"
# free tier: 10 requests per day PER MODEL, so every TTS model the key has is in the chain
MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts", "gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts")
CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "voice"
RATE = 24000

PERSONA = ("Speak the way a friend who knows this place would: relaxed, natural, with real pauses and emphasis. "
           "No announcer voice, no sing-song. Never sound like you are reading a list. "
           "Say exactly the words in the transcript: add nothing before or after, no 'okay', no 'so'.")
# TTS models speak whatever is in the prompt, so the persona goes in systemInstruction and each line gets only a
# short directive the model recognises as delivery, not script ("Say calmly: ...").
STYLE = {
    "calm": "soft, warm and unhurried, like describing a room you love to a friend",
    "alert": "brisk and firm, a quick heads-up with weight on the first words; clear, never alarmed",
    "plain": "simple and brief, matter-of-fact",
}
last_error: str | None = None


PROMPT_VERSION = 7     # bump when PERSONA/STYLE change: old clips are never reused


def key(text: str, style: str, voice: str) -> str:
    return hashlib.sha1(f"v{PROMPT_VERSION}|{voice}|{style}|{text.strip()}".encode()).hexdigest()[:20]


def path_for(text: str, style: str, voice: str) -> Path:
    return CACHE / voice / f"{key(text, style, voice)}.wav"


def wav(pcm: bytes, rate: int = RATE) -> bytes:
    """16-bit mono PCM -> WAV with the true sample rate in its header."""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE" + b"fmt " +
            struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm)) + pcm)


SHORT = {"calm": "Say warmly and calmly", "alert": "Say clearly, with gentle urgency", "plain": "Say simply"}


def synth(text: str, style: str = "calm", voice: str = DEFAULT_VOICE, timeout: int = 40) -> bytes:
    """Gemini TTS -> WAV bytes, checked: if the model read the notes aloud (clip far too long), try the plainest
    prompt form instead. Raises if no plausible clip comes back."""
    notes = f"### DIRECTOR'S NOTES\nVoice: {PERSONA}\nDelivery for this line: {STYLE.get(style, STYLE['calm'])}\n\n### TRANSCRIPT\n{text}"
    # TTS models take no system instruction: delivery notes in a director's-notes header; only TRANSCRIPT is spoken.
    # Two tries with notes (they carry the warning/description difference), then the line alone (neutral delivery).
    for prompt in (notes, notes, text):
        out = _synth_once(prompt, voice, timeout)
        if plausible(text, out):
            return out
        print(f"  voice: rejected a {seconds_of(out):.1f}s clip for {len(text.split())} words (notes read aloud?)", flush=True)
    raise ValueError("no plausible clip")


def _synth_once(prompt: str, voice: str, timeout: int) -> bytes:
    global last_error
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    err = None
    for model in MODELS:
        req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                     method="POST", data=json.dumps(body).encode(),
                                     headers={"x-goog-api-key": gemini.api_key(), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            part = next(p for p in out["candidates"][0]["content"]["parts"] if "inlineData" in p)["inlineData"]
            mime = part.get("mimeType", "")
            rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else RATE   # trust the header Gemini sends
            last_error = None
            return wav(base64.b64decode(part["data"]), rate)
        except urllib.error.HTTPError as e:
            err = e
            last_error = f"{model}: HTTP {e.code}"
            if e.code in (429, 404, 503):
                continue
            raise
        except (urllib.error.URLError, TimeoutError, KeyError, StopIteration) as e:
            err = e
            last_error = f"{model}: {type(e).__name__}"
            continue
    raise err


def seconds_of(wav_bytes: bytes) -> float:
    return (len(wav_bytes) - 44) / (2 * struct.unpack("<I", wav_bytes[24:28])[0])


def plausible(text: str, wav_bytes: bytes) -> bool:
    """TTS models sometimes read the delivery notes aloud too: a clip far longer than its words is rejected."""
    expected = len(text.split()) / 2.6
    return seconds_of(wav_bytes) <= max(3.5, expected * 1.7 + 1.2)


def cached(text: str, style: str, voice: str) -> Path | None:
    p = path_for(text, style, voice)
    return p if p.exists() else None


def ensure(text: str, style: str = "calm", voice: str = DEFAULT_VOICE) -> Path:
    """The clip on disk, generating it if missing."""
    p = path_for(text, style, voice)
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(synth(text, style, voice))
        tmp.replace(p)
    return p


# ---------- make missing clips in the background, one at a time (quota-friendly), never on the request path ----------
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice")
_pending: set[str] = set()
_lock = threading.Lock()
failed: dict[str, float] = {}          # key -> when it last failed (don't hammer a quota)


def queue(text: str, style: str, voice: str) -> bool:
    k = f"{voice}/{key(text, style, voice)}"
    with _lock:
        if k in _pending or path_for(text, style, voice).exists() or time.time() - failed.get(k, 0) < 600:
            return False
        _pending.add(k)

    def run():
        try:
            ensure(text, style, voice)
            print(f"  voice: made [{voice}/{style}] {text[:60]}", flush=True)
        except Exception as e:  # noqa: BLE001 - quota, network, rejected clips: the browser voice covers it
            failed[k] = time.time()
            print(f"  voice: couldn't make [{voice}/{style}] {text[:40]}: {type(e).__name__} {last_error or ''}", flush=True)
        finally:
            with _lock:
                _pending.discard(k)
    _worker.submit(run)
    return True


def pending() -> int:
    with _lock:
        return len(_pending)


def style_for(text: str, kind: str) -> str:
    if kind == "warning":
        return "alert"
    low = text.lower()
    hazard_words = ("mind the", "careful", "low ", "stop", "watch")
    if kind in ("arrival", "detail", "more") and any(low.startswith(w) or f". {w}" in low for w in hazard_words):
        return "alert"
    return "plain" if kind in ("short", "help") else "calm"


if __name__ == "__main__":
    import sys
    t0 = time.monotonic()
    v = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VOICE
    for style, line in [("alert", "Mind the chandelier: one metre six, two steps ahead."),
                        ("calm", "A dining table, about two steps ahead. The living room is behind you.")]:
        p = ensure(line, style, v)
        print(style, p, p.stat().st_size, f"{time.monotonic() - t0:.1f}s")

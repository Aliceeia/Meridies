"""System voice (Gemini TTS, cached).   pytest connect/tests/test_voice.py

- Gemini's raw PCM (24 kHz, mono, 16-bit) is written as WAV with the TRUE rate in the header (no guessing)
- a clip far longer than its words (the model read the delivery notes aloud) is rejected and retried
- /api/voice serves only cached clips: a miss answers 404 at once (the page uses the browser voice) and is queued
- the voice is configurable (A/B) and must be one of the known voices
"""
import base64
import json
import struct
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import connect.server as srv  # noqa: E402
from connect.backend import voice  # noqa: E402


def fake_response(seconds: float, rate=24000):
    pcm = b"\x00\x01" * int(seconds * rate)
    return json.dumps({"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": f"audio/L16;codec=pcm;rate={rate}", "data": base64.b64encode(pcm).decode()}}]}}]}).encode()


class FakeHTTP:
    def __init__(self, durations):
        self.durations, self.prompts = list(durations), []

    def __call__(self, req, timeout=None):
        self.prompts.append(json.loads(req.data)["contents"][0]["parts"][0]["text"])
        body = fake_response(self.durations.pop(0))

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return body
        return R()


def test_wav_header_carries_the_true_rate():
    w = voice.wav(b"\x00\x00" * 24000, 24000)
    assert w[:4] == b"RIFF" and w[8:12] == b"WAVE"
    channels, rate, byte_rate, align, bits = struct.unpack("<HIIHH", w[22:36])
    assert (channels, rate, byte_rate, align, bits) == (1, 24000, 48000, 2, 16)
    assert abs(voice.seconds_of(w) - 1.0) < 1e-6


def test_rate_comes_from_geminis_mime_type(monkeypatch):
    monkeypatch.setattr(voice.gemini, "api_key", lambda: "k")
    monkeypatch.setattr(urllib.request, "urlopen", FakeHTTP([2.0]))
    w = voice.synth("Mind the chandelier, two steps ahead.", "alert", "Sulafat")
    assert struct.unpack("<I", w[24:28])[0] == 24000


def test_notes_read_aloud_are_rejected_then_plain_line(monkeypatch):
    monkeypatch.setattr(voice.gemini, "api_key", lambda: "k")
    fake = FakeHTTP([25.0, 22.0, 2.5])          # notes read aloud twice, then the bare line
    monkeypatch.setattr(urllib.request, "urlopen", fake)
    w = voice.synth("Kitchen. Two memories here.", "calm", "Sulafat")
    assert abs(voice.seconds_of(w) - 2.5) < 0.01
    assert "DIRECTOR'S NOTES" in fake.prompts[0] and fake.prompts[2] == "Kitchen. Two memories here."
    assert voice.plausible("Kitchen.", voice.wav(b"\0\0" * 24000 * 3)) and not voice.plausible("Kitchen.", voice.wav(b"\0\0" * 24000 * 12))


def test_cache_key_changes_with_voice_style_and_prompt_version():
    a = voice.key("Hallway.", "calm", "Sulafat")
    assert a != voice.key("Hallway.", "alert", "Sulafat") != voice.key("Hallway.", "calm", "Aoede")


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "CACHE", tmp_path / "voice")
    for k, v in dict(MEM=tmp_path, SETTINGS=tmp_path / "s.json", AUTO_PROCESS=False, VOICE_ON=True, VOICE_AUTOGEN=True).items():
        monkeypatch.setattr(srv, k, v)
    queued = []
    monkeypatch.setattr(voice, "queue", lambda t, s, v: queued.append((t, s, v)) or True)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path):
        try:
            with urllib.request.urlopen(base + path) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, None, e.read()

    def post(path, body):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield get, post, queued
    httpd.shutdown()


def test_voice_endpoint_serves_cache_only_and_queues_misses(api):
    get, post, queued = api
    code, _, _ = get("/api/voice?text=Last%20place.&style=plain")
    assert code == 404 and queued == [("Last place.", "plain", "Sulafat")]       # instant miss, made in the background
    p = voice.path_for("Last place.", "plain", "Sulafat")
    p.parent.mkdir(parents=True)
    p.write_bytes(voice.wav(b"\0\0" * 24000))
    code, ctype, body = get("/api/voice?text=Last%20place.&style=plain")
    assert code == 200 and ctype == "audio/wav" and body[:4] == b"RIFF"
    assert get("/api/voice?text=x&style=shouting")[0] == 404


def test_voice_is_configurable(api):
    get, post, queued = api
    assert post("/api/settings", {"viewer": "user-a", "voice": "Aoede"})[1]["voice"] == "Aoede"
    assert post("/api/settings", {"viewer": "user-a", "voice": "Robot"})[0] == 400
    get("/api/voice?text=Hallway.&style=calm")
    assert queued[-1] == ("Hallway.", "calm", "Aoede")

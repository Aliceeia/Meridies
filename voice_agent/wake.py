"""Offline "Hey Meri" wake word (Vosk, small English model, runs fine on a Pi 4).

The recognizer is limited to the wake phrase (plus [unk] for everything else), so it is cheap, never sends
audio anywhere, and doesn't use Gemini quota. "Meri" isn't an English word, so the sound-alikes Vosk does
know (mary, merry, marie) stand in for it.

    python wake.py            # self-test on the default mic (sounddevice), prints when it hears the phrase
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

import config

MODEL_DIR = config.HERE / "models" / "vosk-model-small-en-us-0.15"
MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
PHRASES = ["hey meri", "hey mary", "hey merry", "hey marie", "hi mary", "hi merry", "a mary", "hey mari"]
# Decoys: a grammar-limited recognizer forces speech onto the closest phrase it knows, so "hey everybody"
# became "hey marie". Giving common look-alikes their own entries lets them land somewhere harmless.
DECOYS = ["hey everybody", "hey everyone", "hey every", "hey there", "hey guys", "hey man", "hey buddy",
          "hey you", "hey maybe", "hey very", "hey many", "hey mike", "hey emily", "hey em", "every", "body",
          "everybody", "maybe", "very", "many", "memory", "marry", "married", "carry", "sorry", "hey"]
RATE = 16000


def ensure_model() -> Path:
    if MODEL_DIR.exists():
        return MODEL_DIR
    import io, urllib.request, zipfile
    print(f"[wake] downloading {MODEL_URL} (~40 MB, once)")
    with urllib.request.urlopen(MODEL_URL, timeout=300) as r:
        zipfile.ZipFile(io.BytesIO(r.read())).extractall(MODEL_DIR.parent)
    return MODEL_DIR


class WakeWord:
    """feed() 16 kHz int16 mono PCM from any thread; on_wake() is called (from the worker thread) on a match."""

    def __init__(self, on_wake, refractory: float = 2.0):
        import vosk
        vosk.SetLogLevel(-1)
        self.model = vosk.Model(str(ensure_model()))
        self.rec = vosk.KaldiRecognizer(self.model, RATE, json.dumps(PHRASES + DECOYS + ["[unk]"]))
        self.on_wake, self.refractory = on_wake, refractory
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.last = 0.0
        threading.Thread(target=self._loop, daemon=True).start()

    def feed(self, pcm: bytes):
        try:
            self.q.put_nowait(pcm)
        except queue.Full:            # never block the audio path; dropping a chunk only delays detection
            pass

    def _loop(self):
        # Constant beeps mean Vosk never hears a pause, so it never finalises: the "utterance" grows forever and
        # later wake words get lost in it (measured: 4 of 4 missed after 30 s of beeps). So segment it ourselves:
        # every SEGMENT_S restart the recognizer and re-feed the last OVERLAP_S, so a phrase across the cut survives.
        SEGMENT_S, OVERLAP_S = 3.0, 1.5
        import collections
        recent: collections.deque = collections.deque()
        recent_n = since_reset = 0
        while True:
            pcm = self.q.get()
            n = len(pcm) // 2
            recent.append(pcm); recent_n += n; since_reset += n
            while recent_n - len(recent[0]) // 2 >= OVERLAP_S * RATE:
                recent_n -= len(recent.popleft()) // 2
            final = self.rec.AcceptWaveform(pcm)
            res = json.loads(self.rec.Result() if final else self.rec.PartialResult())
            text = res.get("text") or res.get("partial") or ""
            if any(p in text for p in PHRASES) and time.time() - self.last > self.refractory:
                self.last = time.time()
                self.rec.Reset()
                recent.clear(); recent_n = since_reset = 0     # don't re-detect the same phrase from the overlap
                print(f"[wake] heard {text!r}", flush=True)
                self.on_wake()
            elif final:
                self.rec.Reset(); since_reset = 0
            elif since_reset >= SEGMENT_S * RATE:
                self.rec.Reset()
                for chunk in recent:
                    self.rec.AcceptWaveform(chunk)
                since_reset = recent_n


if __name__ == "__main__":
    import sounddevice as sd
    w = WakeWord(lambda: print("*** HEY MERI ***", flush=True))
    with sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=1600,
                           callback=lambda d, *_: w.feed(bytes(d))):
        print("say 'Hey Meri'... (Ctrl+C to stop)")
        while True:
            time.sleep(1)

"""Transcribe a memory's audio with Gemini.

Browsers record webm/opus, which Gemini does not list as a supported audio type, so the clip is converted to
16 kHz mono mp3 with ffmpeg first when ffmpeg is on PATH; without ffmpeg the original bytes are sent as they are.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import gemini

PROMPT = ("Transcribe this voice recording word for word, in the language it is spoken in. "
          "Output only the transcript: no labels, no timestamps, no commentary. "
          "If nobody speaks, output exactly: [no speech]")
GEMINI_AUDIO = {"audio/wav", "audio/mp3", "audio/mpeg", "audio/aiff", "audio/aac", "audio/ogg", "audio/flac"}


def to_mp3(path: Path) -> bytes | None:
    exe = shutil.which("ffmpeg")
    if not exe:
        return None
    r = subprocess.run([exe, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-b:a", "32k", "-f", "mp3", "-"],
                       capture_output=True, timeout=60)
    return r.stdout if r.returncode == 0 and r.stdout else None


def transcribe(path: Path, mime: str) -> dict:
    data = to_mp3(path)
    if data:
        mime = "audio/mp3"
    else:
        data = path.read_bytes()
        mime = mime if mime in GEMINI_AUDIO else "audio/ogg"     # opus-in-webm is closest to ogg/opus
    text = gemini.generate([{"text": PROMPT}, gemini.image_part(data, mime)], timeout=40, retries=1)
    text = text.strip()
    return dict(text="" if text == "[no speech]" else text, no_speech=text == "[no speech]", model=gemini.last_model)

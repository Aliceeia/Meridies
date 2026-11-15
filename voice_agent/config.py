"""Shared settings. Everything can be overridden by environment variables or the .env next to this file."""
from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load_env() -> None:
    # .env here first, then the kit-level .env the memory layer uses (/home/pi/meridies/.env or equivalent)
    for env in (HERE / ".env", HERE.parent / ".env"):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() and not line.lstrip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()


def get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def api_key() -> str:
    for n in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if os.environ.get(n):
            return os.environ[n]
    raise LookupError("GEMINI_API_KEY is not set (put it in gemini_voice_agent/.env)")


PI_HOST = get("PI_HOST", "172.20.10.5")
CAMERA_URL = get("CAMERA_URL", f"http://{PI_HOST}:8090/snapshot.jpg")
OBSTACLE_ENDPOINT = get("OBSTACLE_ENDPOINT", f"tcp://{PI_HOST}:5556")   # sensing_pi warnings, SUB only
MEMORY_URL = get("MEMORY_URL", "http://localhost:8765")                 # connect/server.py
MEMORY_VIEWER = get("MEMORY_VIEWER", "user-a")

LIVE_MODEL = get("LIVE_MODEL", "gemini-3.8-live")
VISION_MODEL = get("VISION_MODEL", "gemini-3.8-flash")
VISION_FALLBACKS = ("gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-flash-latest",
                    "gemini-3.5-flash-lite")
TRANSCRIBE_MODEL = get("TRANSCRIBE_MODEL", "gemini-3.5-transcribe")
VOICE = get("VOICE", "Aoede")
CACHE = HERE / "cache"

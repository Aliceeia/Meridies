"""Minimal Gemini REST client (stdlib only). Never logs the key.

Key: GEMINI_API_KEY (or GOOGLE_API_KEY, or the misspelt GEMNI_API_KEY) from the environment
or the .env one folder above the repo. Model: GEMINI_MODEL, default gemini-3.8-flash.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

KIT = Path(__file__).resolve().parents[3]  # folder above the repo: holds .env and models_index.csv
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMNI_API_KEY")


def secret(name: str) -> str | None:
    v = os.environ.get(name)
    env = KIT / ".env"
    if not v and env.exists():
        for line in env.read_text().splitlines():
            k, _, val = line.partition("=")
            if k.strip() == name:
                v = val.strip().strip('"').strip("'")
    return v or None


def model_name() -> str:
    # gemini-2.5-flash answers 404 "no longer available to new users" for new keys (2026-11-14)
    return secret("GEMINI_MODEL") or "gemini-3.8-flash"


def api_key() -> str:
    for n in KEY_NAMES:
        if (k := secret(n)):
            return k
    raise LookupError("GEMINI_API_KEY is not set (add it to the .env next to models_index.csv)")


def image_part(data: bytes, mime: str = "image/jpeg") -> dict:
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


def generate(parts: list, *, json_out: bool = False, retries: int = 3, timeout: int = 120) -> str:
    """parts: list of {"text": ...} or image_part(...). Returns the text answer."""
    body = {"contents": [{"role": "user", "parts": parts}]}
    if json_out:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_name()}:generateContent", method="POST",
        headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"}, data=json.dumps(body).encode())
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read())
            return " ".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"]).strip()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


if __name__ == "__main__":
    print("model:", model_name())
    print(generate([{"text": "Reply with exactly: ok"}]))

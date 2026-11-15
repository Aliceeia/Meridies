"""Camera frame -> Gemini vision -> short spoken-style answer.

The LiDAR summary is passed alongside the image so the model fuses the two: the camera says WHAT,
the time-of-flight sensors say HOW FAR. Directions are always body-relative (never compass words),
the same rule the memory layer follows.
"""
from __future__ import annotations

import asyncio
import json
import time

from google import genai
from google.genai import types

import config

BASE = (
    "You are the eyes of a blind or low-vision person. The image comes from a camera on the front of the cap "
    "they wear, at eye level, facing the way their head faces. Left/right in the image are their left/right. "
    "Never use compass directions. Use body-relative directions (ahead, slightly left, on your right) and "
    "distances in steps (1 step is about 0.7 m) or arm's lengths. Be brief: it will be spoken aloud. "
    "If the image is dark, blurry or blocked, say so plainly instead of guessing. "
    "Distance data from the cap's LiDAR, which is more accurate than your estimate: {lidar}\n"
    "If the LiDAR reports an urgent obstacle, start your answer with it and say what it most likely is in the "
    "image (or that it may be outside the camera's view).\n\n")

PROMPTS = {
    "describe": "Question from the person: {q}\nAnswer in at most 3 short sentences. Lead with the most "
                "important thing directly ahead, then anything on the left or right. Mention hazards first.",
    "find": "The person is looking for: {q}\nIf it is visible, say where it is relative to them and roughly how "
            "far, and how to reach it. If it is not visible, say so and suggest which way to turn to look.",
    "read": "Read out the text visible in the image that answers: {q}\nRead signs, labels, screens or documents "
            "verbatim where useful. If there is no readable text, say so.",
}

NAV_PROMPT = (
    "The person wants to: {q}\nGive the single next walking instruction. Prefer open floor, avoid people, "
    "furniture, steps and anything at head height. The LiDAR readings are HARD constraints: never say "
    "forward if LiDAR reports an obstacle within 1 m straight ahead or slightly left/right; steer to the open side, "
    "or say stop if both sides are blocked. Return JSON only:\n"
    '{{"action": "forward" | "slight_left" | "left" | "slight_right" | "right" | "stop" | "turn_around" | "arrived",'
    ' "steps": <int 0-10>, "say": "<one short spoken sentence>", "goal_visible": <bool>, "hazard": "<or empty>"}}')

NAV_SCHEMA = types.Schema(type="OBJECT", required=["action", "say"], properties={
    "action": types.Schema(type="STRING", enum=["forward", "slight_left", "left", "slight_right", "right", "stop",
                                                "turn_around", "arrived"]),
    "steps": types.Schema(type="INTEGER"),
    "say": types.Schema(type="STRING"),
    "goal_visible": types.Schema(type="BOOLEAN"),
    "hazard": types.Schema(type="STRING"),
})

_client: genai.Client | None = None
_skip_until: dict[str, float] = {}


def client() -> genai.Client:
    global _client
    _client = _client or genai.Client(api_key=config.api_key(), http_options=types.HttpOptions(api_version="v1beta"))
    return _client


async def _generate(contents, cfg: types.GenerateContentConfig, timeout=20) -> str:
    """Primary model, then fallbacks on quota/overload/timeout (same idea as connect/backend/gemini.py)."""
    chain = [config.VISION_MODEL] + [m for m in config.VISION_FALLBACKS if m != config.VISION_MODEL]
    chain = [m for m in chain if _skip_until.get(m, 0) <= time.monotonic()] or chain
    err = None
    for m in chain:
        try:
            r = await asyncio.wait_for(client().aio.models.generate_content(model=m, contents=contents, config=cfg),
                                       timeout)
            return (r.text or "").strip()
        except asyncio.TimeoutError as e:
            err = e; _skip_until[m] = time.monotonic() + 120
        except genai.errors.APIError as e:
            err = e
            if e.code in (429, 503, 404, 500):
                _skip_until[m] = time.monotonic() + {429: 3600, 404: 86400}.get(e.code, 120)
                continue
            raise
    raise RuntimeError(f"vision failed on every model: {err}")


def _cfg(**kw) -> types.GenerateContentConfig:
    # low thinking keeps "what am I looking at" at ~1-2 s
    return types.GenerateContentConfig(temperature=0.3, thinking_config=types.ThinkingConfig(thinking_level="low"), **kw)


async def ask(jpeg: bytes, mode: str, question: str, lidar: str) -> str:
    prompt = BASE.format(lidar=lidar) + PROMPTS[mode].format(q=question or "What am I looking at?")
    return await _generate([types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt], _cfg())


async def navigate(jpeg: bytes, goal: str, lidar: str) -> dict:
    prompt = BASE.format(lidar=lidar) + NAV_PROMPT.format(q=goal)
    txt = await _generate([types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"), prompt],
                          _cfg(response_mime_type="application/json", response_schema=NAV_SCHEMA))
    try:
        return json.loads(txt)
    except ValueError:
        return {"action": "stop", "say": txt[:200] or "I couldn't work out a direction."}

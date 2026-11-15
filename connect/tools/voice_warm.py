"""Make the system-voice clips a demo will need, ahead of time (Gemini TTS, cached under data/cache/voice/<voice>/).

    python connect/tools/voice_warm.py                 # current voice (settings.json, default Sulafat)
    python connect/tools/voice_warm.py --voice Aoede   # another voice, for A/B
    python connect/tools/voice_warm.py --count         # how many clips are missing, no calls
    python connect/tools/voice_warm.py --browse        # also drive the gallery's listen mode headless, so the server
                                                       # queues every line that page asks for (server must be running)

Same sentence split and delivery rule as frontend/speech.js, so keys match. Memories are never synthesised.
"""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import connect.server as srv  # noqa: E402
from connect.backend import spatial, voice  # noqa: E402

LISTEN_HELP = ("Left and right move between places. Up and down, between memories. Space plays or stops. "
               "D describes the place. M looks closer, which takes a few seconds. T takes you there. Escape leaves.")
FIXED = {   # kind -> lines (mirrors the strings in the frontend)
    "short": ["I couldn't get that — try again.", "First place.", "That's the first.", "Last place.", "That's the last one.",
              "First one here.", "Top of the list.", "Last one here.", "That's all here.", "Not described yet.",
              "Not described yet. M looks closer.", "Not on a family link.", "Taking you there. Any key when it opens.",
              "On the way. Press any key once it's open.", "H for help.", "Nothing here to play.", "Saved.",
              "Choose where you are first.", "There's no recording for this one.", "This is how I sound now.",
              "Here's the new voice.", "Recording — stay put", "Pick a highlighted spot", "Not walkable there",
              "Too far for one step — pick a highlighted spot", "Nothing behind you", "No way ahead — turn first",
              "Edge of the path", "Microphone isn't available", "Couldn't save — try again", "Hat is warning — memory muted",
              "Couldn't get that — try again", "No view here", "Walk a little closer to hear it",
              "That memory isn't here, or isn't yours to hear", "There's no recording for this one"],
    "help": [LISTEN_HELP, "Blindfold on. Arrows walk, W tells you where you are, Enter plays, M looks closer, Space to see again.",
             "Arrows move, Space plays, H for help.", "Space plays it."],
    "any": ["Saved, and private. Keep it private, or decide later?", "Saved. Keep it private, or share with the community?",
            "Kept private.", "Kept as a private draft. You can decide later.", "Sent for review. It stays private until it's approved.",
            "Recording stopped. It's saved as private.", "Still recording. Press R to stop."],
}
DIRS = ["ahead", "ahead left", "ahead right", "left", "right", "behind"]
WARNINGS = [f"{p}, {d}." for d in DIRS for p in ("Stop", "Careful", "Something")] + [f"Stop — {d}." for d in DIRS]


def style_of(sentence: str, kind: str) -> str:                      # = styleOf in speech.js
    if kind == "warning" or re.match(r"^(mind the|careful|stop|low )", sentence, re.I) or re.search(r"\bmetres?\b", sentence, re.I):
        return "alert"
    return "plain" if kind in ("short", "help") else "calm"


def sentences(text: str) -> list[str]:                              # = the split in speech.js
    return [s for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s]


def lines_for_samples() -> list[tuple[str, str]]:
    """Arrival and 'where am I' at every memory's spot and its take-me-there spot, plus each description."""
    out = []
    for m in srv.all_memories(include_hidden=True):
        if m["building_id"] not in srv.desc.BUILDINGS:
            continue
        plan = srv.desc.load_plan(m["building_id"])
        mems = [x for x in srv.all_memories(include_hidden=True) if x["building_id"] == m["building_id"]]
        spots = [(m["position"]["x"], m["position"]["y"], float(m.get("heading_deg") or 0))]
        if plan["kind"] == "interior" and m.get("sweep") is not None:          # where "Take me there" stands you
            own = next((s for s in plan["sweeps"] if s["index"] == m["sweep"]), None)
            for i in (own or {}).get("neighbours", [])[:6]:
                s = next(s for s in plan["sweeps"] if s["index"] == i)
                d = math.hypot(s["position"]["x"] - m["position"]["x"], s["position"]["y"] - m["position"]["y"])
                if 1.2 <= d <= 3.8:
                    h = math.degrees(math.atan2(m["position"]["x"] - s["position"]["x"], m["position"]["y"] - s["position"]["y"])) % 360
                    spots.append((s["position"]["x"], s["position"]["y"], h))
        for viewer in {m["author_id"], "user-a"}:
            vis = [x for x in mems if srv.can_see(x, viewer)]
            for x, y, h in spots:
                for _ in range(3):                                            # several phrasings (vary)
                    a = spatial.arrival(plan, x, y, h, vis, viewer, srv.name_of)
                    if a["text"]:
                        out.append((a["text"], "arrival"))
                    r = spatial.around(plan, x, y, h, vis, viewer, srv.name_of)
                    out.append((" ".join(filter(None, [r["bearings"], r["description"]])), "detail"))
        if m.get("description"):
            out.append((m["description"]["text"], "detail"))
    return out


LIMITS = {"arrival": (4, 20), "warning": (1, 2), "short": (2, 10), "detail": (5, 30), "more": (6, 30), "help": (8, 30), "any": (6, 30)}


def shape(text: str, kind: str) -> str:                             # = shape in speech.js (keeps whole sentences)
    n, secs = LIMITS.get(kind, LIMITS["any"])
    out = []
    for s in sentences(re.sub(r"\s{2,}", " ", text)):
        if len(out) >= n or len(" ".join(out + [s]).split()) / 2.5 > secs:
            break
        out.append(s[:1].upper() + s[1:])
    return " ".join(out)


def line_style(text: str, kind: str) -> str:                        # = lineStyle in speech.js
    st = [style_of(s, kind) for s in sentences(text)]
    if all(x == st[0] for x in st):
        return st[0]
    return "mixed" if "alert" in st else st[0]


def all_lines() -> list[tuple[str, str]]:
    """Whole lines, one clip each (natural intonation across sentences), shaped exactly as the page shapes them."""
    out = [(t, k) for k, ts in FIXED.items() for t in ts] + [(w, "warning") for w in WARNINGS] + lines_for_samples()
    clips, seen = [], set()
    for text, kind in out:
        t = shape(text, kind)
        k = (t, line_style(t, kind))
        if t and k not in seen:
            seen.add(k)
            clips.append(k)
    return clips


def browse():
    """Drive listen mode headless so the server queues every line it asks for (the server makes them)."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        for tab in ("Memories", "Shared with me"):
            p = b.new_page()
            p.add_init_script("localStorage.setItem('meridies.user', 'user-a')")
            p.goto("http://localhost:8765/")
            p.wait_for_selector(".entry")
            p.locator(".tab", has_text=tab).click()
            p.wait_for_timeout(600)
            p.keyboard.press("l")
            for _ in range(3):                      # three passes: the phrasings rotate
                n = p.evaluate("gallery.G.places.length")
                for _ in range(n):
                    p.wait_for_timeout(700)
                    for _ in range(p.evaluate("gallery.G.places[gallery.G.placeIdx].memory_count")):
                        p.keyboard.press("ArrowDown"); p.wait_for_timeout(500)
                    p.keyboard.press("d"); p.wait_for_timeout(500)
                    p.keyboard.press("ArrowRight")
                for _ in range(n):
                    p.keyboard.press("ArrowLeft"); p.wait_for_timeout(300)
            p.close()
        b.close()


# The free tier allows 10 TTS requests a day per model: these go first (heard most, never vary much).
PRIORITY = [("I couldn't get that — try again.", "plain"), ("Last place.", "plain"), ("First place.", "plain"),
            ("H for help.", "plain"), ("Arrows move, Space plays, H for help.", "plain"), ("Space plays it.", "plain"),
            ("Stop, ahead.", "alert"), ("Careful, ahead.", "alert"), ("Careful, ahead left.", "alert"),
            ("Careful, ahead right.", "alert"), ("There's no recording for this one.", "plain"),
            ("Last one here.", "plain"), ("Not described yet. M looks closer.", "plain")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default=srv.settings().get("voice", voice.DEFAULT_VOICE), choices=voice.VOICES)
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--browse", action="store_true")
    ap.add_argument("--priority", action="store_true", help="only the most-heard lines (fits the free daily quota)")
    a = ap.parse_args()
    clips = [(s, st) for t, st in PRIORITY for s in sentences(t)] if a.priority else all_lines()
    missing = [(t, s) for t, s in clips if not voice.path_for(t, s, a.voice).exists()]   # Gemini clips (fallback ones get upgraded)
    print(f"{len(clips)} clips needed for {a.voice}; {len(missing)} missing")
    if a.count:
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed
    made = failed = 0
    t0 = time.monotonic()

    def one(item):
        text, style = item
        voice.ensure(text, style, a.voice, gemini_only=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(one, it): it for it in missing}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                f.result(); made += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                text, style = futs[f]
                print(f"  FAILED {style:5} {text[:50]!r}: {voice.last_error or e!r}"[:160], flush=True)
            if i % 20 == 0:
                print(f"  [{i}/{len(missing)}] {made} made, {failed} failed, {time.monotonic() - t0:.0f}s", flush=True)
    print(f"done: {made} made, {failed} failed in {time.monotonic() - t0:.0f}s")
    if a.browse:
        browse()
        print("listen-mode lines queued on the server; it makes them in the background")


if __name__ == "__main__":
    main()

"""Sample memories so the gallery is never empty. Every one is marked is_sample: true.

    python connect/tools/seed_samples.py            # create / update the samples (keeps existing descriptions)
    python connect/tools/seed_samples.py remove     # delete every sample memory and its files
    (hide/show without deleting: profile menu -> Demo data, or POST /api/settings)

Your own samples (author user-a) and memories two blind friends shared with you (Maya, Theo: you are in their
circle, so they show under "Shared with me"). Each: a real sweep in the scan, a heading with a pre-rendered
first-person view (the cover), a scripted transcript, a neural voice (Microsoft Edge voices via edge-tts; one
voice per person), and a Gemini description from the existing describe pipeline.
"""
from __future__ import annotations

import asyncio
import json
import math
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import connect.server as srv  # noqa: E402
from connect.backend import places  # noqa: E402
from connect.backend.interior_render import Renderer  # noqa: E402

NS = uuid.UUID("6f1c3a52-6a0e-4d1b-9a43-0c2f5d7e9b11")
ME = "user-a"
VOICES = {   # one voice per person, so a memory sounds like who left it
    "user-a": ("en-US-AvaMultilingualNeural", "-4%"),
    "maya": ("en-US-EmmaMultilingualNeural", "-2%"),
    "theo": ("en-US-AndrewMultilingualNeural", "-6%"),
}
FRIENDS_SHARE_WITH = {"maya": [ME], "theo": [ME]}    # their circles include you

# author, building, room label (or "hazard:<kind>"), key, visibility, days ago, transcript
SAMPLES = [
    (ME, "2K7G7cdukZV", "Hallway", "p-arrive", "community", 1,
     "If you're coming in for the first time: the stairs are straight ahead, and there's a bench along the wall "
     "on your right if you need to wait for someone. It gets loud in here around noon."),
    (ME, "2K7G7cdukZV", "Hallway", "p-alone", "family", 2,
     "First time I walked through here on my own, nobody holding my arm. I counted the steps from the door. "
     "Twenty-two. I was so proud I called Mom from right here."),
    (ME, "2K7G7cdukZV", "Hallway", "p-door", "private", 2,
     "Note to self. The door past the stairs sticks. Push it with your shoulder, not your hand."),
    (ME, "zvKHR9DHNjJ", "hazard:chandelier", "l-chandelier", "family", 9,
     "Careful, the chandelier here hangs low. Grandpa hit his head on it every single Christmas, and he laughed every time."),
    (ME, "zvKHR9DHNjJ", "hazard:chandelier", "l-movies", "family", 12,
     "This is where we'd all pile in for movie night. Someone always fell asleep before the end. Usually Dad."),
    (ME, "zvKHR9DHNjJ", "hazard:chandelier", "l-radio", "family", 16,
     "Sunday mornings Grandma kept the radio on, right about here, and the whole house smelled like coffee and toast."),
    # ---- shared with you by blind friends ----
    ("maya", "2K7G7cdukZV", "Classroom", "f-maya-seats", "family", 3,
     "Okay, so, this is the classroom upstairs. Third row has the chairs without arms, which are way easier to slide "
     "into. And the professor stands on the left, so sit on the right if you want to hear her better."),
    ("maya", "2K7G7cdukZV", "Classroom", "f-maya-talk", "family", 6,
     "I gave my first presentation in this room. I had my notes in braille on my lap, and I didn't touch them once. "
     "I'm still kind of proud of that."),
    ("theo", "zvKHR9DHNjJ", "Kitchen", "f-theo-kettle", "family", 4,
     "Hey, it's Theo. The kettle's on the counter, right of the sink, and the mugs are in the cupboard just above it, "
     "second shelf. Help yourself. Seriously."),
    ("theo", "zvKHR9DHNjJ", "Living Room", "f-theo-piano", "family", 8,
     "There's an old piano against this wall. Nobody really plays it, but if you hold down the low keys, you can feel "
     "the whole floor hum under your feet. My niece loves that."),
]


def sample_id(key: str) -> str:
    return uuid.uuid5(NS, key).hex


def delete(mid: str):
    for f in srv.MEM.glob(f"{mid}*"):
        f.unlink()


def remove() -> int:
    n = 0
    for m in srv.all_memories(include_hidden=True):
        if m.get("is_sample"):
            delete(m["id"]); n += 1
    with srv.MEM_LOCK:                                   # the sample friends leave your circles too
        c = {k: v for k, v in srv.circles().items() if k not in FRIENDS_SHARE_WITH}
        srv.CIRCLES.write_text(json.dumps(c, indent=1))
    return n


def speak(text: str, who: str, out: Path):
    """Neural voice -> ogg/opus, loudness-normalised like a phone recording."""
    import edge_tts
    voice, rate = VOICES[who]
    mp3 = Path(tempfile.mkdtemp()) / "v.mp3"
    asyncio.run(edge_tts.Communicate(text, voice, rate=rate).save(str(mp3)))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-af", "loudnorm=I=-18:TP=-2:LRA=11", "-ac", "1",
                    "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", str(out)], check=True)


def cached_yaws(bid: str, sweep: int) -> list[int]:
    with Renderer(model=bid) as r:
        return [k for k in range(4) if any(r.cache_path(sweep + 1, f"0.000,{k * math.pi / 2:.3f}").with_suffix(sfx).exists()
                                           for sfx in (".jpg", ".png"))]


def spots_interior(bid: str, room: str, n: int):
    """n sweeps in ONE place (same room polygon), each with cached views, plus the heading to face."""
    plan = srv.desc.load_plan(bid)
    target = want = None
    if room.startswith("hazard:"):
        h = next(h for h in plan["hazards"] if h["kind"] == room.split(":", 1)[1])
        target = h["position"][:2]
        s0 = min(plan["sweeps"], key=lambda s: math.hypot(s["position"]["x"] - target[0], s["position"]["y"] - target[1]))
        want = places.place_of(dict(building_id=bid, position=s0["position"], sweep=s0["index"]), plan)[0]
    groups: dict[str, list] = {}
    for s in plan["sweeps"]:
        pid, name = places.place_of(dict(building_id=bid, position=s["position"], sweep=s["index"]), plan)
        if (pid == want) if want else (name == room):
            ys = cached_yaws(bid, s["index"])
            if ys:
                groups.setdefault(pid, []).append((s, ys))
    best = max(groups.values(), key=lambda g: (len(g), sum(len(y) for _, y in g)))
    if target:
        best.sort(key=lambda t: math.hypot(t[0]["position"]["x"] - target[0], t[0]["position"]["y"] - target[1]))
    else:
        best.sort(key=lambda t: -len(t[1]))
    picked = [best[0]]
    while len(picked) < min(n, len(best)):
        picked.append(max((t for t in best if t not in picked), key=lambda t: min(
            math.hypot(t[0]["position"]["x"] - p[0]["position"]["x"], t[0]["position"]["y"] - p[0]["position"]["y"]) for p in picked)))
    while len(picked) < n:
        picked.append(picked[len(picked) % len(best)])
    pid = places.place_of(dict(building_id=bid, position=picked[0][0]["position"], sweep=picked[0][0]["index"]), plan)[0]
    poly = next((r["polygon"] for r in plan["rooms"] if pid.endswith(r["id"])), None)
    cx, cy = (sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)) if poly else (0, 0)
    out = []
    for i, (s, ys) in enumerate(picked):
        tx, ty = target if (target and i == 0) else (cx, cy)
        face = math.degrees(math.atan2(tx - s["position"]["x"], ty - s["position"]["y"])) % 360
        k = min(ys, key=lambda k: abs(((-k * 90) % 360 - face + 540) % 360 - 180))
        x, y = s["position"]["x"], s["position"]["y"]
        if target and i == 0:   # a note about a hazard is left standing under it: anchor 0.6 m from it
            d = math.hypot(x - target[0], y - target[1])
            if d > 0.6:
                x, y = target[0] + 0.6 * (x - target[0]) / d, target[1] + 0.6 * (y - target[1]) / d
        out.append(dict(x=x, y=y, sweep=s["index"], heading=(-k * 90) % 360))
    return out


def save_circles():
    with srv.MEM_LOCK:
        c = srv.circles()
        for who, members in FRIENDS_SHARE_WITH.items():
            c[who] = sorted(set(c.get(who, [])) | set(members))
        srv.CIRCLES.write_text(json.dumps(c, indent=1))


def create():
    srv.MEM.mkdir(parents=True, exist_ok=True)
    keep = {sample_id(s[3]) for s in SAMPLES}
    gone = [m["id"] for m in srv.all_memories(include_hidden=True) if m.get("is_sample") and m["id"] not in keep]
    for mid in gone:
        delete(mid)
    spots: dict[tuple, list] = {}
    for _, bid, where, *_ in SAMPLES:
        spots[(bid, where)] = []
    for (bid, where) in spots:
        spots[(bid, where)] = spots_interior(bid, where, sum(1 for s in SAMPLES if (s[1], s[2]) == (bid, where)))
    used: dict[tuple, int] = {}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for author, bid, where, key, vis, days, text in SAMPLES:
        i = used.get((bid, where), 0)
        used[(bid, where)] = i + 1
        sp, mid = spots[(bid, where)][i], sample_id(key)
        old = srv.load(mid) if (srv.MEM / f"{mid}.json").exists() else {}
        for f in srv.MEM.glob(f"{mid}.*"):
            if f.suffix != ".json":
                f.unlink()                                   # the old (robotic) audio
        speak(text, author, srv.MEM / f"{mid}.ogg")
        same_spot = old.get("sweep") == sp["sweep"] and old.get("heading_deg") == round(sp["heading"], 1)
        meta = dict(id=mid, position=dict(x=round(sp["x"], 3), y=round(sp["y"], 3), z=1.5), building_id=bid, author_id=author,
                    created_at=old.get("created_at") or (now - timedelta(days=days, hours=len(key) % 9, minutes=len(text) % 50)).isoformat(),
                    visibility=vis, draft=False, share_request=None, reports=[],
                    description=old.get("description") if same_spot else None,
                    audio_type="audio/ogg", audio_file=f"{mid}.ogg", heading_deg=round(sp["heading"], 1), sweep=sp["sweep"],
                    is_sample=True, transcript=dict(status="done", text=text, source="script", at=now.isoformat()))
        if vis == "community":   # sample data: marked as seeded, never as a human review
            meta["share_request"] = dict(status="approved", requested_at=now.isoformat(),
                                         review=dict(reviewer="sample-seed (not a human review)", status="approved"))
        if not same_spot:
            for f in srv.MEM.glob(f"{mid}_*.jpg"):
                f.unlink()
        srv.save(meta)
        srv.cover_file(meta)
        print(f"{key:14} {author:7} {bid} sweep={sp['sweep']:>3} {vis:9} voice={VOICES[author][0]}", flush=True)
    for author, bid, where, key, *_ in SAMPLES:
        m = srv.load(sample_id(key))
        if m.get("description"):
            continue
        try:
            d = srv.desc.describe(m, srv.MEM)
            srv.update(m["id"], lambda m: m.__setitem__("description", d))
            print(f"described {key}: {d['text'][:80]}…", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"describe {key} FAILED: {e!r}"[:200], flush=True)
    save_circles()
    print(f"{len(SAMPLES)} samples ready, {len(gone)} removed; circles: {FRIENDS_SHARE_WITH}")


def redescribe():
    """Write the samples' descriptions again with the current prompt (one Gemini call each)."""
    for *_, key, _vis, _days, _text in [(s[0], s[1], s[2], s[3], s[4], s[5], s[6]) for s in SAMPLES]:
        m = srv.load(sample_id(key))
        try:
            d = srv.desc.describe(m, srv.MEM)
            srv.update(m["id"], lambda m: m.__setitem__("description", d))
            print(f"{key:14} {d['text']}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"{key:14} FAILED {e!r}"[:200], flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["remove"]:
        print(f"removed {remove()} sample memories")
    elif sys.argv[1:] == ["redescribe"]:
        redescribe()
    else:
        create()

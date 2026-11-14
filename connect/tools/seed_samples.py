"""Sample memories so the gallery is never empty. Every one is marked is_sample: true.

    python connect/tools/seed_samples.py            # create (or re-create) the 8 samples
    python connect/tools/seed_samples.py remove     # delete every sample memory and its files
    (hide/show without deleting: the "Samples" switch in the gallery header, or POST /api/settings)

Each sample: a real sweep in the scan, a heading that has a pre-rendered first-person view, a scripted
transcript, audio spoken by the Windows voice (Microsoft Zira, offline), the scan cover, and a Gemini
description from the existing describe pipeline. Yard covers are the drone-scan view (the yard has no
first-person renders).
"""
from __future__ import annotations

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

AUTHOR = "user-a"
NS = uuid.UUID("6f1c3a52-6a0e-4d1b-9a43-0c2f5d7e9b11")

# building, room label (interior) / zone index (yard), key, visibility, days ago, transcript
SAMPLES = [
    ("2K7G7cdukZV", "Hallway", "p-arrive", "community", 1,
     "If you're coming in for the first time: the stairs are straight ahead, and there's a bench along the wall "
     "on your right if you need to wait for someone. It gets loud in here around noon."),
    ("2K7G7cdukZV", "Hallway", "p-alone", "family", 2,
     "First time I walked through here on my own, nobody holding my arm. I counted the steps from the door. "
     "Twenty-two. I was so proud I called Mom from right here."),
    ("2K7G7cdukZV", "Hallway", "p-door", "private", 2,
     "Note to self. The door past the stairs sticks. Push it with your shoulder, not your hand."),
    ("zvKHR9DHNjJ", "hazard:chandelier", "l-chandelier", "family", 9,
     "Careful, the chandelier here hangs low. Grandpa hit his head on it every single Christmas, and he laughed every time."),
    ("zvKHR9DHNjJ", "hazard:chandelier", "l-movies", "family", 12,
     "This is where we'd all pile in for movie night. Someone always fell asleep before the end. Usually Dad."),
    ("zvKHR9DHNjJ", "hazard:chandelier", "l-radio", "family", 16,
     "Sunday mornings Grandma kept the radio on, right about here, and the whole house smelled like coffee and toast."),
    ("1PVeAYmPNJz", 1, "y-swing", "family", 20,
     "The swing used to hang from this branch. If you pushed hard enough, you could see over the fence into the neighbors' garden."),
    ("1PVeAYmPNJz", 1, "y-roots", "private", 5,
     "Mind the roots here, they lift the ground a little. I trip on them every time I'm not paying attention."),
]


def sample_id(key: str) -> str:
    return uuid.uuid5(NS, key).hex


def remove() -> int:
    n = 0
    for m in srv.all_memories():
        if m.get("is_sample"):
            for f in srv.MEM.glob(f"{m['id']}*"):
                f.unlink()
            n += 1
    return n


def tts(text: str, out: Path):
    """Windows SAPI (offline) -> wav -> ogg/opus."""
    wav = Path(tempfile.mkdtemp()) / "s.wav"
    ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'en-*' } | Select-Object -First 1; "
          "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) }; $s.Rate = -1; "
          f"$s.SetOutputToWaveFile('{wav}'); $s.Speak([Console]::In.ReadToEnd()); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], input=text.encode("utf-8"), check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ac", "1", "-c:a", "libopus", "-b:a", "32k", str(out)], check=True)


def cached_yaws(bid: str, sweep: int) -> list[int]:
    with Renderer(model=bid) as r:
        return [k for k in range(4) if any(r.cache_path(sweep + 1, f"0.000,{k * math.pi / 2:.3f}").with_suffix(sfx).exists()
                                           for sfx in (".jpg", ".png"))]


def spots_interior(bid: str, room: str, n: int):
    """n sweeps in ONE place (same room polygon), each with cached views, plus the heading to face."""
    plan = srv.desc.load_plan(bid)
    target = None
    if room.startswith("hazard:"):
        h = next(h for h in plan["hazards"] if h["kind"] == room.split(":", 1)[1])
        target = h["position"][:2]
        s0 = min(plan["sweeps"], key=lambda s: math.hypot(s["position"]["x"] - target[0], s["position"]["y"] - target[1]))
        want = places.place_of(dict(building_id=bid, position=s0["position"], sweep=s0["index"]), plan)[0]
    groups: dict[str, list] = {}
    for s in plan["sweeps"]:
        pid, name = places.place_of(dict(building_id=bid, position=s["position"], sweep=s["index"]), plan)
        ys = cached_yaws(bid, s["index"])
        if ys and (room.startswith("hazard:") and pid == want or name == room):
            groups.setdefault(pid, []).append((s, ys))
    best = max(groups.values(), key=lambda g: (len(g), sum(len(y) for _, y in g)))
    if target:   # the hazard memory first: the sweep nearest the hazard
        best.sort(key=lambda t: math.hypot(t[0]["position"]["x"] - target[0], t[0]["position"]["y"] - target[1]))
    else:        # spread out: farthest-first from the group's first sweep
        best.sort(key=lambda t: -len(t[1]))
    picked = [best[0]]
    while len(picked) < n:
        picked.append(max((t for t in best if t not in picked), key=lambda t: min(
            math.hypot(t[0]["position"]["x"] - p[0]["position"]["x"], t[0]["position"]["y"] - p[0]["position"]["y"]) for p in picked)))
    rooms = [r for r in plan["rooms"] if places.place_of(dict(building_id=bid, position=picked[0][0]["position"],
                                                                sweep=picked[0][0]["index"]), plan)[0].endswith(r["id"])]
    c = rooms[0]["polygon"] if rooms else None
    cx, cy = (sum(p[0] for p in c) / len(c), sum(p[1] for p in c) / len(c)) if c else (0, 0)
    out = []
    for i, (s, ys) in enumerate(picked):
        tx, ty = (target if (target and i == 0) else (cx, cy))
        face = math.degrees(math.atan2(tx - s["position"]["x"], ty - s["position"]["y"])) % 360
        # map heading -> cached yaw k: k = round(-heading / 90) % 4; pick the cached k closest to the wanted heading
        k = min(ys, key=lambda k: abs(((-k * 90) % 360 - face + 540) % 360 - 180))
        out.append(dict(x=s["position"]["x"], y=s["position"]["y"], sweep=s["index"], heading=(-k * 90) % 360))
    return out


def spots_yard(zone: int, n: int):
    plan = srv.desc.load_plan("1PVeAYmPNJz")
    z = plan["zones"][zone]
    cx, cy, r = z["center"][0], z["center"][1], z["radius_m"] * 0.45
    out = []
    for i in range(n):
        a = math.radians(40 + i * 150)
        x, y = cx + r * math.sin(a), cy + r * math.cos(a)
        out.append(dict(x=x, y=y, sweep=None, heading=(math.degrees(math.atan2(cx - x, cy - y))) % 360))
    return out


def create():
    removed = remove()
    srv.MEM.mkdir(parents=True, exist_ok=True)
    spots: dict[tuple, list] = {}
    for bid, where, *_ in SAMPLES:
        spots.setdefault((bid, where), [])
    for (bid, where) in spots:
        n = sum(1 for s in SAMPLES if (s[0], s[1]) == (bid, where))
        spots[(bid, where)] = spots_interior(bid, where, n) if isinstance(where, str) else spots_yard(where, n)
    used: dict[tuple, int] = {}
    now = datetime.now(timezone.utc).replace(microsecond=0)
    made = []
    for bid, where, key, vis, days, text in SAMPLES:
        i = used.get((bid, where), 0)
        used[(bid, where)] = i + 1
        sp = spots[(bid, where)][i]
        mid = sample_id(key)
        tts(text, srv.MEM / f"{mid}.ogg")
        when = now - timedelta(days=days, hours=(hash(key) % 9), minutes=(hash(key) % 50))
        meta = dict(id=mid, position=dict(x=round(sp["x"], 3), y=round(sp["y"], 3), z=1.5), building_id=bid, author_id=AUTHOR,
                    created_at=when.isoformat(timespec="seconds"), visibility=vis, draft=False, share_request=None, reports=[],
                    description=None, audio_type="audio/ogg", audio_file=f"{mid}.ogg", heading_deg=round(sp["heading"], 1),
                    is_sample=True, transcript=dict(status="done", text=text, source="script", at=now.isoformat()))
        if sp["sweep"] is not None:
            meta["sweep"] = sp["sweep"]
        if vis == "community":   # sample data: marked as seeded, never as a human review
            meta["share_request"] = dict(status="approved", requested_at=now.isoformat(),
                                         review=dict(reviewer="sample-seed (not a human review)", status="approved"))
        srv.save(meta)
        cover = srv.cover_file(meta)
        print(f"{key:13} {bid} sweep={meta.get('sweep')} heading={meta['heading_deg']:5.1f} {vis:9} cover={cover.name if cover else None}",
              flush=True)
        made.append(mid)
    for mid in made:   # the existing Gemini describe pipeline (first-person + aerial + plan for interiors)
        m = srv.load(mid)
        try:
            d = srv.desc.describe(m, srv.MEM)
            srv.update(mid, lambda m: m.__setitem__("description", d))
            print(f"described {mid[:8]}: {d['text'][:90]}…", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"describe {mid[:8]} FAILED: {e!r}"[:200], flush=True)
    print(f"{len(made)} samples created ({removed} old samples replaced)")


if __name__ == "__main__":
    if sys.argv[1:] == ["remove"]:
        print(f"removed {remove()} sample memories")
    else:
        create()

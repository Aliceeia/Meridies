"""Step 5: warning level per object (with hysteresis) and the top-N list for the audio engine."""
from __future__ import annotations

import math

from .describe import Obj

REQUIRED = ("id", "az", "dist", "level")   # what audio/mixer.py reads; everything else is extra


def _level(o: Obj, prev: str | None, c: dict) -> str | None:
    d, ttc = o.dist, o.ttc_s
    if o.kind == "wall_parallel":                       # corridor wall beside you: quiet cue only when very close
        limit = c["parallel_wall_dist_m"] + (0.1 if prev else 0.0)
        return "wall" if d < limit else None
    if d < c["urgent_dist_m"] or ttc < c["urgent_ttc_s"]:
        return "urgent"
    if d < c["warn_dist_m"] or ttc < c["warn_ttc_s"]:
        return "warn"
    if prev and (d < c["clear_dist_m"] or ttc < c["clear_ttc_s"]):
        return "warn"                                   # hysteresis: stay on until clearly gone
    return None


def score(o: Obj, c: dict) -> float:
    front = max(0.0, 0.4 + 0.6 * math.cos(math.radians(o.az)))
    near = max(1.0 / max(o.dist, 0.05), 2.0 / o.ttc_s if math.isfinite(o.ttc_s) and o.ttc_s > 0 else 0.0)
    return front * c["kind_weight"].get(o.kind, 0.5) * near


def rank(objs: list[Obj], c: dict) -> list[dict]:
    """Set .level/.score on every object; return the warning list (<= max_warnings, nearest first)."""
    for o in objs:
        tr = o.extra.get("track")
        o.level = _level(o, tr.level if tr else None, c)
        if tr and tr.fresh_hits < c.get("min_fresh_frames", 1):
            o.level = None                              # a short blip (stray reading, shoulder): don't beep yet
        if tr:
            tr.level = o.level
        o.score = score(o, c)
    active = sorted((o for o in objs if o.level), key=lambda o: -o.score)[: c["max_warnings"]]
    active.sort(key=straight_dist)
    return [warning_entry(o) for o in active]


def straight_dist(o: Obj) -> float:
    """Straight-line distance from the head centre to the object's nearest point (m).
    o.dist is the horizontal part; o.el is that point's elevation."""
    return max(o.dist, 0.0) / max(math.cos(math.radians(o.el)), 0.05)


def warning_entry(o: Obj) -> dict:
    # "dist" is the straight-line distance, which is what audio/mixer.py (and its simulator) expect:
    # it recovers the horizontal part itself as dist * cos(el). "hdist" is the horizontal distance.
    w = {"id": o.id, "az": round(o.az, 1), "dist": round(straight_dist(o), 3), "level": o.level,
         "hdist": round(max(o.dist, 0.0), 3),
         "kind": o.kind, "el": round(o.el, 1), "az_l": round(o.az_l, 1), "az_r": round(o.az_r, 1),
         "width_m": round(o.width_m, 3), "height_m": [round(o.z_min, 2), round(o.z_max, 2)],
         "closing_mps": round(o.closing_mps, 2)}
    if math.isfinite(o.ttc_s):
        w["ttc_s"] = round(o.ttc_s, 2)
    return w

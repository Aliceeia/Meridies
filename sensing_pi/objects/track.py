"""Track objects across frames: stable ids, smoothed position, closing speed and time-to-contact.
Closing speed follows integration/obstacle_select.py (least-squares slope over a short window)."""
from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field

from .describe import Obj


@dataclass
class Track:
    id: str
    az: float
    dist: float
    az_l: float
    az_r: float
    last_seen: float
    history: deque = field(default_factory=lambda: deque(maxlen=64))   # (t, raw nearest dist)
    level: str | None = None
    fresh_hits: int = 0   # frames in which this object had readings from that very frame (not only held map points)


def _slope(samples) -> float:
    n = len(samples)
    mt = sum(t for t, _ in samples) / n
    md = sum(d for _, d in samples) / n
    den = sum((t - mt) ** 2 for t, _ in samples)
    return 0.0 if den < 1e-9 else sum((t - mt) * (d - md) for t, d in samples) / den


class Tracker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.tracks: dict[str, Track] = {}
        self._ids = itertools.count(1)

    def _cost(self, tr: Track, o: Obj) -> float | None:
        c = self.cfg
        dd = abs(o.dist - tr.dist)
        if dd > c["track_gate_dist_m"]:
            return None
        overlap = o.az_l <= tr.az_r + 3 and o.az_r >= tr.az_l - 3      # spans overlap: wide objects/walls
        da = 0.0 if overlap else abs(o.az - tr.az)
        if da > c["track_gate_az_deg"]:
            return None
        return da / c["track_gate_az_deg"] + dd / c["track_gate_dist_m"]

    def update(self, objs: list[Obj], t: float) -> list[Obj]:
        c = self.cfg
        pairs = []
        for tid, tr in self.tracks.items():
            for i, o in enumerate(objs):
                cost = self._cost(tr, o)
                if cost is not None:
                    pairs.append((cost, tid, i))
        used_t, used_o = set(), set()
        for cost, tid, i in sorted(pairs):
            if tid in used_t or i in used_o:
                continue
            used_t.add(tid)
            used_o.add(i)
            self._apply(self.tracks[tid], objs[i], t)
        for i, o in enumerate(objs):
            if i not in used_o:
                tr = Track(id=f"O{next(self._ids)}", az=o.az, dist=o.dist, az_l=o.az_l, az_r=o.az_r, last_seen=t)
                self.tracks[tr.id] = tr
                self._apply(tr, o, t, fresh=True)
        for tid in [tid for tid, tr in self.tracks.items() if t - tr.last_seen > c["track_drop_s"]]:
            del self.tracks[tid]
        return objs

    def _apply(self, tr: Track, o: Obj, t: float, fresh: bool = False) -> None:
        c = self.cfg
        a = 1.0 if fresh else c["track_ema"]
        tr.az += a * (((o.az - tr.az + 180) % 360) - 180)
        tr.dist += a * (o.dist - tr.dist)
        tr.az_l, tr.az_r, tr.last_seen = o.az_l, o.az_r, t
        tr.fresh_hits += any(abs(p.t - t) < 1e-6 for p in o.points)
        tr.history.append((t, o.dist))
        recent = [(ts, d) for ts, d in tr.history if t - ts <= c["ttc_window_s"] + 1e-6]
        closing = -_slope(recent) if len(recent) >= c["ttc_min_samples"] else 0.0
        o.id, o.az, o.dist = tr.id, tr.az, tr.dist
        o.closing_mps = closing
        o.ttc_s = o.dist / closing if closing > c["min_closing_mps"] else math.inf
        o.extra["track"] = tr

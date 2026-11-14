"""Steps 3-4 of the design: describe each group (nearest point, span, width, height, kind) and detect walls."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .scan import ScanPoint


@dataclass
class Obj:
    points: list[ScanPoint]
    az: float                  # nearest point, deg (+ right)
    el: float                  # nearest point, deg (+ up)
    dist: float                # nearest point, horizontal m
    az_l: float                # leftmost azimuth, deg
    az_r: float                # rightmost azimuth, deg
    width_m: float             # distance between the two end points
    z_min: float
    z_max: float
    n_bins: int
    kind: str                  # narrow | wide | wall_facing | wall_oblique | wall_parallel
    wall_angle_deg: float | None = None   # 0 = runs along the walking direction, 90 = faces you
    wall_resid_m: float | None = None
    wall_line: tuple | None = None        # ((x1, y1), (x2, y2)) in head frame
    # filled by the tracker / ranker
    id: str = ""
    closing_mps: float = 0.0
    ttc_s: float = math.inf
    level: str | None = None
    score: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"id": self.id, "az": round(self.az, 1), "dist": round(self.dist, 3), "el": round(self.el, 1),
             "az_l": round(self.az_l, 1), "az_r": round(self.az_r, 1), "width_m": round(self.width_m, 3),
             "height_m": [round(self.z_min, 2), round(self.z_max, 2)], "n_bins": self.n_bins,
             "kind": self.kind, "level": self.level, "score": round(self.score, 3),
             "closing_mps": round(self.closing_mps, 2)}
        if math.isfinite(self.ttc_s):
            d["ttc_s"] = round(self.ttc_s, 2)
        if self.wall_line is not None:
            d["wall_line"] = [[round(v, 3) for v in pt] for pt in self.wall_line]
            d["wall_angle_deg"] = round(self.wall_angle_deg, 1)
        return d


def _wall_fit(xy: np.ndarray):
    """Total-least-squares line through xy (N,2). Returns (angle_to_forward_deg, max_resid, endpoints)."""
    c = xy.mean(axis=0)
    _, _, vt = np.linalg.svd(xy - c)
    v = vt[0]                                   # line direction
    n = np.array([-v[1], v[0]])                 # line normal
    resid = np.abs((xy - c) @ n)
    angle = math.degrees(math.acos(min(1.0, abs(v[0]))))  # vs forward axis (1, 0)
    s = (xy - c) @ v
    ends = (tuple(c + s.min() * v), tuple(c + s.max() * v))
    return angle, float(resid.max()), ends


def _is_wall_piece(group, cfg) -> bool:
    if len(group) < cfg["wall_min_bins"]:
        return False
    xy = np.array([p.xy for p in group])
    if np.linalg.norm(xy[-1] - xy[0]) < cfg["wall_min_width_m"]:
        return False
    return _wall_fit(xy)[1] <= cfg["wall_max_resid_m"]


def split_corners(group: list[ScanPoint], cfg: dict, depth: int = 3) -> list[list[ScanPoint]]:
    """A continuous surface that isn't one straight line (e.g. a corridor corner) is cut at the
    point furthest from its end-to-end chord, but only if a resulting piece is a clean wall.
    Round or irregular objects (people, chairs) stay whole, so they still get one sound."""
    n = len(group)
    if depth == 0 or n < cfg["wall_min_bins"] + 2 or _is_wall_piece(group, cfg):
        return [group]
    xy = np.array([p.xy for p in group])
    chord = xy[-1] - xy[0]
    L = np.linalg.norm(chord)
    if L < 1e-6:
        return [group]
    dev = np.abs((xy[:, 0] - xy[0, 0]) * chord[1] - (xy[:, 1] - xy[0, 1]) * chord[0]) / L
    i = int(np.argmax(dev[1:-1])) + 1
    if dev[i] < 2 * cfg["wall_max_resid_m"]:
        return [group]
    for a, b in ((group[:i + 1], group[i + 1:]), (group[:i], group[i:])):   # corner point goes to either side
        if _is_wall_piece(a, cfg) or _is_wall_piece(b, cfg):
            return split_corners(a, cfg, depth - 1) + split_corners(b, cfg, depth - 1)
    return [group]


def describe(group: list[ScanPoint], cfg: dict) -> Obj:
    newest = max(p.t for p in group)
    near = min((p for p in group if p.t >= newest - 1e-6), key=lambda p: p.dist)  # map points only fill gaps
    left, right = group[0], group[-1]
    xy = np.array([p.xy for p in group])
    width = float(np.linalg.norm(right.xy - left.xy))
    zs = [p.head[2] for p in group]
    n = len(group)
    kind = "narrow" if (width < cfg["narrow_width_m"] or n < cfg["narrow_min_bins"]) else "wide"
    obj = Obj(points=group, az=near.az, el=near.el, dist=near.dist, az_l=left.az, az_r=right.az,
              width_m=width, z_min=min(zs), z_max=max(zs), n_bins=n, kind=kind)

    if n >= cfg["wall_min_bins"] and width >= cfg["wall_min_width_m"]:
        angle, resid, ends = _wall_fit(xy)
        if resid <= cfg["wall_max_resid_m"]:
            obj.wall_angle_deg, obj.wall_resid_m, obj.wall_line = angle, resid, ends
            if angle < cfg["wall_parallel_deg"]:
                obj.kind = "wall_parallel"
            elif angle > cfg["wall_facing_deg"]:
                obj.kind = "wall_facing"
            else:
                obj.kind = "wall_oblique"
    return obj

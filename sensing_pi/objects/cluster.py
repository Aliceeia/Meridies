"""Step 4: group neighbouring scan points into objects by distance similarity along azimuth."""
from __future__ import annotations

from .scan import ScanPoint


def cluster(points: list[ScanPoint], cfg: dict) -> list[list[ScanPoint]]:
    """points must be sorted by azimuth. Neighbours join when their ranges look like one
    continuous surface and no large angular gap separates them."""
    if not points:
        return []
    groups, cur = [], [points[0]]
    for prev, p in zip(points, points[1:]):
        same = (abs(p.dist - prev.dist) < max(cfg["same_abs_m"], cfg["same_rel"] * prev.dist)
                and p.az - prev.az <= cfg["max_gap_deg"])
        if same:
            cur.append(p)
        else:
            groups.append(cur)
            cur = [p]
    groups.append(cur)
    return groups

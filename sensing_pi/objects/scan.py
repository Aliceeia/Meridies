"""Step 1-2: point cloud -> height band -> azimuth-binned scan (one point per bin)."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from geometry.orientation import HeadOrientation, world_to_head


@dataclass
class ScanPoint:
    t: float                 # capture time, s
    world: np.ndarray        # (3,) gravity-aligned frame at capture (== head frame without an IMU)
    head: np.ndarray         # (3,) head frame, at the time it was last re-projected
    sensor: int
    zone: int

    @property
    def az(self) -> float:   # deg, + right
        return math.degrees(math.atan2(self.head[1], self.head[0]))

    @property
    def el(self) -> float:   # deg, + up
        return math.degrees(math.atan2(self.head[2], math.hypot(self.head[0], self.head[1])))

    @property
    def dist(self) -> float:  # horizontal range from the head centre, m
        return math.hypot(self.head[0], self.head[1])

    @property
    def xy(self) -> np.ndarray:  # x forward, y right
        return self.head[:2]


def cloud_to_points(cloud, t: float, cfg: dict) -> list[ScanPoint]:
    """Valid points inside the height band and range limit (no binning yet)."""
    k = cloud.valid.copy()
    world = cloud.world if cloud.world is not None else cloud.xyz
    lo, hi = cfg["band_z_m"]
    k &= (world[:, 2] >= lo) & (world[:, 2] <= hi)
    k &= np.hypot(cloud.xyz[:, 0], cloud.xyz[:, 1]) <= cfg["max_range_m"]
    return [ScanPoint(t, world[i].copy(), cloud.xyz[i].copy(), int(cloud.sensor[i]), int(cloud.zone[i]))
            for i in np.flatnonzero(k)]


def reproject(points: list[ScanPoint], o: HeadOrientation | None) -> None:
    """Refresh .head from .world for the current head orientation (no-op without an IMU)."""
    if o is None or not points:
        return
    head = world_to_head(np.array([p.world for p in points]), o)
    for p, h in zip(points, head):
        p.head = h


def bin_scan(points: list[ScanPoint], bin_deg: float) -> list[ScanPoint]:
    """One point per azimuth bin: the newest capture time wins (old map points only fill gaps),
    then the nearest. Returned sorted by azimuth."""
    best: dict[int, ScanPoint] = {}
    for p in points:
        b = math.floor(p.az / bin_deg)
        q = best.get(b)
        if q is None or p.t > q.t + 1e-6 or (abs(p.t - q.t) <= 1e-6 and p.dist < q.dist):
            best[b] = p
    return [best[b] for b in sorted(best)]

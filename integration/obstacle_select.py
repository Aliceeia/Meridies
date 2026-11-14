"""Time-to-collision obstacle selection for the SoundHat sector sensors.

Input is the hat's wire format, one frame per tick:
    {"t": <ms>, "mm": [812, 1430, None, 3900, 640, None]}   # 6 sectors, None = no return
plus the IMU yaw rate (gyro_z, deg/s).

We warn on *time to collision*, never on raw distance:
    closing_speed = -d(distance)/dt     (positive = approaching)
    ttc = distance / closing_speed      if closing_speed > MIN_CLOSING_MPS else inf
    WARN if ttc < 2.0 s, URGENT if ttc < 0.8 s
    Suppress everything while |gyro_z| > 60 deg/s (head turn).

So a wall you walk parallel to (constant distance, ~0 closing speed) stays silent,
while something closing on you warns however far away it starts.

Closing speed is the least-squares slope over a short window, not a two-sample
difference, so 20 Hz sensor jitter does not cause false alarms.
Pure Python, no dependencies, so it ports to the Pi unchanged.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import IntEnum
import math

SECTOR_AZIMUTHS_DEG = (-15, 15, -45, 45, -80, 80)
WARN_TTC_S = 2.0
URGENT_TTC_S = 0.8
MIN_CLOSING_MPS = 0.1
GYRO_SUPPRESS_DPS = 60.0
WINDOW_S = 0.30          # slope window: about 6 samples at 20 Hz
MAX_RANGE_M = 4.0
DROPOUT_HOLD_S = 0.15    # carry the last estimate through a short dropout


class Level(IntEnum):
    SILENT = 0
    WARN = 1
    URGENT = 2


@dataclass(frozen=True)
class SectorState:
    sector: int
    azimuth_deg: int
    distance_m: float | None
    closing_mps: float
    ttc_s: float
    level: Level


def _level(ttc: float) -> Level:
    if ttc < URGENT_TTC_S:
        return Level.URGENT
    if ttc < WARN_TTC_S:
        return Level.WARN
    return Level.SILENT


def _slope(samples) -> float:
    """Least-squares d(distance)/dt over (t, d) samples."""
    n = len(samples)
    mt = sum(t for t, _ in samples) / n
    md = sum(d for _, d in samples) / n
    var = sum((t - mt) ** 2 for t, _ in samples)
    if var <= 0:
        return 0.0
    return sum((t - mt) * (d - md) for t, d in samples) / var


class SectorTracker:
    def __init__(self, sector: int):
        self.sector = sector
        self.azimuth = SECTOR_AZIMUTHS_DEG[sector]
        self.hist: deque[tuple[float, float]] = deque()
        self.last: SectorState = self._silent(None)
        self.last_t: float | None = None

    def _silent(self, d):
        return SectorState(self.sector, self.azimuth, d, 0.0, math.inf, Level.SILENT)

    def reset(self) -> None:
        self.hist.clear()
        self.last = self._silent(None)
        self.last_t = None

    def update(self, t: float, d: float | None) -> SectorState:
        if d is None or d > MAX_RANGE_M:
            # Hold a recent estimate through a short dropout; otherwise go quiet.
            if self.last_t is not None and t - self.last_t <= DROPOUT_HOLD_S:
                return self.last
            self.hist.clear()
            self.last = self._silent(None)
            return self.last

        self.hist.append((t, d))
        while self.hist and t - self.hist[0][0] > WINDOW_S:
            self.hist.popleft()
        self.last_t = t

        if len(self.hist) < 3:
            self.last = self._silent(d)
            return self.last

        closing = -_slope(self.hist)
        ttc = d / closing if closing > MIN_CLOSING_MPS else math.inf
        self.last = SectorState(self.sector, self.azimuth, d, closing, ttc, _level(ttc))
        return self.last


@dataclass(frozen=True)
class Frame:
    t_s: float
    sectors: tuple[SectorState, ...]
    suppressed: bool

    @property
    def level(self) -> Level:
        return max((s.level for s in self.sectors), default=Level.SILENT)

    @property
    def worst(self) -> SectorState | None:
        """The sector to sonify first: highest level, then lowest ttc."""
        active = [s for s in self.sectors if s.level > Level.SILENT]
        return min(active, key=lambda s: (-s.level, s.ttc_s)) if active else None


class ObstacleSelector:
    def __init__(self, n_sectors: int = len(SECTOR_AZIMUTHS_DEG)):
        self.trackers = [SectorTracker(i) for i in range(n_sectors)]

    def update(self, t_s: float, mm: list[int | None], gyro_z_dps: float = 0.0) -> Frame:
        if abs(gyro_z_dps) > GYRO_SUPPRESS_DPS:
            # Head turning: sector distances swing for reasons that aren't motion
            # toward an obstacle. Drop history so stale slopes don't fire afterwards.
            for tr in self.trackers:
                tr.reset()
            return Frame(t_s, tuple(tr.last for tr in self.trackers), suppressed=True)
        states = tuple(
            tr.update(t_s, None if v is None else v / 1000.0)
            for tr, v in zip(self.trackers, mm)
        )
        return Frame(t_s, states, suppressed=False)

    def update_msg(self, msg: dict, gyro_z_dps: float = 0.0) -> Frame:
        """Accepts the raw hat message {"t": ms, "mm": [...]}."""
        return self.update(msg["t"] / 1000.0, msg["mm"], gyro_z_dps)

"""The two TTC cases that are the demo, plus head-turn suppression.

Simulated at 20 Hz with +/-10 mm sensor jitter (seeded) so the tests are not
passing only on perfect data.
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.obstacle_select import (  # noqa: E402
    SECTOR_AZIMUTHS_DEG, Level, ObstacleSelector,
)

HZ = 20
DT = 1.0 / HZ


def jitter(rng, d_m):
    return None if d_m is None or d_m > 4.0 else int(round(d_m * 1000 + rng.uniform(-10, 10)))


def test_parallel_wall_at_0_6m_is_silent():
    """Walk 5 s at 1.1 m/s alongside a wall 0.6 m to the right. Must never warn."""
    rng = random.Random(1)
    sel = ObstacleSelector()
    lateral = 0.6
    levels = []
    for i in range(5 * HZ):
        mm = []
        for az in SECTOR_AZIMUTHS_DEG:
            s = math.sin(math.radians(az))
            # Ray hits the wall on the right (positive azimuth) at lateral/sin(az).
            mm.append(jitter(rng, lateral / s if s > 0 else None))
        levels.append(sel.update(i * DT, mm).level)
    assert max(levels) == Level.SILENT, f"parallel wall warned: {levels}"


def test_object_closing_at_1mps_from_2m_warns():
    """Object straight ahead, 2 m away, closing at 1 m/s. Must warn, then go urgent."""
    rng = random.Random(2)
    sel = ObstacleSelector()
    frames = []
    for i in range(int(1.6 * HZ)):            # 2.0 m -> 0.4 m
        d = 2.0 - 1.0 * i * DT
        mm = [jitter(rng, d), jitter(rng, d), None, None, None, None]
        frames.append(sel.update(i * DT, mm))

    first_warn = next(f for f in frames if f.level >= Level.WARN)
    assert first_warn.t_s <= 0.3, f"warned too late at t={first_warn.t_s}"
    assert first_warn.worst.ttc_s < 2.0
    assert any(f.level == Level.URGENT for f in frames)
    assert 0.9 < first_warn.worst.closing_mps < 1.1


def test_head_turn_suppresses_warnings():
    rng = random.Random(3)
    sel = ObstacleSelector()
    for i in range(int(1.2 * HZ)):
        d = 2.0 - 1.0 * i * DT
        mm = [jitter(rng, d)] * 2 + [None] * 4
        f = sel.update(i * DT, mm, gyro_z_dps=120.0)
        assert f.suppressed and f.level == Level.SILENT

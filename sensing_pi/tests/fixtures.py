"""Shared fixed rigs for the tests.

The tests use their own copies of the sensor layouts, so editing the real measurements in
config/sensor_layout.py (or learning a self-mask) never changes what the tests check.
Importing this module also puts sensing_pi/ on sys.path.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # sensing_pi/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geometry import load_mounts  # noqa: E402
from geometry.layout import with_rows  # noqa: E402

# (x_cm, y_cm, angle_deg, tilt_deg, height_cm), same columns as config/sensor_layout.py

PLANNED_CAP = {   # the planned cap layout from docs/cap_layout.md
    "S1": (-8.0, 15.0, 40.0, -10.0, 7.0), "S2": (0.0, 17.0, 0.0, -10.0, 7.0),
    "S3": (8.0, 15.0, 320.0, -10.0, 7.0), "S4": (-9.0, 0.0, 85.0, -5.0, 8.0),
    "S5": (0.0, 14.0, 0.0, -40.0, 6.0), "S6": (9.0, 0.0, 275.0, -5.0, 8.0),
}

MEASURED_CAP = {  # the cap as measured on 2026-11-15
    "S1": (0.0, 15.0, 10.0, -5.0, 0.0), "S2": (-10.7, 7.3, 75.0, -30.0, 0.0),
    "S3": (10.7, 7.3, 290.0, -30.0, 0.0), "S4": (-10.0, 0.0, 90.0, -25.0, 0.0),
    "S5": (0.0, -10.0, 180.0, -5.0, 0.0), "S6": (10.0, 0.0, 270.0, -35.0, 0.0),
}

PLATE = {         # the flat bench plate
    "S1": (-2.5, 0.0, 0.0, 0.0, 2.0), "S2": (0.0, 0.0, 0.0, 0.0, 2.0), "S3": (2.5, 0.0, 0.0, 0.0, 2.0),
    "S4": (-2.5, 0.0, 0.0, 0.0, -2.0), "S5": (0.0, 0.0, 0.0, 0.0, -2.0), "S6": (2.5, 0.0, 0.0, 0.0, -2.0),
}


def cap_rig(rows=PLANNED_CAP):
    """cap_mounts.json settings with a fixed layout (and no learned self-mask)."""
    return with_rows(load_mounts(ROOT / "config" / "cap_mounts.json"), rows)


def flat_rig(rows=PLATE):
    return with_rows(load_mounts(ROOT / "config" / "flat_mounts.json"), rows)

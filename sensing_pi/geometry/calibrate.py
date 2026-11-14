"""Calibration for the cap's sensors: range scale/offset (bench) and mount angles (worn, facing a wall).

Inputs are tof6 frames as produced by tests/multi6/tof6_record.py ({"res", "d", "s"}),
e.g. loaded from its CSV with load_tof6_csv().
"""
from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import numpy as np

from .transform import VALID_STATUS, Mount, Rig, sensor_points


def load_tof6_csv(path: str | Path) -> list[dict]:
    """Read a tof6_record.py CSV into a list of frames."""
    with open(path, newline="") as fh:
        meta = dict(tok.split("=", 1) for tok in fh.readline().lstrip("#").split() if "=" in tok)
        res = int(meta["res"])
        z = res * res
        rd = csv.reader(fh)
        next(rd)
        frames, bad = [], 0
        for row in rd:
            if not row:
                continue
            try:  # a recording cut off by a power loss ends in garbage / NUL bytes
                t = float(row[0])
                vals = [int(v) for v in row[1:]]
            except ValueError:
                bad += 1
                continue
            n = len(vals) // (2 * z)
            if n == 0 or len(vals) != 2 * n * z:
                bad += 1
                continue
            frames.append({"t": t, "res": res,
                           "d": [vals[k * z:(k + 1) * z] for k in range(n)],
                           "s": [vals[(n + k) * z:(n + k + 1) * z] for k in range(n)]})
    if bad:
        print(f"{path}: skipped {bad} malformed row(s)")
    return frames


def zone_medians(frames: list[dict], k: int, min_valid: float = 0.5) -> np.ndarray:
    """Per-zone median raw distance (mm) for sensor k; NaN where fewer than min_valid of frames were valid."""
    d = np.array([f["d"][k] for f in frames], dtype=float)
    s = np.array([f["s"][k] for f in frames])
    good = np.isin(s, VALID_STATUS) & (d > 0)
    d[~good] = np.nan
    med = np.nanmedian(np.where(good.any(axis=0), d, 0), axis=0)
    med[good.mean(axis=0) < min_valid] = np.nan
    return med


def centre_zones(res: int) -> list[int]:
    c = res // 2
    return [r * res + col for r in (c - 1, c) for col in (c - 1, c)]


# ---------------------------------------------------------------- 1. range
def fit_range(frames_by_true_mm: dict[float, list[dict]], k: int) -> tuple[float, float, float]:
    """Sensor k pointed square at a wall at several known distances.
    Returns (range_scale, range_offset_mm, rms_mm) so that true = raw * scale + offset.
    With a single distance only the offset is fitted."""
    true, meas = [], []
    for true_mm, frames in frames_by_true_mm.items():
        med = zone_medians(frames, k)[centre_zones(frames[0]["res"])]
        med = med[~np.isnan(med)]
        if med.size:
            true.append(float(true_mm))
            meas.append(float(np.mean(med)))
    if not true:
        raise ValueError(f"sensor {k}: no valid centre-zone readings")
    true, meas = np.array(true), np.array(meas)
    if len(true) == 1:
        scale, offset = 1.0, float(true[0] - meas[0])
    else:
        scale, offset = np.polyfit(meas, true, 1)
    rms = float(np.sqrt(np.mean((meas * scale + offset - true) ** 2)))
    return float(scale), float(offset), rms


# ---------------------------------------------------------------- 2. mount angles
def _wall_residual(m: Mount, dist_mm, res, rig: Rig, n, D):
    p = sensor_points(m, dist_mm, res, rig.fov_deg, rig.range_model, rig.zone_flip)
    return p @ n - D


def calibrate_mount(rig: Rig, k: int, frames: list[dict], normal, distance_m: float,
                    search_deg: float = 20.0, fit_roll: bool = False) -> tuple[Mount, dict]:
    """Correct sensor k's az/el (and optionally roll) from frames of a flat wall.

    normal: unit vector in the HEAD frame pointing from the head toward the wall
            (facing it: (1,0,0); wall on the left: (0,-1,0); on the right: (0,1,0)).
    distance_m: perpendicular distance from the head centre to the wall.
    x/y/z of the mount are taken as measured and kept fixed.
    Returns (new Mount, report) where report has az/el/roll, rms_m, n_zones and the starting rms.
    """
    n = np.asarray(normal, dtype=float)
    n = n / np.linalg.norm(n)
    m0 = rig.sensors[k]
    res = frames[0]["res"]
    med = zone_medians(frames, k)
    use = ~np.isnan(med)
    if use.sum() < 6:
        raise ValueError(f"{m0.id}: only {int(use.sum())} zones see the wall reliably")

    def cost(m, mask):
        r = _wall_residual(m, np.nan_to_num(med), res, rig, n, distance_m)[mask]
        return float(np.sqrt(np.mean(r ** 2)))

    start_rms = cost(m0, use)
    best = m0
    for _round in range(2):  # second round after dropping outlier zones (floor, clutter)
        span, step = search_deg, 2.0
        while step >= 0.05:
            grid = np.arange(-span, span + 1e-9, step)
            rolls = grid if fit_roll else [0.0]
            cands = [replace(best, az_deg=best.az_deg + da, el_deg=best.el_deg + de, roll_deg=best.roll_deg + dr)
                     for da in grid for de in grid for dr in rolls]
            best = min(cands, key=lambda m: cost(m, use))
            span, step = step * 2, step / 4
        r = _wall_residual(best, np.nan_to_num(med), res, rig, n, distance_m)
        mad = np.median(np.abs(r[use] - np.median(r[use]))) + 1e-3
        use = use & (np.abs(r) < max(4 * mad, 0.03))
        search_deg = 3.0

    best = replace(best, az_deg=round(float(best.az_deg), 2), el_deg=round(float(best.el_deg), 2),
                   roll_deg=round(float(best.roll_deg), 2))
    report = {"id": best.id, "az_deg": best.az_deg, "el_deg": best.el_deg,
              "roll_deg": best.roll_deg, "rms_m": round(cost(best, use), 4),
              "start_rms_m": round(start_rms, 4), "n_zones": int(use.sum())}
    return best, report


def apply(rig: Rig, k: int, mount: Mount) -> Rig:
    """Return a copy of rig with sensor k replaced."""
    sensors = list(rig.sensors)
    sensors[k] = mount
    return replace(rig, sensors=sensors)

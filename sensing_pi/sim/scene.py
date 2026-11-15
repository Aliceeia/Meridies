"""Ray-cast simple scenes through any rig into tof6 frames ({"t","res","d","s"}).

Used by the tests, and by `main.py --sim <scene>` / `objects.run --source sim:<scene>` as a hardware-free mock.
Shapes are in the head frame (x forward, y right, z up, metres).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from geometry.transform import Rig, zone_rays

NO_TARGET = 255


@dataclass
class Plane:
    point: tuple            # any point on the plane
    normal: tuple           # need not be unit; sign doesn't matter

    def hit(self, o, u):
        n = np.asarray(self.normal, float)
        n = n / np.linalg.norm(n)
        den = u @ n
        with np.errstate(divide="ignore", invalid="ignore"):
            t = ((np.asarray(self.point) - o) @ n) / den
        t[np.abs(den) < 1e-9] = np.inf
        return t


@dataclass
class Pole:
    """Vertical cylinder at (x, y) with radius r, from z_min to z_max."""
    x: float
    y: float
    r: float = 0.03
    z_min: float = -2.0
    z_max: float = 2.0

    def hit(self, o, u):
        dx, dy = o[0] - self.x, o[1] - self.y
        a = u[:, 0] ** 2 + u[:, 1] ** 2
        b = 2 * (dx * u[:, 0] + dy * u[:, 1])
        c = dx * dx + dy * dy - self.r ** 2
        disc = b * b - 4 * a * c
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (-b - np.sqrt(np.maximum(disc, 0))) / (2 * a)
        t[(disc < 0) | (a < 1e-12)] = np.inf
        z = o[2] + t * u[:, 2]
        t[(z < self.z_min) | (z > self.z_max)] = np.inf
        return t


def render(rig: Rig, shapes, res: int = 8, t: float = 0.0, max_range_m: float = 4.0,
           noise_mm: float = 0.0, rng=None) -> dict:
    """One tof6 frame of `shapes` as seen by every sensor in `rig`."""
    rng = rng or np.random.default_rng(0)
    d_all, s_all, sig_all, sps_all = [], [], [], []
    for m in rig.sensors:
        rays = zone_rays(m, res, rig.fov_deg, rig.zone_flip)
        norm = np.linalg.norm(rays, axis=1)
        u = rays / norm[:, None]
        o = m.origin
        best = np.full(len(u), np.inf)
        for sh in shapes:
            ts = sh.hit(o, u)
            ts[ts <= 0.01] = np.inf
            best = np.minimum(best, ts)
        seen = best <= max_range_m
        meas = best if rig.range_model == "radial" else best / norm
        mm = meas * 1000 + (rng.normal(0, noise_mm, meas.shape) if noise_mm else 0)
        raw = (mm - m.range_offset_mm) / m.range_scale
        d_all.append([int(round(v)) if ok else 0 for v, ok in zip(raw, seen)])
        s_all.append([5 if ok else NO_TARGET for ok in seen])
        # roughly what the real sensors report (2026-11-15 sample): sigma ~1% of range, signal ~ 1/d^2
        sig_all.append([int(2 + 0.01 * v) if ok else 0 for v, ok in zip(mm, seen)])
        sps_all.append([int(5e7 / max(v, 50) ** 2) if ok else 0 for v, ok in zip(mm, seen)])
    return {"t": round(t, 4), "res": res, "d": d_all, "s": s_all, "sigma": sig_all, "signal": sps_all}


# ---------------------------------------------------------------- named moving scenes
def scene_shapes(name: str, t: float):
    """Shapes at time t (s) for a named demo scene."""
    if name == "wall":                     # flat wall 1.2 m ahead
        return [Plane((1.2, 0, 0), (1, 0, 0))]
    if name == "pole_sweep":               # pole 1 m away sweeping -40..+40 deg, period 8 s
        az = math.radians(40 * math.sin(2 * math.pi * t / 8))
        return [Pole(math.cos(az), math.sin(az))]
    if name == "approach":                 # pole walking toward you at 1 m/s from 3 m, repeats every 3 s
        x = 3.0 - (t % 3.0)
        return [Pole(max(x, 0.25), 0.1)]
    if name == "pole_wall":                # pole 0.6 m in front of a wall at 1.8 m
        return [Plane((1.8, 0, 0), (1, 0, 0)), Pole(1.2, -0.1)]
    if name == "corridor":                 # walls 0.6 m left and right, end wall 3 m ahead
        return [Plane((0, -0.6, 0), (0, 1, 0)), Plane((0, 0.6, 0), (0, 1, 0)), Plane((3.0, 0, 0), (1, 0, 0))]
    raise ValueError(f"unknown scene {name!r}; try wall, pole_sweep, approach, pole_wall, corridor")


SCENES = ("wall", "pole_sweep", "approach", "pole_wall", "corridor")

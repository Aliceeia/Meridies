"""Zone-level cleanup before any geometry: drop readings the sensor itself flags as unreliable,
and readings that a learned self-mask says are the cap / body / cables.

Both work by marking a zone invalid (status 0) in a copy of the frame, so everything downstream
is unchanged. Frames without sigma/signal (old recordings, old streams) skip those two checks.

Settings: "quality" in config/objects.json. Self-mask file: config/selfmask_<rig json name>.json,
made with tools/learn_selfmask.py while the cap is worn in open space.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

INVALID = 0   # VL53L5CX "range not updated": treated as invalid everywhere


def mask_path_for(rig) -> Path | None:
    """config/selfmask_<stem>.json next to the rig json the rig was loaded from."""
    src = rig.extra.get("_source")
    if not src:
        return None
    p = Path(src)
    return p.with_name(f"selfmask_{p.stem}.json")


def load_mask(path: Path, rig) -> dict | None:
    """{sensor index: np.array(zones) of mm thresholds (0 = no mask)} or None."""
    if path is None or not path.exists():
        return None
    raw = json.loads(path.read_text())
    ids = [m.id for m in rig.sensors]
    out = {}
    for sid, zones in raw.get("zones", {}).items():
        if sid in ids:
            out[ids.index(sid)] = np.asarray(zones, dtype=float)
    return out


def learn_mask(frames: list[dict], ids: list[str], near_mm: float = 600.0, min_frac: float = 0.2,
               margin_mm: float = 30.0) -> tuple[dict, dict]:
    """Learn the self-mask from frames recorded with the cap WORN IN OPEN SPACE (nothing real
    within near_mm). A zone that reads closer than near_mm in at least min_frac of the frames is
    seeing the cap / body / cables: its threshold becomes the 98th percentile of those near
    readings + margin (never more than near_mm). Readings beyond the threshold still count, so a
    real object further out in that zone is not lost.
    Returns (zones {id: [mm per zone, 0 = unmasked]}, stats {id: (masked zones, total zones)})."""
    n = len(frames)
    if n == 0:
        raise ValueError("no frames")
    zones, stats = {}, {}
    for k, sid in enumerate(ids):
        d = np.array([f["d"][k] for f in frames], dtype=float)
        s = np.array([f["s"][k] for f in frames])
        valid = np.isin(s, (5, 9)) & (d > 0)
        near = valid & (d < near_mm)
        frac = near.sum(axis=0) / n
        thr = np.zeros(d.shape[1])
        for z in np.flatnonzero(frac >= min_frac):
            thr[z] = min(near_mm, float(np.percentile(d[near[:, z], z], 98)) + margin_mm)
        zones[sid] = [int(round(v)) for v in thr]
        stats[sid] = (int((thr > 0).sum()), d.shape[1])
    return zones, stats


class QualityFilter:
    def __init__(self, cfg: dict, rig=None, mask: dict | None = None, mask_file: Path | None = None):
        q = cfg.get("quality", {})
        self.enabled = q.get("enabled", True)
        self.max_sigma_mm = q.get("max_sigma_mm", 15.0)
        self.max_sigma_rel = q.get("max_sigma_rel", 0.03)
        self.min_signal = q.get("min_signal", 3.0)
        self.mask_file = None
        if mask is None and rig is not None and q.get("self_mask", True):
            self.mask_file = mask_file or mask_path_for(rig)
            mask = load_mask(self.mask_file, rig)
        self.mask = mask or {}
        self.counts = {"sigma": 0, "signal": 0, "mask": 0, "zones": 0}

    def describe(self) -> str:
        if not self.enabled:
            return "quality filter: OFF"
        m = (f"self-mask {self.mask_file.name if self.mask_file else '(given)'} ({len(self.mask)} sensors)"
             if self.mask else "no self-mask")
        return (f"quality filter: sigma <= max({self.max_sigma_mm:g} mm, {self.max_sigma_rel:g} x dist), "
                f"signal >= {self.min_signal:g}, {m}")

    def apply(self, frame: dict) -> dict:
        if not self.enabled:
            return frame
        has_q = "sigma" in frame and "signal" in frame
        new_s = []
        for k, (d, s) in enumerate(zip(frame["d"], frame["s"])):
            d = np.asarray(d, dtype=float)
            s = np.array(s)
            valid = np.isin(s, (5, 9)) & (d > 0)
            self.counts["zones"] += int(valid.sum())
            bad = np.zeros_like(valid)
            if has_q:
                sig = np.asarray(frame["sigma"][k], dtype=float)
                sps = np.asarray(frame["signal"][k], dtype=float)
                b_sig = valid & (sig > np.maximum(self.max_sigma_mm, self.max_sigma_rel * d))
                b_sps = valid & ~b_sig & (sps < self.min_signal)
                self.counts["sigma"] += int(b_sig.sum())
                self.counts["signal"] += int(b_sps.sum())
                bad |= b_sig | b_sps
            m = self.mask.get(k)
            if m is not None and len(m) == len(d):
                b_mask = valid & ~bad & (m > 0) & (d <= m)
                self.counts["mask"] += int(b_mask.sum())
                bad |= b_mask
            s[bad] = INVALID
            new_s.append(s.tolist())
        out = dict(frame)
        out["s"] = new_s
        return out

    def summary(self) -> str:
        n = max(self.counts["zones"], 1)
        c = self.counts
        return (f"quality filter removed {100 * (c['sigma'] + c['signal'] + c['mask']) / n:.1f}% of valid zones "
                f"(sigma {c['sigma']}, signal {c['signal']}, self-mask {c['mask']} of {c['zones']})")

"""Body zone: ignore points that fall on the wearer's own shoulders, torso and arms.

The body is described as boxes in the HEAD frame (origin = head centre at ear height,
x forward, y right, z up, metres). A box with "mirror_y": true also applies with y negated,
so one entry covers both arms. Settings: "body_zone" in config/objects.json (off by default),
switched on for the cap in config/cap_mounts.json -> "objects".

Limits: the boxes move with the head, so when the head turns far from the torso the zone
slides off the shoulders. Once the MPU6050 gives head yaw relative to the body, the boxes
can be placed in the body frame instead.
"""
from __future__ import annotations

import numpy as np


class BodyZone:
    def __init__(self, cfg: dict):
        bz = cfg.get("body_zone") or {}
        self.enabled = bool(bz.get("enabled", False))
        self.boxes = []
        for b in bz.get("boxes", []):
            lo = np.array([b["x"][0], b["y"][0], b["z"][0]], dtype=float)
            hi = np.array([b["x"][1], b["y"][1], b["z"][1]], dtype=float)
            self.boxes.append((b.get("name", "box"), lo, hi))
            if b.get("mirror_y"):
                self.boxes.append((b.get("name", "box") + " (mirror)",
                                   np.array([lo[0], -hi[1], lo[2]]), np.array([hi[0], -lo[1], hi[2]])))
        self.removed = 0

    def inside(self, xyz: np.ndarray) -> np.ndarray:
        """Bool per point: inside any body box."""
        xyz = np.asarray(xyz, dtype=float).reshape(-1, 3)
        hit = np.zeros(len(xyz), dtype=bool)
        if not self.enabled:
            return hit
        for _, lo, hi in self.boxes:
            hit |= np.all((xyz >= lo) & (xyz <= hi), axis=1)
        return hit

    def apply(self, cloud) -> None:
        """Mark cloud points on the body as invalid (in place)."""
        if not self.enabled:
            return
        hit = cloud.valid & self.inside(cloud.xyz)
        self.removed += int(hit.sum())
        cloud.valid &= ~hit

    def describe(self) -> str:
        if not self.enabled:
            return "body zone: off"
        names = sorted({n.replace(" (mirror)", "") for n, _, _ in self.boxes})
        return f"body zone: {len(self.boxes)} boxes ({', '.join(names)})"

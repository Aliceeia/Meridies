"""Object layer: tof6 frame -> points -> scan -> objects -> tracked, ranked warning list.

    pipe = ObjectPipeline(rig, load_cfg(rig=rig))
    objs, warnings = pipe.step(frame)     # warnings = list for audio/engine.py (port 5556)

See docs/objects_pipeline.md.
"""
from __future__ import annotations

from geometry.orientation import LevelHead, OrientationSource
from geometry.transform import Rig, frame_to_points

from .cluster import cluster
from .config import load_cfg
from .describe import Obj, describe, split_corners
from .localmap import LocalMap
from .rank import REQUIRED, rank
from .scan import ScanPoint, bin_scan, cloud_to_points, reproject
from .track import Tracker


class ObjectPipeline:
    def __init__(self, rig: Rig, cfg: dict | None = None, orientation: OrientationSource | None = None):
        self.rig = rig
        self.cfg = cfg or load_cfg(rig=rig)
        self.orientation = orientation or LevelHead()
        self.has_imu = not isinstance(self.orientation, LevelHead)
        window = self.cfg["map_window_imu_s"] if self.has_imu else self.cfg["map_window_s"]
        self.map = LocalMap(window)
        self.tracker = Tracker(self.cfg)
        self.last_scan: list[ScanPoint] = []

    def step(self, frame: dict) -> tuple[list[Obj], list[dict]]:
        t = float(frame["t"])
        o = self.orientation.read()
        cloud = frame_to_points(frame, self.rig, orientation=o)
        self.map.add(t, cloud_to_points(cloud, t, self.cfg))
        pts = self.map.points()
        reproject(pts, o if self.has_imu else None)
        scan = bin_scan(pts, self.cfg["bin_deg"])
        self.last_scan = scan
        groups = [piece for g in cluster(scan, self.cfg) for piece in split_corners(g, self.cfg)]
        objs = [describe(g, self.cfg) for g in groups]
        self.tracker.update(objs, t)
        warnings = rank(objs, self.cfg)
        return objs, warnings


__all__ = ["ObjectPipeline", "Obj", "ScanPoint", "load_cfg", "REQUIRED"]

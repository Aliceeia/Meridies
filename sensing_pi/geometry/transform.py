"""Sensor-zone -> head-frame coordinate transform for the cap's VL53L5CX sensors.

Frames and angles (shared with sectors.json and audio/hrtf_render.py):
    head frame: origin = head centre (between the ear canals), x forward, y right, z up, metres
    az (horizontal angle): 0 = straight ahead, + right, - left, degrees
    el (vertical angle):   0 = level, + up, - down, degrees
    roll: + = the body's right side tips down (clockwise as seen from behind), degrees

A mount is "where the sensor points" (az, el, roll) plus "where it sits" (x, y, z).
The same basis() is used for sensor -> head and, later, head -> world (orientation.py).
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

VALID_STATUS = (5, 9)  # VL53L5CX target_status: valid, valid with large pulse


# ---------------------------------------------------------------- config
@dataclass
class Mount:
    id: str
    az_deg: float
    el_deg: float
    roll_deg: float = 0.0
    x_m: float = 0.0
    y_m: float = 0.0
    z_m: float = 0.0
    flip_h: bool = False          # mirror zone columns (set from the hand-sweep test)
    flip_v: bool = False          # mirror zone rows
    range_scale: float = 1.0      # corrected_mm = raw_mm * range_scale + range_offset_mm
    range_offset_mm: float = 0.0

    @property
    def origin(self) -> np.ndarray:
        return np.array([self.x_m, self.y_m, self.z_m])


@dataclass
class Rig:
    sensors: list[Mount]
    fov_deg: float = 45.0             # full FoV of the 8x8 / 4x4 grid, both axes
    range_model: str = "radial"       # "radial": distance along each zone's ray; "perpendicular": along the sensor axis
    min_range_m: float = 0.12         # drop points closer than this to the head centre (brim / self hits)
    floor_z_m: float | None = -1.55   # drop points below this height (head-level assumption until the IMU is in)
    # How the VL53L5CX's own zone order maps onto the scene, for a board mounted upright (roll 0).
    # Same for every board of one module type; set once from the hand test (docs/objects_pipeline.md).
    zone_flip_h: bool = False
    zone_flip_v: bool = False
    extra: dict = field(default_factory=dict)

    def index(self, sensor_id: str) -> int:
        return [m.id for m in self.sensors].index(sensor_id)

    @property
    def zone_flip(self) -> tuple[bool, bool]:
        return (self.zone_flip_h, self.zone_flip_v)


_RIG_KEYS = ("fov_deg", "range_model", "min_range_m", "floor_z_m", "zone_flip_h", "zone_flip_v")


def load_mounts(path: str | Path) -> Rig:
    """Read a rig json. If it has "layout": "<NAME>", each sensor's position and angles come from
    LAYOUTS[NAME] in the layout file (default config/sensor_layout.py, next to the json);
    the json then only holds the advanced settings (roll, flips, range calibration)."""
    path = Path(path)
    raw = json.loads(path.read_text())
    names = {f.name for f in fields(Mount)}
    entries = [dict(s) for s in raw["sensors"]]

    if "layout" in raw:
        from .layout import GEOMETRY_FIELDS, load_layout
        layout_file = path.parent / raw.get("layout_file", "sensor_layout.py")
        geo = load_layout(layout_file, raw["layout"])
        ids = [s["id"] for s in entries]
        if sorted(geo) != sorted(ids):
            raise ValueError(f"{layout_file} {raw['layout']} has sensors {sorted(geo)}, {path.name} has {ids}")
        for s in entries:
            dup = [k for k in GEOMETRY_FIELDS if k in s]
            if dup:
                raise ValueError(f"{path.name} {s['id']}: {dup} are set here AND in {layout_file.name}; "
                                 f"keep positions/angles only in {layout_file.name}")
            s.update(geo[s["id"]])

    sensors = [Mount(**{k: v for k, v in s.items() if k in names}) for s in entries]
    extra = {k: v for k, v in raw.items() if k not in _RIG_KEYS + ("sensors",)}
    extra["_source"] = str(path.resolve())   # lets the pipeline find config/selfmask_<stem>.json
    return Rig(sensors=sensors, extra=extra, **{k: raw[k] for k in _RIG_KEYS if k in raw})


def save_mounts(rig: Rig, path: str | Path) -> None:
    """Write a rig json. For a layout-based rig the positions/angles are NOT written (they live in
    the layout file); print them with geometry.layout.format_row and paste them there instead."""
    from .layout import GEOMETRY_FIELDS
    out = {k: v for k, v in rig.extra.items() if k != "_source"}
    out.update({k: getattr(rig, k) for k in _RIG_KEYS})
    skip = set(GEOMETRY_FIELDS) if "layout" in rig.extra else set()
    out["sensors"] = [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(m).items() if k not in skip}
                      for m in rig.sensors]
    Path(path).write_text(json.dumps(out, indent=2) + "\n")


# ---------------------------------------------------------------- angles <-> vectors
def direction(az_deg, el_deg) -> np.ndarray:
    """Unit vector(s) for az/el. Works on scalars or arrays (last axis = xyz)."""
    a, e = np.radians(az_deg), np.radians(el_deg)
    return np.stack([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)], axis=-1)


def to_angles(xyz) -> tuple[np.ndarray, np.ndarray]:
    """(az_deg, el_deg) of vector(s) seen from the origin."""
    xyz = np.asarray(xyz, dtype=float)
    az = np.degrees(np.arctan2(xyz[..., 1], xyz[..., 0]))
    el = np.degrees(np.arctan2(xyz[..., 2], np.hypot(xyz[..., 0], xyz[..., 1])))
    return az, el


def basis(az_deg: float, el_deg: float, roll_deg: float = 0.0) -> np.ndarray:
    """3x3 matrix whose columns are the body's forward, right, up axes in the parent frame.
    parent_vector = basis @ (forward, right, up) local components."""
    a, e, r = (math.radians(v) for v in (az_deg, el_deg, roll_deg))
    f = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    r0 = np.array([-math.sin(a), math.cos(a), 0.0])
    u0 = np.array([-math.sin(e) * math.cos(a), -math.sin(e) * math.sin(a), math.cos(e)])
    right = math.cos(r) * r0 - math.sin(r) * u0
    up = math.sin(r) * r0 + math.cos(r) * u0
    return np.column_stack([f, right, up])


# ---------------------------------------------------------------- zones
def zone_angles(res: int, fov_deg: float, flip_h: bool = False, flip_v: bool = False):
    """Per-zone (h, v) angles in degrees in the sensor's own frame (h + right, v + up).
    Zone i = row * res + col; row 0 is treated as the top, col 0 as the left, before flips."""
    step = fov_deg / res
    idx = np.arange(res * res)
    row, col = idx // res, idx % res
    h = (col - (res - 1) / 2) * step
    v = ((res - 1) / 2 - row) * step
    return (-h if flip_h else h), (-v if flip_v else v)


def zone_rays(m: Mount, res: int, fov_deg: float, zone_flip: tuple[bool, bool] = (False, False)) -> np.ndarray:
    """(res*res, 3) ray directions in the head frame, scaled so the component along the sensor axis is 1.
    zone_flip = the rig's (zone_flip_h, zone_flip_v); combined with the mount's own flips."""
    h, v = zone_angles(res, fov_deg, m.flip_h != zone_flip[0], m.flip_v != zone_flip[1])
    local = np.column_stack([np.ones_like(h), np.tan(np.radians(h)), np.tan(np.radians(v))])
    return local @ basis(m.az_deg, m.el_deg, m.roll_deg).T


def corrected_range_m(m: Mount, dist_mm) -> np.ndarray:
    return (np.asarray(dist_mm, dtype=float) * m.range_scale + m.range_offset_mm) / 1000.0


def sensor_points(m: Mount, dist_mm, res: int, fov_deg: float = 45.0, range_model: str = "radial",
                  zone_flip: tuple[bool, bool] = (False, False)) -> np.ndarray:
    """Head-frame xyz (res*res, 3) for one sensor's zone distances (raw mm, sensor zone order)."""
    rays = zone_rays(m, res, fov_deg, zone_flip)
    d = corrected_range_m(m, dist_mm)[:, None]
    if range_model == "radial":
        rays = rays / np.linalg.norm(rays, axis=1, keepdims=True)
    elif range_model != "perpendicular":
        raise ValueError(f"unknown range_model {range_model!r}")
    return m.origin + d * rays


# ---------------------------------------------------------------- whole frame
@dataclass
class PointCloud:
    xyz: np.ndarray        # (N, 3) head frame, metres
    sensor: np.ndarray     # (N,) sensor index into rig.sensors
    zone: np.ndarray       # (N,) zone index in that sensor
    valid: np.ndarray      # (N,) bool: good status and not filtered out
    world: np.ndarray | None = None  # (N, 3) gravity-aligned frame, when an orientation was given

    def polar(self):
        """(az_deg, el_deg, range_m) of every point, from the head centre."""
        az, el = to_angles(self.xyz)
        return az, el, np.linalg.norm(self.xyz, axis=1)

    def only_valid(self) -> "PointCloud":
        k = self.valid
        return PointCloud(self.xyz[k], self.sensor[k], self.zone[k], self.valid[k],
                          None if self.world is None else self.world[k])


def frame_to_points(frame: dict, rig: Rig, orientation=None) -> PointCloud:
    """Convert a tof6 frame {"res", "d": [6 x zones], "s": [6 x zones]} into a head-frame point cloud.

    orientation: optional geometry.orientation.HeadOrientation. When given, points are also rotated
    into the gravity-aligned world frame and the floor filter uses true height. Without it the head
    is assumed level (fine for bench tests; the MPU6050 slot fills this in later).
    """
    res = frame["res"]
    xyz, sid, zid, ok = [], [], [], []
    for k, m in enumerate(rig.sensors):
        d = np.asarray(frame["d"][k], dtype=float)
        s = np.asarray(frame["s"][k])
        xyz.append(sensor_points(m, d, res, rig.fov_deg, rig.range_model, rig.zone_flip))
        sid.append(np.full(res * res, k))
        zid.append(np.arange(res * res))
        ok.append(np.isin(s, VALID_STATUS) & (d > 0))
    xyz = np.concatenate(xyz)
    cloud = PointCloud(xyz, np.concatenate(sid), np.concatenate(zid), np.concatenate(ok))

    height = xyz[:, 2]
    if orientation is not None:
        from .orientation import head_to_world
        cloud.world = head_to_world(xyz, orientation)
        height = cloud.world[:, 2]
    cloud.valid &= np.linalg.norm(xyz, axis=1) >= rig.min_range_m
    if rig.floor_z_m is not None:
        cloud.valid &= height > rig.floor_z_m + 0.10
    return cloud

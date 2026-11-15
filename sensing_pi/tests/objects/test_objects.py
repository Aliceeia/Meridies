"""Synthetic checks for the object pipeline on both rigs. Run from sensing_pi/:
    .venv/bin/python tests/objects/test_objects.py
(also works under pytest)."""
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from geometry import load_mounts  # noqa: E402
from geometry.transform import to_angles, zone_rays  # noqa: E402
from objects import REQUIRED, ObjectPipeline  # noqa: E402
from sim.scene import Plane, Pole, render, scene_shapes  # noqa: E402

from geometry.layout import with_rows  # noqa: E402

# Fixed test layouts, so the tests don't change when config/sensor_layout.py is edited.
PLANNED_CAP = {
    "S1": (-8.0, 15.0, 40.0, -10.0, 7.0), "S2": (0.0, 17.0, 0.0, -10.0, 7.0),
    "S3": (8.0, 15.0, 320.0, -10.0, 7.0), "S4": (-9.0, 0.0, 85.0, -5.0, 8.0),
    "S5": (0.0, 14.0, 0.0, -40.0, 6.0), "S6": (9.0, 0.0, 275.0, -5.0, 8.0),
}
PLATE = {
    "S1": (-2.5, 0.0, 0.0, 0.0, 2.0), "S2": (0.0, 0.0, 0.0, 0.0, 2.0), "S3": (2.5, 0.0, 0.0, 0.0, 2.0),
    "S4": (-2.5, 0.0, 0.0, 0.0, -2.0), "S5": (0.0, 0.0, 0.0, 0.0, -2.0), "S6": (2.5, 0.0, 0.0, 0.0, -2.0),
}
FLAT = with_rows(load_mounts(ROOT / "config" / "flat_mounts.json"), PLATE)
CAP = with_rows(load_mounts(ROOT / "config" / "cap_mounts.json"), PLANNED_CAP)


def test_real_cap_layout_runs():
    """The hand-edited cap layout loads and the pipeline runs on it without errors."""
    real = load_mounts(ROOT / "config" / "cap_mounts.json")
    for _, w in run(real, lambda t: scene_shapes("corridor", t), n=4):
        check_schema(w)
HZ = 10


def run(rig, shapes_at, n=6, noise_mm=8):
    """Feed n frames; shapes_at(t) -> shapes. Returns per-frame (objs, warnings)."""
    pipe = ObjectPipeline(rig)
    rng = np.random.default_rng(1)
    out = []
    for i in range(n):
        t = i / HZ
        out.append(pipe.step(render(rig, shapes_at(t), t=t, noise_mm=noise_mm, rng=rng)))
    return out


def check_schema(warnings):
    assert isinstance(warnings, list) and len(warnings) <= 3
    json.dumps(warnings)
    dists = [w["dist"] for w in warnings]
    assert dists == sorted(dists), "warnings must be nearest first"
    for w in warnings:
        for k in REQUIRED:
            assert w.get(k) is not None, f"missing {k}: {w}"
        assert isinstance(w["az"], float) and isinstance(w["dist"], float)
    assert len({w["id"] for w in warnings}) == len(warnings)


def test_pole_is_one_narrow_object():
    for rig in (FLAT, CAP):
        az = 10
        pole = Pole(math.cos(math.radians(az)), math.sin(math.radians(az)))
        objs, w = run(rig, lambda t: [pole])[-1]
        assert len(objs) == 1, [o.to_dict() for o in objs]
        o = objs[0]
        assert o.kind == "narrow" and o.width_m < 0.35, o.to_dict()
        assert abs(o.az - az) < 3, o.to_dict()
        assert w and w[0]["id"] == o.id and w[0]["level"] == "warn"
        check_schema(w)


def test_facing_wall():
    for rig in (FLAT, CAP):
        objs, w = run(rig, lambda t: [Plane((1.2, 0, 0), (1, 0, 0))])[-1]
        walls = [o for o in objs if o.kind == "wall_facing"]
        assert walls, [o.to_dict() for o in objs]
        assert abs(walls[0].dist - 1.2) < 0.03, walls[0].to_dict()
        assert any(x["kind"] == "wall_facing" and x["level"] == "warn" for x in w)
        check_schema(w)


def test_parallel_wall_quiet_until_close():
    objs, w = run(CAP, lambda t: [Plane((0, 0.6, 0), (0, 1, 0))])[-1]
    par = [o for o in objs if o.kind == "wall_parallel"]
    assert par and abs(par[0].dist - 0.6) < 0.05, [o.to_dict() for o in objs]
    assert not w, w
    objs, w = run(CAP, lambda t: [Plane((0, 0.4, 0), (0, 1, 0))])[-1]
    assert [x["level"] for x in w] == ["wall"], w
    assert w[0]["az"] > 60, w


def test_pole_in_front_of_wall_is_separate():
    for rig in (FLAT, CAP):
        objs, w = run(rig, lambda t: scene_shapes("pole_wall", t))[-1]
        poles = [o for o in objs if o.kind == "narrow" and abs(o.dist - 1.17) < 0.1]
        walls = [o for o in objs if abs(o.dist - 1.8) < 0.1]
        assert poles and walls, [o.to_dict() for o in objs]
        assert w[0]["id"] == poles[0].id, w          # pole ranks first
        check_schema(w)


def test_approach_tracks_id_and_closing_speed():
    # The simulator casts one ray per zone, so a thin pole only shows up once rays are
    # closer together than its diameter (~2 m here); real zones integrate an area.
    frames = run(FLAT, lambda t: [Pole(3.0 - t, 0.05)], n=26)
    frames = [(objs, w) for objs, w in frames if objs]
    assert len(frames) >= 12
    ids = {o.id for objs, _ in frames for o in objs}
    assert len(ids) == 1, ids
    speeds = [objs[0].closing_mps for objs, _ in frames[3:]]
    assert 0.85 < np.median(speeds) < 1.15, speeds
    levels = [w[0]["level"] if w else None for _, w in frames]
    first_warn, first_urgent = levels.index("warn"), levels.index("urgent")
    assert first_warn < first_urgent, levels
    d_warn = frames[first_warn][0][0].dist
    d_urg = frames[first_urgent][0][0].dist
    assert 1.6 < d_warn < 2.4 and 0.6 < d_urg < 1.0, (d_warn, d_urg)


def test_sweeping_pole_keeps_id_and_stays_narrow():
    frames = run(CAP, lambda t: scene_shapes("pole_sweep", t), n=40)
    seen = [(i, objs) for i, (objs, _) in enumerate(frames) if objs]
    assert len(seen) >= 36
    ids = {o.id for _, objs in seen for o in objs}
    assert len(ids) == 1, ids
    assert all(objs[0].width_m < 0.35 for _, objs in seen), [round(o[0].width_m, 2) for _, o in seen]
    lag = [abs(objs[0].az - 40 * math.sin(2 * math.pi * (i / HZ) / 8)) for i, objs in seen]
    assert np.median(lag) < 6, [round(x) for x in lag]


def test_flat_bottom_row_is_rotated():
    """Bottom row (roll 180): its zone 0 looks down-right, the top row's zone 0 looks up-left."""
    s1, s4 = FLAT.sensors[0], FLAT.sensors[3]
    az1, el1 = to_angles(zone_rays(s1, 8, FLAT.fov_deg)[0])
    az4, el4 = to_angles(zone_rays(s4, 8, FLAT.fov_deg)[0])
    assert az1 < -15 and el1 > 15 and az4 > 15 and el4 < -15
    # and both rows put a real target at the same place
    pole = Pole(1.0, 0.15, r=0.03, z_min=-0.1, z_max=0.1)
    from geometry import frame_to_points
    cloud = frame_to_points(render(FLAT, [pole]), FLAT).only_valid()
    top = cloud.xyz[cloud.sensor < 3]
    bot = cloud.xyz[cloud.sensor >= 3]
    assert len(top) and len(bot)
    assert abs(np.degrees(np.arctan2(top[:, 1], top[:, 0])).mean()
               - np.degrees(np.arctan2(bot[:, 1], bot[:, 0])).mean()) < 45 / 8


def test_rig_zone_flip_applies_to_every_board():
    from dataclasses import replace
    rig = replace(FLAT, zone_flip_v=True)
    _, el1 = to_angles(zone_rays(rig.sensors[0], 8, rig.fov_deg, rig.zone_flip)[0])
    _, el4 = to_angles(zone_rays(rig.sensors[3], 8, rig.fov_deg, rig.zone_flip)[0])
    assert el1 < -15 and el4 > 15          # both rows invert together; the 180 deg between rows is kept
    objs, _ = run(rig, lambda t: [Plane((1.2, 0, 0), (1, 0, 0))])[-1]   # sim + transform stay consistent
    assert any(o.kind == "wall_facing" and abs(o.dist - 1.2) < 0.03 for o in objs)


def test_schema_all_scenes():
    for rig in (FLAT, CAP):
        for sc in ("wall", "pole_sweep", "approach", "pole_wall", "corridor"):
            for _, w in run(rig, lambda t, sc=sc: scene_shapes(sc, t), n=8):
                check_schema(w)


def test_empty_scene_is_silent():
    objs, w = run(CAP, lambda t: [])[-1]
    assert objs == [] and w == []


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")

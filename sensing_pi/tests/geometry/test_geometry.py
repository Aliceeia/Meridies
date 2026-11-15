"""Synthetic checks for sensing_pi/geometry. Run from sensing_pi/:
    .venv/bin/python tests/geometry/test_geometry.py
(also works under pytest)."""
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from geometry import (HeadOrientation, MPU6050Source, Rig, apply, basis, calibrate_mount,  # noqa: E402
                      direction, fit_range, frame_to_points, head_to_world, load_mounts, to_angles,
                      zone_angles)
from geometry.transform import zone_rays  # noqa: E402

RES = 8


def simulate_wall(m, rig, normal, D, n_frames=5, noise_mm=0.0, seed=0):
    """Frames where every sensor sees a flat wall (only sensor m.id gets real data)."""
    rng = np.random.default_rng(seed)
    n = np.asarray(normal, float)
    rays = zone_rays(m, RES, rig.fov_deg, rig.zone_flip)
    u = rays / np.linalg.norm(rays, axis=1, keepdims=True)
    t = (D - m.origin @ n) / (u @ n)                         # radial distance to the plane, m
    if rig.range_model == "perpendicular":
        t = t / np.linalg.norm(rays, axis=1)
    hit = (u @ n) > 0.05
    k = rig.index(m.id)
    frames = []
    for _ in range(n_frames):
        true_mm = t * 1000 + rng.normal(0, noise_mm, t.shape)
        raw = (true_mm - m.range_offset_mm) / m.range_scale
        d = [[0] * RES * RES for _ in rig.sensors]
        s = [[255] * RES * RES for _ in rig.sensors]
        d[k] = [int(round(v)) if h else 0 for v, h in zip(raw, hit)]
        s[k] = [5 if h else 255 for h in hit]
        frames.append({"t": 0.0, "res": RES, "d": d, "s": s})
    return frames


# Fixed test layout (the planned cap from docs/cap_layout.md), so the tests don't change
# when the real measurements in config/sensor_layout.py are edited.
PLANNED_CAP = {
    "S1": (-8.0, 15.0, 40.0, -10.0, 7.0), "S2": (0.0, 17.0, 0.0, -10.0, 7.0),
    "S3": (8.0, 15.0, 320.0, -10.0, 7.0), "S4": (-9.0, 0.0, 85.0, -5.0, 8.0),
    "S5": (0.0, 14.0, 0.0, -40.0, 6.0), "S6": (9.0, 0.0, 275.0, -5.0, 8.0),
}


def rig():
    from geometry.layout import with_rows
    return with_rows(load_mounts(ROOT / "config" / "cap_mounts.json"), PLANNED_CAP)


def test_direction_roundtrip():
    for az, el in [(0, 0), (30, -10), (-85, 5), (120, 40)]:
        a, e = to_angles(direction(az, el))
        assert abs(a - az) < 1e-9 and abs(e - el) < 1e-9


def test_basis_orthonormal_and_roll_sign():
    M = basis(25, -15, 30)
    assert np.allclose(M.T @ M, np.eye(3))
    R = basis(0, 0, 90)                  # roll +90: right axis points down, up axis points right
    assert np.allclose(R[:, 1], [0, 0, -1]) and np.allclose(R[:, 2], [0, 1, 0])


def test_zone_layout():
    h, v = zone_angles(8, 45)
    assert math.isclose(h[0], -19.6875) and math.isclose(v[0], 19.6875)     # zone 0 = top-left
    assert math.isclose(h[63], 19.6875) and math.isclose(v[63], -19.6875)
    hf, vf = zone_angles(8, 45, flip_h=True, flip_v=True)
    assert math.isclose(hf[0], 19.6875) and math.isclose(vf[0], -19.6875)


def test_points_land_on_wall():
    r = rig()
    m = r.sensors[1]                      # S2, front brim
    frames = simulate_wall(m, r, (1, 0, 0), 1.5)
    cloud = frame_to_points(frames[0], replace(r, floor_z_m=None)).only_valid()
    cloud = cloud.__class__(cloud.xyz[cloud.sensor == 1], *(a[cloud.sensor == 1] for a in
                                                             (cloud.sensor, cloud.zone, cloud.valid)))
    assert np.allclose(cloud.xyz[:, 0], 1.5, atol=0.002)
    az, el, rng = cloud.polar()
    assert abs(np.mean(az)) < 2 and -25 < np.mean(el) < 0


def test_floor_and_min_range_filters():
    r = rig()
    m = r.sensors[4]                      # S5, tilted down 40 deg
    frames = simulate_wall(m, r, (0, 0, -1), 1.55)  # the floor, 1.55 m below the head centre
    cloud = frame_to_points(frames[0], r)
    assert not cloud.valid[cloud.sensor == 4].any(), "floor hits should be filtered"


def test_head_to_world():
    p = np.array([[1.0, 0.0, 0.0]])
    w = head_to_world(p, HeadOrientation(pitch_deg=-30))
    a, e = to_angles(w[0])
    assert abs(e + 30) < 1e-9 and abs(a) < 1e-9


def test_fit_range():
    r = rig()
    m = replace(r.sensors[1], az_deg=0, el_deg=0, x_m=0, y_m=0, z_m=0,
                range_scale=0.97, range_offset_mm=-25.0)
    r2 = apply(r, 1, m)
    data = {mm: simulate_wall(m, r2, (1, 0, 0), mm / 1000) for mm in (500, 1000, 2000)}
    scale, offset, rms = fit_range(data, 1)
    assert abs(scale - m.range_scale) < 0.005 and abs(offset - m.range_offset_mm) < 3 and rms < 3


def test_calibrate_mount_recovers_angles():
    r = rig()
    for k, normal in [(1, (1, 0, 0)), (0, (1, 0, 0)), (3, (0, -1, 0)), (4, (1, 0, 0))]:
        true_m = replace(r.sensors[k], az_deg=r.sensors[k].az_deg + 4, el_deg=r.sensors[k].el_deg - 3)
        frames = simulate_wall(true_m, apply(r, k, true_m), normal, 1.2, noise_mm=8)
        fitted, rep = calibrate_mount(r, k, frames, normal, 1.2)
        assert abs(fitted.az_deg - true_m.az_deg) < 0.7, rep
        assert abs(fitted.el_deg - true_m.el_deg) < 0.7, rep
        assert rep["rms_m"] < 0.02 and rep["rms_m"] < rep["start_rms_m"], rep
        print("  ", rep)


def test_layout_conventions():
    from geometry.layout import angle_to_az, az_to_angle, fields_to_row, row_to_fields
    # angle: counter-clockwise from above, 90 = left -> internal az + right
    assert angle_to_az(0) == 0 and angle_to_az(90) == -90 and angle_to_az(270) == 90
    assert angle_to_az(180) in (-180, 180) and angle_to_az(320) == 40 and angle_to_az(360) == 0
    for az in (-170, -85, -40, 0, 40, 85, 170):
        assert abs(angle_to_az(az_to_angle(az)) - az) < 1e-9
    # x_cm right / y_cm front / height_cm up -> x_m forward / y_m right / z_m up
    f = row_to_fields((-8.0, 15.0, 40.0, -10.0, 7.0))
    assert f == {"x_m": 0.15, "y_m": -0.08, "z_m": 0.07, "az_deg": -40.0, "el_deg": -10.0}
    assert row_to_fields((1, 2, 0, 0))["z_m"] == 0.0              # height optional
    m = replace(rig().sensors[0], **f)
    assert fields_to_row(m) == (-8.0, 15.0, 40.0, -10.0, 7.0)
    for bad in [(1, 2, 3), (0, 0, 0, 95)]:
        try:
            row_to_fields(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted bad row {bad}")


def test_rigs_load_geometry_from_layout_file():
    # the real, hand-edited layout: only check that it loads into sane values
    from geometry.layout import row_to_fields
    import importlib.util
    spec = importlib.util.spec_from_file_location("sl", ROOT / "config" / "sensor_layout.py")
    sl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sl)
    real = load_mounts(ROOT / "config" / "cap_mounts.json")
    assert [m.id for m in real.sensors] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    for m in real.sensors:
        f = row_to_fields(sl.CAP[m.id])
        assert (m.az_deg, m.el_deg, m.x_m, m.y_m, m.z_m) == (f["az_deg"], f["el_deg"], f["x_m"], f["y_m"], f["z_m"])
        assert -180 <= m.az_deg <= 180 and -90 <= m.el_deg <= 90 and abs(m.x_m) < 0.5 and abs(m.y_m) < 0.5
    flat = load_mounts(ROOT / "config" / "flat_mounts.json")
    assert [m.roll_deg for m in flat.sensors] == [0, 0, 0, 180, 180, 180]     # advanced settings kept
    assert flat.sensors[3].z_m == -0.02 and flat.sensors[2].y_m == 0.025


def test_mpu6050_is_a_stub():
    try:
        MPU6050Source()
    except NotImplementedError:
        return
    raise AssertionError("MPU6050Source should not be implemented yet")


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")

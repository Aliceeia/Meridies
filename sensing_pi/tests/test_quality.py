"""Checks for the zone quality filter and the learned self-mask. Run from sensing_pi/:
    .venv/bin/python tests/test_quality.py        (or all tests: .venv/bin/python tests/run_all.py)
(also works under pytest)."""
import csv
import json
import tempfile
from pathlib import Path

import numpy as np

from fixtures import ROOT, cap_rig, pinned_cfg
from geometry import load_mounts
from geometry.calibrate import load_tof6_csv
from objects import ObjectPipeline, load_cfg
from objects.quality import QualityFilter, learn_mask, load_mask, mask_path_for
from sensors import frames as C
from sim.scene import Plane, render

CAP = cap_rig()
CFG = pinned_cfg(CAP)
Z = 64


def frame(d=1000, s=5, sigma=10, signal=20):
    return {"t": 0.0, "res": 8, "d": [[d] * Z for _ in range(6)], "s": [[s] * Z for _ in range(6)],
            "sigma": [[sigma] * Z for _ in range(6)], "signal": [[signal] * Z for _ in range(6)]}


def valid_count(f):
    return sum(v in (5, 9) for row in f["s"] for v in row)


def test_good_readings_pass():
    q = QualityFilter(CFG, mask={})
    assert valid_count(q.apply(frame())) == 6 * Z


def test_high_sigma_rejected_relative_to_distance():
    q = QualityFilter(CFG, mask={})
    assert valid_count(q.apply(frame(d=1000, sigma=40))) == 0     # > 3% of 1 m
    assert valid_count(q.apply(frame(d=3000, sigma=40))) == 6 * Z  # fine at 3 m
    assert valid_count(q.apply(frame(d=200, sigma=14))) == 6 * Z   # under the 15 mm floor


def test_weak_signal_rejected():
    q = QualityFilter(CFG, mask={})
    assert valid_count(q.apply(frame(signal=1))) == 0


def test_old_frames_without_quality_fields_pass():
    f = frame(sigma=999, signal=0)
    del f["sigma"], f["signal"]
    assert valid_count(QualityFilter(CFG, mask={}).apply(f)) == 6 * Z


def test_disabled_filter_is_a_no_op():
    cfg = {**CFG, "quality": {**CFG["quality"], "enabled": False}}
    f = frame(sigma=999)
    assert QualityFilter(cfg, mask={}).apply(f) is f


def test_self_mask_removes_near_but_keeps_far():
    mask = {1: np.array([200] * 10 + [0] * (Z - 10), dtype=float)}   # S2, first 10 zones, <= 200 mm
    q = QualityFilter(CFG, mask=mask)
    near = q.apply(frame(d=150))
    assert sum(v in (5, 9) for v in near["s"][1]) == Z - 10            # 10 masked
    assert valid_count(near) == 6 * Z - 10
    assert valid_count(q.apply(frame(d=900))) == 6 * Z                  # something real further out stays


def test_learn_mask():
    frames = []
    rng = np.random.default_rng(0)
    for _ in range(50):
        f = frame(d=2500)
        f["d"][2][:8] = [int(v) for v in rng.normal(120, 5, 8)]          # S3 top row sees the brim
        f["d"][4][5] = 300 if rng.random() < 0.1 else 2500              # S5 zone 5: rare near hit (10%)
        frames.append(f)
    zones, stats = learn_mask(frames, [f"S{k + 1}" for k in range(6)], near_mm=600, min_frac=0.2, margin_mm=30)
    assert stats["S3"] == (8, Z) and all(140 < v < 200 for v in zones["S3"][:8])
    assert stats["S5"] == (0, Z) and stats["S1"] == (0, Z)


def test_mask_file_is_found_and_loaded():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        for name in ("cap_mounts.json", "sensor_layout.py"):
            (d / name).write_text((ROOT / "config" / name).read_text())
        rig = load_mounts(d / "cap_mounts.json")
        assert mask_path_for(rig) == d / "selfmask_cap_mounts.json"
        assert load_mask(mask_path_for(rig), rig) is None                # no file -> no mask
        (d / "selfmask_cap_mounts.json").write_text(json.dumps({"zones": {"S2": [100] * Z}}))
        m = load_mask(mask_path_for(rig), rig)
        assert list(m) == [1] and m[1][0] == 100
        assert QualityFilter(load_cfg(rig=rig), rig).mask                 # picked up automatically


def test_pipeline_ignores_brim_with_mask():
    """A surface 12 cm in front of S2 is an urgent obstacle without the mask and gone with it."""
    brim = [Plane((0.29, 0, 0), (1, 0, 0))]                              # S2 sits at x = 0.17 m
    frames = [render(CAP, brim, t=i / 10) for i in range(5)]
    for f in frames:                                                     # keep only S2's view of it
        for k in range(6):
            if k != 1:
                f["d"][k] = [0] * Z
                f["s"][k] = [255] * Z
    _, w = ObjectPipeline(CAP, CFG, quality=QualityFilter(CFG, mask={})).step(frames[0])
    assert w and w[0]["level"] == "urgent", w
    zones, _ = learn_mask(frames, [m.id for m in CAP.sensors])
    mask = {k: np.array(zones[m.id], dtype=float) for k, m in enumerate(CAP.sensors) if any(zones[m.id])}
    pipe = ObjectPipeline(CAP, CFG, quality=QualityFilter(CFG, mask=mask))
    for f in frames:
        _, w = pipe.step(f)
    assert w == [], w


def test_csv_roundtrip_with_quality_and_old_format():
    f = frame(d=1234, sigma=12, signal=34)
    f["t"] = 5.0
    with tempfile.TemporaryDirectory() as d:
        for q in (True, False):
            p = Path(d) / f"x{q}.csv"
            with open(p, "w", newline="") as fh:
                fh.write(C.csv_meta_line(8, 10, quality=q))
                w = csv.writer(fh)
                w.writerow(C.csv_header(8, quality=q))
                w.writerow(C.frame_to_row(f, quality=q))
            g = load_tof6_csv(p)[0]
            assert g["d"] == f["d"] and g["s"] == f["s"]
            assert ("sigma" in g) == q and (not q or (g["sigma"] == f["sigma"] and g["signal"] == f["signal"]))
            with open(p, newline="") as fh:                              # the viewer's reader
                fh.readline()
                rd = csv.reader(fh)
                next(rd)
                v = C.row_to_frame(next(rd), 8)
            assert v["d"] == f["d"] and ("sigma" in v) == q


if __name__ == "__main__":
    tests = [(n, fn) for n, fn in globals().items() if n.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")

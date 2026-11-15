"""Checks for the calibration tools' non-interactive parts. Run from sensing_pi/:
    .venv/bin/python tests/test_tools.py          (or all tests: .venv/bin/python tests/run_all.py)
(also works under pytest)."""
import json
import sys
import tempfile
from pathlib import Path

from fixtures import ROOT

sys.path.insert(0, str(ROOT / "tools"))
from geometry.calibrate import fit_range  # noqa: E402
from range_cal import centre_reading, update_range_in_json  # noqa: E402


def wall_frames(raw_mm, n=10, k=0):
    f = {"t": 0.0, "res": 8, "d": [[0] * 64 for _ in range(6)], "s": [[255] * 64 for _ in range(6)]}
    f["d"][k] = [int(raw_mm)] * 64
    f["s"][k] = [5] * 64
    return [f] * n


def test_fit_recovers_scale_and_offset():
    # a sensor that reads 3 % long and 20 mm short: raw = (true - (-20)) / 0.97 ... i.e. true = raw * 0.97 - 20
    data = {t: wall_frames((t + 20) / 0.97) for t in (500.0, 1000.0, 2000.0)}
    scale, offset, rms = fit_range(data, 0)
    assert abs(scale - 0.97) < 0.002 and abs(offset + 20) < 2 and rms < 1


def test_centre_reading_ignores_invalid_zones():
    f = wall_frames(1000)[0]
    f["s"][0][27] = 255
    f["d"][0][28] = 5000
    f["s"][0][28] = 255
    r, nz = centre_reading(f, 0)
    assert r == 1000 and nz == 2


def test_update_range_in_json_touches_only_that_sensor():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "cap_mounts.json"
        original = (ROOT / "config" / "cap_mounts.json").read_text()
        p.write_text(original)
        assert update_range_in_json(p, "S3", 0.9812, -14.26)
        new = json.loads(p.read_text())
        s = {x["id"]: x for x in new["sensors"]}
        assert s["S3"]["range_scale"] == 0.9812 and s["S3"]["range_offset_mm"] == -14.3
        assert all(s[i]["range_scale"] == 1.0 and s[i]["range_offset_mm"] == 0.0 for i in s if i != "S3")
        old_lines, new_lines = original.splitlines(), p.read_text().splitlines()
        assert len(old_lines) == len(new_lines)
        assert sum(a != b for a, b in zip(old_lines, new_lines)) == 1      # only S3's line changed
        assert not update_range_in_json(p, "S9", 1.0, 0.0)


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")

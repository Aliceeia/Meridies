"""The six-sensor frame format: CSV recording format and the 2 x 3 mosaic image.
No hardware imports, so it is safe on the laptop (used by tools/tof6_view.py).

A frame is a dict:
    {"t": <unix seconds>, "res": 4|8, "d": [S1..S6 zone distances mm], "s": [S1..S6 zone statuses],
     "sigma": [S1..S6 range_sigma_mm], "signal": [S1..S6 signal_per_spad]}     # sigma/signal optional
where each list per sensor is a flat list of res*res values in the sensor's own zone order.
CSV files with sigma/signal have "q=1" in their first line; older files (q missing) have d and s only.
"""

import csv
from pathlib import Path

from .pins import ADDRS, DEFAULT_ADDR, INT_PINS, LPN_PINS, N_SENSORS  # noqa: F401

# Physical arrangement as seen from behind the sensors (sensor numbers, 1-based).
LAYOUT = [[1, 2, 3],
          [4, 5, 6]]

VALID_STATUS = {5, 9}  # range valid, valid with large pulse


# ---------- CSV ----------
BLOCKS = ("d", "s", "sigma", "signal")   # column blocks in a q=1 CSV, in this order


def csv_meta_line(res, hz, quality=True):
    return f"# tof6 res={res} hz={hz}" + (" q=1" if quality else "") + "\n"


def csv_header(res, quality=True):
    z = res * res
    blocks = BLOCKS if quality else BLOCKS[:2]
    return ["t"] + [f"S{k + 1}_{b}{i}" for b in blocks for k in range(N_SENSORS) for i in range(z)]


def frame_to_row(f, quality=True):
    blocks = BLOCKS if quality else BLOCKS[:2]
    z = len(f["d"][0])
    row = [f"{f['t']:.4f}"]
    for b in blocks:
        src = f.get(b) or [[0] * z] * N_SENSORS
        row += [v for k in range(N_SENSORS) for v in src[k]]
    return row


def row_to_frame(row, res):
    """Works for both old (d, s) and q=1 (d, s, sigma, signal) rows."""
    z = res * res
    vals = [int(v) for v in row[1:]]
    n_blocks = len(vals) // (N_SENSORS * z)
    f = {"t": float(row[0]), "res": res}
    for b, name in enumerate(BLOCKS[:n_blocks]):
        off = b * N_SENSORS * z
        f[name] = [vals[off + k * z:off + (k + 1) * z] for k in range(N_SENSORS)]
    return f


def parse_meta(line):
    """'# tof6 res=8 hz=10' -> {'res': 8, 'hz': 10}"""
    out = {}
    for tok in line.lstrip("#").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = int(v)
    return out


def load_csv(path: str | Path) -> list[dict]:
    """Read a recording into a list of frames. Files with "q=1" in the first line also carry
    per-zone sigma and signal; older files have distances and statuses only. Rows that can't be
    parsed (e.g. a recording cut off by a power loss ends in NUL bytes) are skipped and counted."""
    with open(path, newline="") as fh:
        meta = dict(tok.split("=", 1) for tok in fh.readline().lstrip("#").split() if "=" in tok)
        res = int(meta["res"])
        n_blocks = 4 if meta.get("q") == "1" else 2
        z = res * res
        rd = csv.reader(fh)
        next(rd)
        frames, bad = [], 0
        for row in rd:
            if not row:
                continue
            try:
                t = float(row[0])
                vals = [int(v) for v in row[1:]]
            except ValueError:
                bad += 1
                continue
            n = len(vals) // (n_blocks * z)
            if n == 0 or len(vals) != n_blocks * n * z:
                bad += 1
                continue
            f = {"t": t, "res": res}
            for b, name in enumerate(BLOCKS[:n_blocks]):
                off = b * n * z
                f[name] = [vals[off + k * z:off + (k + 1) * z] for k in range(n)]
            frames.append(f)
    if bad:
        print(f"{path}: skipped {bad} malformed row(s)")
    return frames


# ---------- image ----------
def _orient(tile, how):
    import numpy as np
    return {"none": tile, "flipx": np.fliplr(tile), "flipy": np.flipud(tile),
            "rot180": np.rot90(tile, 2)}[how]


def mosaic(frame, tile="none", row_tile=None):
    """Combine the six tiles into one (2*res, 3*res) masked array of mm.
    Invalid zones are masked. `tile` re-orients every tile the same way;
    `row_tile` (one entry per LAYOUT row) adds an extra per-row re-orientation,
    e.g. for a row of boards mounted upside down."""
    import numpy as np
    res = frame["res"]
    rows = len(LAYOUT)
    cols = len(LAYOUT[0])
    img = np.zeros((rows * res, cols * res), dtype=float)
    bad = np.ones_like(img, dtype=bool)
    row_tile = row_tile or ["none"] * rows
    for r, layout_row in enumerate(LAYOUT):
        for c, sensor in enumerate(layout_row):
            k = sensor - 1
            d = np.asarray(frame["d"][k], dtype=float).reshape(res, res)
            s = np.asarray(frame["s"][k]).reshape(res, res)
            fix = lambda t: _orient(_orient(t, tile), row_tile[r])  # noqa: E731
            img[r * res:(r + 1) * res, c * res:(c + 1) * res] = fix(d)
            bad[r * res:(r + 1) * res, c * res:(c + 1) * res] = fix(~np.isin(s, list(VALID_STATUS)))
    return np.ma.masked_array(img, mask=bad)

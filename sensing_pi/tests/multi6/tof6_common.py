"""Shared bits for the 6 x VL53L5CX co-test. No hardware imports: safe on the laptop.

A frame is a dict:
    {"t": <unix seconds>, "res": 4|8, "d": [S1..S6 zone distances mm], "s": [S1..S6 zone statuses]}
where each of d[k] / s[k] is a flat list of res*res values in the sensor's own zone order.
"""

N_SENSORS = 6
LPN_PINS = [17, 27, 22, 23, 24, 25]   # S1..S6, BCM numbering
INT_PINS = [5, 6, 13, 19, 26, 16]     # S1..S6, BCM numbering
ADDRS = [0x30, 0x31, 0x32, 0x33, 0x34, 0x35]
DEFAULT_ADDR = 0x29

# Physical arrangement as seen from behind the sensors (sensor numbers, 1-based).
LAYOUT = [[1, 2, 3],
          [4, 5, 6]]

VALID_STATUS = {5, 9}  # range valid, valid with large pulse


# ---------- CSV ----------
def csv_meta_line(res, hz):
    return f"# tof6 res={res} hz={hz}\n"


def csv_header(res):
    z = res * res
    return (["t"]
            + [f"S{k + 1}_d{i}" for k in range(N_SENSORS) for i in range(z)]
            + [f"S{k + 1}_s{i}" for k in range(N_SENSORS) for i in range(z)])


def frame_to_row(f):
    return ([f"{f['t']:.4f}"]
            + [v for k in range(N_SENSORS) for v in f["d"][k]]
            + [v for k in range(N_SENSORS) for v in f["s"][k]])


def row_to_frame(row, res):
    z = res * res
    vals = [int(v) for v in row[1:]]
    d = [vals[k * z:(k + 1) * z] for k in range(N_SENSORS)]
    off = N_SENSORS * z
    s = [vals[off + k * z:off + (k + 1) * z] for k in range(N_SENSORS)]
    return {"t": float(row[0]), "res": res, "d": d, "s": s}


def parse_meta(line):
    """'# tof6 res=8 hz=10' -> {'res': 8, 'hz': 10}"""
    out = {}
    for tok in line.lstrip("#").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = int(v)
    return out


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

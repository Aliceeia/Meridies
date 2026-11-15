"""Convert between the hand-edited layout (config/sensor_layout.py) and the internal head frame.

Layout (what you edit)                      Internal (what the code uses)
  x_cm      + right                           y_m      + right
  y_cm      + front                           x_m      + forward
  height_cm + up                              z_m      + up
  angle_deg 0 front, counter-clockwise        az_deg   0 front, + right, -180..180
            (90 = left, 270 = right)
  tilt_deg  0 level, - down, + up             el_deg   same

Run `python -m geometry.layout config/cap_mounts.json` (from sensing_pi/) to print the
layout as the code sees it.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

GEOMETRY_FIELDS = ("x_m", "y_m", "z_m", "az_deg", "el_deg")
COLUMNS = ("x_cm", "y_cm", "angle_deg", "tilt_deg", "height_cm")


def angle_to_az(angle_deg: float) -> float:
    """Layout angle (CCW, 0..360, 90 = left) -> internal az (-180..180, + right)."""
    az = (-float(angle_deg) + 180.0) % 360.0 - 180.0
    return 0.0 if az == 0 else az   # avoid -0.0


def az_to_angle(az_deg: float) -> float:
    """Internal az (+ right) -> layout angle (CCW, 0..360)."""
    a = (-float(az_deg)) % 360.0
    return 0.0 if math.isclose(a, 360.0) else a


def row_to_fields(row) -> dict:
    """(x_cm, y_cm, angle_deg, tilt_deg[, height_cm]) -> internal Mount geometry fields."""
    if len(row) not in (4, 5):
        raise ValueError(f"layout row needs 4 or 5 numbers (x_cm, y_cm, angle_deg, tilt_deg[, height_cm]), got {row!r}")
    x_cm, y_cm, angle, tilt = (float(v) for v in row[:4])
    height = float(row[4]) if len(row) == 5 else 0.0
    if not -90.0 <= tilt <= 90.0:
        raise ValueError(f"tilt_deg must be between -90 and 90, got {tilt}")
    return {"x_m": y_cm / 100.0, "y_m": x_cm / 100.0, "z_m": height / 100.0,
            "az_deg": angle_to_az(angle), "el_deg": tilt}


def fields_to_row(m) -> tuple:
    """Internal Mount -> (x_cm, y_cm, angle_deg, tilt_deg, height_cm), rounded for pasting."""
    return (round(m.y_m * 100, 1), round(m.x_m * 100, 1), round(az_to_angle(m.az_deg), 1),
            round(m.el_deg, 1), round(m.z_m * 100, 1))


def format_row(sensor_id: str, row) -> str:
    """One line in the same format as config/sensor_layout.py."""
    x, y, a, t, h = row
    return f'    "{sensor_id}": ({x:7.1f}, {y:7.1f},   {a:7.1f},   {t:6.1f},  {h:6.1f}),'


def load_layout(layout_file: Path, name: str) -> dict:
    """Read LAYOUTS[name] from a layout .py file -> {sensor_id: fields}."""
    spec = importlib.util.spec_from_file_location(f"_sensor_layout_{name}", layout_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    layouts = getattr(mod, "LAYOUTS", None)
    if not isinstance(layouts, dict) or name not in layouts:
        raise KeyError(f"{layout_file}: no LAYOUTS[{name!r}] (have {list(layouts or {})})")
    out = {}
    for sid, row in layouts[name].items():
        try:
            out[sid] = row_to_fields(row)
        except ValueError as e:
            raise ValueError(f"{layout_file} {name} {sid}: {e}") from None
    return out


def with_rows(rig, rows: dict):
    """Copy of `rig` with each sensor's geometry replaced by rows {id: (x_cm, y_cm, angle, tilt[, height])}.
    Used by the tests so they don't depend on the hand-edited layout file. The copy is not linked
    to the rig json any more, so a self-mask learned for the real geometry is not applied to it."""
    from dataclasses import replace
    extra = {k: v for k, v in rig.extra.items() if k != "_source"}
    return replace(rig, extra=extra, sensors=[replace(m, **row_to_fields(rows[m.id])) for m in rig.sensors])


def _main(argv):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from geometry.transform import load_mounts
    if len(argv) != 2:
        print("usage: python -m geometry.layout config/cap_mounts.json")
        return 2
    rig = load_mounts(argv[1])
    src = rig.extra.get("layout", "(inline in the json)")
    print(f"{argv[1]}  layout: {src}")
    print(f"{'id':<4}{'x_cm':>7}{'y_cm':>7}{'angle':>8}{'tilt':>7}{'height':>8}   ->  internal az(+right)  el   x_fwd  y_right  z  roll")
    for m in rig.sensors:
        x, y, a, t, h = fields_to_row(m)
        print(f"{m.id:<4}{x:7.1f}{y:7.1f}{a:8.1f}{t:7.1f}{h:8.1f}   ->  {m.az_deg:7.1f}  {m.el_deg:6.1f}  "
              f"{m.x_m:6.3f} {m.y_m:7.3f} {m.z_m:6.3f}  {m.roll_deg:4.0f}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv))

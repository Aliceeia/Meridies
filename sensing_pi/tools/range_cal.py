#!/usr/bin/env python3
"""Self-paced range calibration for one sensor against a flat wall.

Needs the raw stream running (main.py). Run from sensing_pi/:
    .venv/bin/python tools/range_cal.py --sensor S1

For each distance (default 0.5, 1.0, 2.0 m) point the sensor straight at a flat wall with its
front face that far away (tape measure), hold it still and press Enter. It records a few
seconds, then fits true = raw * range_scale + range_offset_mm from the centre zones, shows the
error before/after, and asks before saving the two numbers into the rig json.
"""
import argparse
import json
import queue
import re
import socket
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from geometry import load_mounts  # noqa: E402
from geometry.calibrate import centre_zones, fit_range  # noqa: E402

RECORD_FRAMES = 30
REDRAW_S = 0.35
CLEAR = "\033[H\033[2J"
BOLD, RESET, GREEN, YELLOW, RED = "\033[1m", "\033[0m", "\033[32m", "\033[33m", "\033[31m"


def update_range_in_json(path: Path, sensor_id: str, scale: float, offset: float) -> bool:
    """Set range_scale / range_offset_mm on the one-line entry for sensor_id, keeping the file's layout."""
    text = path.read_text()
    pat = re.compile(r'(\{[^{}]*"id"\s*:\s*"' + re.escape(sensor_id) + r'"[^{}]*\})')
    m = pat.search(text)
    if not m:
        return False
    entry = m.group(1)
    new = re.sub(r'("range_scale"\s*:\s*)[-0-9.eE]+', lambda g: f"{g.group(1)}{scale:.4f}", entry)
    new = re.sub(r'("range_offset_mm"\s*:\s*)[-0-9.eE]+', lambda g: f"{g.group(1)}{offset:.1f}", new)
    if new == entry and f"{scale:.4f}" not in entry:
        return False
    path.write_text(text[:m.start(1)] + new + text[m.end(1):])
    json.loads(path.read_text())          # still valid json
    return True


class Reader(threading.Thread):
    def __init__(self, host, port):
        super().__init__(daemon=True)
        self.f = socket.create_connection((host, port), timeout=5).makefile("r")
        self.latest = None
        self.n = 0
        self.error = None
        self.listeners = []

    def run(self):
        try:
            for line in self.f:
                fr = json.loads(line)
                self.latest = fr
                self.n += 1
                for q in self.listeners:
                    q.put(fr)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)


def stdin_lines(q):
    for line in sys.stdin:
        q.put(line.strip())


def centre_reading(fr, k):
    """Median of the valid centre zones (mm) and how many were valid."""
    zones = centre_zones(fr["res"])
    d = np.array(fr["d"][k])[zones]
    s = np.array(fr["s"][k])[zones]
    ok = np.isin(s, (5, 9)) & (d > 0)
    return (float(np.median(d[ok])) if ok.any() else None), int(ok.sum())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sensor", default="S1")
    p.add_argument("--rig", default="config/cap_mounts.json")
    p.add_argument("--host", default="localhost:5557")
    p.add_argument("--distances", default="500,1000,2000", help="true distances in mm, comma separated")
    p.add_argument("--save", help="also write the result as JSON to this file")
    a = p.parse_args()
    rig_path = Path(a.rig) if Path(a.rig).exists() else ROOT / a.rig
    rig = load_mounts(rig_path)
    k = rig.index(a.sensor)
    m = rig.sensors[k]
    dists = [float(x) for x in a.distances.split(",")]
    host, _, port = a.host.partition(":")
    rd = Reader(host, int(port or 5557))
    rd.start()
    keys = queue.Queue()
    threading.Thread(target=stdin_lines, args=(keys,), daemon=True).start()

    recorded: dict[float, list] = {}
    recent = deque(maxlen=12)
    for i, true_mm in enumerate(dists):
        while not keys.empty():
            keys.get_nowait()
        state = "aim"
        frames, fq = [], None
        while True:
            if rd.error:
                sys.exit(f"\nlost the raw stream: {rd.error} (is main.py running?)")
            fr = rd.latest
            reading, nz = centre_reading(fr, k) if fr else (None, 0)
            if reading is not None:
                recent.append(reading)
            if state == "aim" and not keys.empty():
                keys.get_nowait()
                state, fq = "record", queue.Queue()
                rd.listeners.append(fq)
            if state == "record":
                while not fq.empty() and len(frames) < RECORD_FRAMES:
                    frames.append(fq.get_nowait())
                if len(frames) >= RECORD_FRAMES:
                    rd.listeners.remove(fq)
                    recorded[true_mm] = frames
                    break
            out = [CLEAR, f"{BOLD}RANGE CALIBRATION - {a.sensor}{RESET}   (current correction: "
                          f"scale {m.range_scale:.4f}, offset {m.range_offset_mm:+.1f} mm)   frames: {rd.n}", ""]
            out.append(f"  DISTANCE {i + 1} of {len(dists)}:")
            out.append(f"  {BOLD}{YELLOW}>>> POINT {a.sensor} STRAIGHT AT A FLAT WALL, ITS FRONT FACE "
                       f"{true_mm / 10:.0f} CM AWAY <<<{RESET}")
            if state == "aim":
                out.append(f"  {BOLD}Hold it still, then press Enter in this window.{RESET}")
            else:
                out.append(f"  {GREEN}recording... keep still  [{'#' * len(frames):<{RECORD_FRAMES}}]{RESET}")
            out += ["", "  Tips: square to the wall (not tilted), nothing else in front of it, a matt wall is best."]
            out += ["", "  Live centre reading (raw, before correction):"]
            if reading is None:
                out.append(f"    {RED}no valid centre reading{RESET} - is the wall in view and within 4 m?")
            else:
                spread = float(np.std(recent)) if len(recent) > 3 else 0.0
                steady = f"{GREEN}steady{RESET}" if spread < 6 else f"{YELLOW}moving ({spread:.0f} mm){RESET}"
                err = reading - true_mm
                out.append(f"    {reading:6.0f} mm   ({nz}/4 centre zones valid)   {steady}   "
                           f"reads {err:+.0f} mm vs {true_mm:.0f} mm")
            out += ["", "  Done so far:"]
            for t, frs in recorded.items():
                vals = [r for r in (centre_reading(f, k)[0] for f in frs) if r is not None]
                out.append(f"    {t:6.0f} mm -> raw mean {np.mean(vals):6.1f} mm" if vals else f"    {t:6.0f} mm -> no valid data")
            out.append("\n  (Ctrl-C to quit without saving)")
            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
            time.sleep(REDRAW_S if state == "aim" else 0.1)

    scale, offset, rms = fit_range(recorded, k)
    rows = []
    for t, frs in recorded.items():
        vals = [r for r in (centre_reading(f, k)[0] for f in frs) if r is not None]
        raw = float(np.mean(vals)) if vals else float("nan")
        rows.append((t, raw, raw - t, raw * scale + offset - t))
    sane = 0.85 <= scale <= 1.15 and abs(offset) < 150 and rms < 30
    sys.stdout.write(CLEAR)
    print(f"{BOLD}=== RANGE CALIBRATION RESULT for {a.sensor} ==={RESET}\n")
    print("   true (mm)   raw mean   error before   error after")
    for t, raw, eb, ea in rows:
        print(f"   {t:8.0f}   {raw:8.1f}   {eb:+10.1f}     {ea:+9.1f}")
    print(f"\n   fit: true = raw x {scale:.4f} {offset:+.1f} mm   (rms after: {rms:.1f} mm)")
    if not sane:
        print(f"\n   {RED}This looks off (scale outside 0.85-1.15, offset > 150 mm or rms > 30 mm).{RESET}")
        print("   Check the sensor was square to the wall and the distances were measured from its front face.")
    if a.save:
        Path(a.save).write_text(json.dumps({"sensor": a.sensor, "scale": scale, "offset_mm": offset, "rms_mm": rms,
                                            "rows": rows, "sane": sane}) + "\n")
    print(f"\n   Save scale {scale:.4f} and offset {offset:+.1f} mm for {a.sensor} into {rig_path.name}? "
          f"Type y and press Enter (anything else = no): ", end="", flush=True)
    answer = keys.get()
    if answer.lower().startswith("y"):
        if update_range_in_json(rig_path, a.sensor, scale, offset):
            print(f"   {GREEN}saved.{RESET} Restart main.py to use it.")
        else:
            print(f"   {RED}could not find {a.sensor}'s line in {rig_path.name}; set it by hand.{RESET}")
    else:
        print("   not saved.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped, nothing saved")

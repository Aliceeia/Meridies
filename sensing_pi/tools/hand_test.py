#!/usr/bin/env python3
"""Self-paced hand test: does each sensor's 8x8 grid map left/right and up/down the right way?

Needs the raw stream running (main.py). Run from sensing_pi/, with the cap on:
    .venv/bin/python tools/hand_test.py --sensor S1

The screen redraws in place a few times a second: the current step at the top, a checklist,
and a small picture of where the code thinks your hand is. Hold your hand in front of the
sensor, then move it RIGHT, LEFT, UP and DOWN (about 15-20 cm, holding each until it says OK).
If the picture moves the opposite way to your hand, that axis is mirrored; the result at the
end says what to set in config/cap_mounts.json.
"""
import argparse
import json
import socket
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from geometry import load_mounts  # noqa: E402
from geometry.transform import basis, zone_rays  # noqa: E402

NEAR_MM = 450        # the hand must be closer than this to the sensor
MIN_ZONES = 2
HOLD_S = 1.2         # a position counts once it is held this long
MOVE_DEG = 5.0       # minimum change in direction to count as "moved"
PAUSE_S = 2.0        # pause after each OK so you can read the next step
REDRAW_S = 0.35      # screen refresh interval

CLEAR = "\033[H\033[2J"
BOLD, RESET, GREEN, YELLOW = "\033[1m", "\033[0m", "\033[32m", "\033[33m"


def zone_dirs(m, res, fov, zone_flip):
    """(h, v) degrees of every zone in the sensor's own frame (h + right, v + up), current config."""
    local = zone_rays(m, res, fov, zone_flip) @ basis(m.az_deg, m.el_deg, m.roll_deg)
    return np.degrees(np.arctan2(local[:, 1], local[:, 0])), np.degrees(np.arctan2(local[:, 2], local[:, 0]))


class Reader(threading.Thread):
    """Keeps the latest hand reading for one sensor from the raw stream."""

    def __init__(self, host, port, k, h, v):
        super().__init__(daemon=True)
        self.f = socket.create_connection((host, port), timeout=5).makefile("r")
        self.k, self.h, self.v = k, h, v
        self.latest = None      # (h, v, dist_mm, near_mask) or None
        self.frames = 0
        self.error = None

    def run(self):
        try:
            for line in self.f:
                fr = json.loads(line)
                d = np.array(fr["d"][self.k]); s = np.array(fr["s"][self.k])
                near = np.isin(s, (5, 9)) & (d > 0) & (d < NEAR_MM)
                self.frames += 1
                if near.sum() < MIN_ZONES:
                    self.latest = None
                else:
                    self.latest = (float(self.h[near].mean()), float(self.v[near].mean()), float(np.median(d[near])), near)
        except Exception as e:  # noqa: BLE001
            self.error = str(e)


def picture(h, v, near, res):
    """Where the hand is, drawn as the code sees it (your view, looking out from the sensor)."""
    step = (h.max() - h.min()) / (res - 1) if res > 1 else 1
    grid = [["." for _ in range(res)] for _ in range(res)]
    if near is not None:
        for z in np.flatnonzero(near):
            c = int(round((h[z] - h.min()) / step))
            r = int(round((v.max() - v[z]) / step))
            grid[r][c] = "#"
    lines = ["        up", "   " + "-" * (2 * res + 1)]
    for r, row in enumerate(grid):
        side = "left " if r == res // 2 else "     "
        tail = " right" if r == res // 2 else ""
        lines.append(f"{side}| {' '.join(row)} |{tail}")
    lines += ["   " + "-" * (2 * res + 1), "        down"]
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--sensor", default="S1")
    p.add_argument("--rig", default="config/cap_mounts.json")
    p.add_argument("--host", default="localhost:5557")
    p.add_argument("--res", type=int, default=8)
    p.add_argument("--save", help="write the result as JSON to this file")
    a = p.parse_args()
    rig = load_mounts(a.rig if Path(a.rig).exists() else ROOT / a.rig)
    k = rig.index(a.sensor)
    m = rig.sensors[k]
    h, v = zone_dirs(m, a.res, rig.fov_deg, rig.zone_flip)
    host, _, port = a.host.partition(":")
    rd = Reader(host, int(port or 5557), k, h, v)
    rd.start()

    # steps: (label, instruction, kind) ; kind = "hold" | ("move", axis, name) | "centre"
    steps = [("Hand in front", f"Hold a flat hand 20-30 cm in front of {a.sensor} and keep it still", "hold")]
    for name, axis in (("RIGHT", 0), ("LEFT", 0), ("UP", 1), ("DOWN", 1)):
        steps.append((name, f"Move your hand 15-20 cm to your {name}, then hold it still", ("move", axis, name)))
        steps.append(("Back to middle", "Bring your hand back to the middle and hold it still", "centre"))

    centre, moves = None, {}
    i, since, vals, last_draw, ok_until = 0, None, [], 0.0, 0.0
    status = ""
    while i < len(steps):
        if rd.error:
            sys.exit(f"\nlost the raw stream: {rd.error} (is main.py running?)")
        now = time.monotonic()
        r = rd.latest
        label, text, kind = steps[i]
        if now >= ok_until:
            accept = False
            if r is not None:
                hh, vv = r[0], r[1]
                if kind == "hold":
                    accept = True
                elif kind == "centre":
                    accept = abs(hh - centre[0]) < MOVE_DEG and abs(vv - centre[1]) < MOVE_DEG
                else:
                    accept = abs((hh, vv)[kind[1]] - centre[kind[1]]) >= MOVE_DEG
            if accept:
                since = since or now
                vals.append((r[0], r[1]))
                if now - since >= HOLD_S:
                    hv = np.mean(vals, axis=0)
                    if kind == "hold":
                        centre = hv
                    elif kind != "centre":
                        moves[kind[2]] = float(hv[kind[1]] - centre[kind[1]])
                    status = f"{GREEN}OK! {label} done.{RESET}  Next step in {PAUSE_S:.0f} s..."
                    i += 1
                    since, vals, ok_until = None, [], now + PAUSE_S
            else:
                since, vals = None, []
                status = ""
        if now - last_draw >= REDRAW_S or now < ok_until:
            last_draw = now
            out = [CLEAR, f"{BOLD}HAND TEST - {a.sensor}{RESET}  (points {m.az_deg:+.0f} deg left/right, "
                          f"{m.el_deg:+.0f} deg tilt)   frames received: {rd.frames}", ""]
            cur = steps[min(i, len(steps) - 1)]
            if now < ok_until:
                out += [f"  {status}", "", f"  Next: {cur[1] if i < len(steps) else 'done'}"]
            else:
                out += [f"  STEP {i + 1} of {len(steps)}:", f"  {BOLD}{YELLOW}>>> {text.upper()} <<<{RESET}"]
                if since:
                    frac = min(1.0, (now - since) / HOLD_S)
                    out.append(f"  holding: [{'#' * int(frac * 20):<20}]")
                else:
                    out.append("")
            out += [""]
            for j, (lab, _, _) in enumerate(steps):
                mark = f"{GREEN}[x]{RESET}" if j < i else (f"{YELLOW}[>]{RESET}" if j == i else "[ ]")
                out.append(f"   {mark} {j + 1}. {lab}")
            out += ["", "  What the code thinks it sees (your view, looking out):"]
            if r is None:
                out.append(f"    no hand within {NEAR_MM / 10:.0f} cm of {a.sensor}")
            else:
                out.append(f"    hand at {r[2] / 10:.0f} cm:  {r[0]:+.0f} deg right, {r[1]:+.0f} deg up")
            out += ["    " + ln for ln in picture(h, v, None if r is None else r[3], a.res)]
            out.append("  (If the # moves the OPPOSITE way to your hand, that axis is mirrored. Ctrl-C to quit.)")
            sys.stdout.write("\n".join(out) + "\n")
            sys.stdout.flush()
        time.sleep(0.05)

    h_ok = moves["RIGHT"] > 0 and moves["LEFT"] < 0
    h_bad = moves["RIGHT"] < 0 and moves["LEFT"] > 0
    v_ok = moves["UP"] > 0 and moves["DOWN"] < 0
    v_bad = moves["UP"] < 0 and moves["DOWN"] > 0
    if h_ok and v_ok:
        advice = "nothing to change"
    elif h_bad and v_bad:
        advice = f"{a.sensor} is mounted upside down: add 180 to its roll_deg in {a.rig}"
    elif h_bad and v_ok:
        advice = f"toggle flip_h for {a.sensor} in {a.rig}"
    elif v_bad and h_ok:
        advice = f"toggle flip_v for {a.sensor} in {a.rig}"
    else:
        advice = "unclear: run the test again with bigger, steadier moves"
    sys.stdout.write(CLEAR)
    print(f"{BOLD}=== HAND TEST RESULT for {a.sensor} ==={RESET}\n")
    print(f"  left/right: RIGHT moved {moves['RIGHT']:+.1f} deg, LEFT moved {moves['LEFT']:+.1f} deg -> "
          + ("correct" if h_ok else "MIRRORED" if h_bad else "unclear"))
    print(f"  up/down   : UP moved {moves['UP']:+.1f} deg, DOWN moved {moves['DOWN']:+.1f} deg -> "
          + ("correct" if v_ok else "MIRRORED" if v_bad else "unclear"))
    print(f"\n  -> {advice}\n")
    if a.save:
        Path(a.save).write_text(json.dumps({"sensor": a.sensor, "moves": moves,
                                            "h": "ok" if h_ok else "mirrored" if h_bad else "unclear",
                                            "v": "ok" if v_ok else "mirrored" if v_bad else "unclear",
                                            "advice": advice}) + "\n")
        print(f"  (saved to {a.save})")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")

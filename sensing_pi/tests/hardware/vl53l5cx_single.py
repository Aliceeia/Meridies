#!/usr/bin/env python3
"""Bench test for a single VL53L5CX (AITRIP V2 module) on the Pi's I2C bus 1.

Wiring: VIN->3.3V (pin 1), GND->pin 6, SDA->pin 3, SCL->pin 5, LPn->GPIO17 (pin 11), INT unconnected.
LPn is driven high by this script (the module may have no LPn pull-up; floating LPn = I2C off).

Examples (run from sensing_pi/):
  .venv/bin/python tests/hardware/vl53l5cx_single.py                 # live 8x8 grid
  .venv/bin/python tests/hardware/vl53l5cx_single.py --res 4 --hz 20 # 4x4 at 20 Hz
  .venv/bin/python tests/hardware/vl53l5cx_single.py --frames 100 --no-grid --csv wall_1m.csv
"""
import argparse
import csv
import statistics
import sys
import time

import vl53l5cx_ctypes as vl53

VALID = {vl53.STATUS_RANGE_VALID, vl53.STATUS_RANGE_VALID_LARGE_PULSE}  # 5, 9


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, choices=(4, 8), default=8, help="grid size (default 8)")
    p.add_argument("--hz", type=int, help="ranging rate; max 15 at 8x8, 60 at 4x4 (default 15 / 20)")
    p.add_argument("--addr", type=lambda s: int(s, 0), default=vl53.DEFAULT_I2C_ADDRESS, help="I2C address (default 0x29)")
    p.add_argument("--lpn", type=int, default=17, help="BCM GPIO wired to LPn, driven high (default 17; -1 = don't touch)")
    p.add_argument("--frames", type=int, default=0, help="stop after N frames (0 = run until Ctrl-C)")
    p.add_argument("--csv", help="log every frame (t, d0..dN, s0..sN) to this file")
    p.add_argument("--no-grid", action="store_true", help="print one summary line per frame instead of the grid")
    return p.parse_args()


def middle_rows(res):
    """Rows treated as 'head height' for the single reduced distance."""
    return range(res // 4, res - res // 4)  # 8x8 -> rows 2..5, 4x4 -> rows 1..2


def center_zones(res):
    """The central 2x2 zones, used for the wall-accuracy stats."""
    c = res // 2
    return [r * res + col for r in (c - 1, c) for col in (c - 1, c)]


def render_grid(dist, stat, res):
    lines = []
    for r in range(res):
        cells = []
        for c in range(res):
            i = r * res + c
            cells.append(f"{dist[i]:5d}" if stat[i] in VALID else " ----")
        lines.append(" ".join(cells))
    return "\n".join(lines)


def main():
    a = parse_args()
    zones = a.res * a.res
    hz = a.hz or (15 if a.res == 8 else 20)

    if a.lpn >= 0:
        import lgpio
        gpio = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(gpio, a.lpn, 1)  # LPn high = I2C enabled; held until the script exits
        time.sleep(0.02)
        print(f"LPn (GPIO{a.lpn}) driven high", flush=True)

    print(f"Connecting to VL53L5CX at 0x{a.addr:02x} and uploading firmware...", flush=True)
    t0 = time.monotonic()
    try:
        sensor = vl53.VL53L5CX(i2c_addr=a.addr)
    except (OSError, RuntimeError) as e:
        sys.exit(f"FAILED: {e}\nCheck wiring, that I2C is enabled, and that `i2cdetect -y 1` shows {a.addr:02x}.")
    print(f"Init OK in {time.monotonic() - t0:.2f} s", flush=True)

    sensor.set_resolution(zones)
    sensor.set_ranging_frequency_hz(hz)
    sensor.set_target_order(vl53.TARGET_ORDER_CLOSEST)  # nearest obstacle matters, not the brightest
    sensor.start_ranging()

    writer = None
    if a.csv:
        f = open(a.csv, "w", newline="")
        writer = csv.writer(f)
        writer.writerow(["t"] + [f"d{i}" for i in range(zones)] + [f"s{i}" for i in range(zones)])

    rows = middle_rows(a.res)
    centre = center_zones(a.res)
    centre_vals, n_frames, n_invalid = [], 0, 0
    t_start = t_last = time.monotonic()
    rate = 0.0

    try:
        while not a.frames or n_frames < a.frames:
            if not sensor.data_ready():
                time.sleep(0.002)
                continue
            data = sensor.get_data()
            now = time.monotonic()
            dist = list(data.distance_mm[0][:zones])
            stat = list(data.target_status[0][:zones])

            n_frames += 1
            n_invalid += sum(s not in VALID for s in stat)
            centre_vals += [dist[i] for i in centre if stat[i] in VALID]
            rate = 0.8 * rate + 0.2 / (now - t_last) if n_frames > 1 else 0.0
            t_last = now

            mid = [dist[r * a.res + c] for r in rows for c in range(a.res) if stat[r * a.res + c] in VALID]
            nearest = min(mid) if mid else None  # what one entry of {"mm": [...]} would carry

            if writer:
                writer.writerow([f"{now:.4f}"] + dist + stat)

            if a.no_grid:
                print(f"#{n_frames:5d}  {rate:5.1f} Hz  nearest(mid rows)={nearest}  temp={data.silicon_temp_degc}C")
            else:
                sys.stdout.write("\033[H\033[J")
                print(f"VL53L5CX {a.res}x{a.res} @ {hz} Hz set | measured {rate:5.1f} Hz | frame {n_frames} | {data.silicon_temp_degc} C")
                print(f"mm per zone (---- = invalid status); mid rows {rows.start}-{rows.stop - 1} nearest: {nearest}\n")
                print(render_grid(dist, stat, a.res), flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sensor.stop_ranging()
        if writer:
            f.close()

    elapsed = time.monotonic() - t_start
    print(f"\n--- {n_frames} frames in {elapsed:.1f} s ({n_frames / elapsed if elapsed else 0:.1f} Hz avg)")
    if n_frames:
        print(f"invalid zones: {100 * n_invalid / (n_frames * zones):.1f}%")
    if len(centre_vals) > 1:
        print(f"centre 2x2: mean {statistics.mean(centre_vals):.1f} mm, std {statistics.stdev(centre_vals):.1f} mm, "
              f"n={len(centre_vals)}")
    if a.csv:
        print(f"logged to {a.csv}")


if __name__ == "__main__":
    main()

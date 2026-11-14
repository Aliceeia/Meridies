#!/usr/bin/env python3
"""Record six VL53L5CX sensors (2 x 3 grid) as one combined frame. Pi only.

Brings the sensors up one at a time with their LPn lines, moves them to 0x30-0x35,
then reads each one when its INT line fires (driver: sensing_pi/sensors/tof6.py).

Run from sensing_pi/:
  .venv/bin/python tests/multi6/tof6_record.py --csv rec.csv --serve 5557
"""
import argparse
import csv
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))  # sensing_pi/
import tof6_common as C  # noqa: E402
from netio import JsonLineServer  # noqa: E402
from sensors.tof6 import Tof6  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, choices=(4, 8), default=8)
    p.add_argument("--hz", type=int, help="per-sensor rate (default 10 at 8x8, 15 at 4x4)")
    p.add_argument("--frames", type=int, default=0, help="stop after N combined frames (0 = until Ctrl-C)")
    p.add_argument("--csv", help="write every combined frame to this CSV")
    p.add_argument("--serve", type=int, metavar="PORT", help="stream frames as JSON lines over TCP on this port")
    p.add_argument("--no-int", action="store_true", help="ignore INT pins and poll every sensor over I2C")
    return p.parse_args()


def main():
    a = parse_args()
    log = lambda msg: print(msg, flush=True)  # noqa: E731
    tof = Tof6(res=a.res, hz=a.hz, use_int=not a.no_int, log=log)

    writer = csv_file = None
    if a.csv:
        csv_file = open(a.csv, "w", newline="")
        csv_file.write(C.csv_meta_line(a.res, tof.hz))
        writer = csv.writer(csv_file)
        writer.writerow(C.csv_header(a.res))
    server = JsonLineServer(a.serve, "streaming", log) if a.serve else None

    tof.start()
    log(f"ranging {a.res}x{a.res} @ {tof.hz} Hz per sensor, INT {'off (polling)' if a.no_int else 'on'}. Ctrl-C to stop.")
    n_frames = 0
    t_start = last_report = time.monotonic()
    try:
        for frame in tof.frames():
            if writer:
                writer.writerow(C.frame_to_row(frame))
            if server:
                server.send(frame)
            n_frames += 1
            now = time.monotonic()
            if now - last_report >= 2.0:
                el = now - t_start
                per = "  ".join(f"S{k + 1} {tof.reads[k] / el:4.1f}Hz int={tof.int_edges[k]} err={tof.errors[k]}"
                                for k in range(C.N_SENSORS))
                log(f"[{el:5.0f}s] frames={n_frames} ({n_frames / el:4.1f}/s) | {per}")
                last_report = now
            if a.frames and n_frames >= a.frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        tof.close()
        if csv_file:
            csv_file.close()
        if server:
            server.close()

    el = time.monotonic() - t_start
    print(f"\n--- {n_frames} combined frames in {el:.1f} s")
    for k in range(C.N_SENSORS):
        warn = "  <- no INT edges: check INT wire" if not a.no_int and tof.int_edges[k] == 0 else ""
        print(f"S{k + 1}: {tof.reads[k]} reads, {tof.errors[k]} errors, {tof.int_edges[k]} INT edges{warn}")
    if a.csv:
        print(f"saved {a.csv}")


if __name__ == "__main__":
    main()

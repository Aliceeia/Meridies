#!/usr/bin/env python3
"""Learn which zones see the cap itself (brim, hair, cables) and mask them out.

HOW TO RECORD: wear the cap, stand in OPEN SPACE (nothing within ~1 m: away from walls,
desks and people), look around slowly, for ~20 s. Then, from sensing_pi/:

  # from a running raw stream (main.py, port 5557)
  .venv/bin/python tools/learn_selfmask.py --rig config/cap_mounts.json --source tcp:localhost:5557 --secs 20
  # or from a recording made the same way
  .venv/bin/python tools/learn_selfmask.py --rig config/cap_mounts.json --csv open_space.csv

It writes config/selfmask_<rig>.json (e.g. selfmask_cap_mounts.json); the object stream loads it
automatically. Re-learn whenever a sensor is moved. Delete the file to turn the mask off.
"""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from geometry import load_mounts  # noqa: E402
from objects.quality import learn_mask, mask_path_for  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rig", default="config/cap_mounts.json")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", help="recording made in open space with the cap worn")
    src.add_argument("--source", help="live stream, e.g. tcp:localhost:5557")
    p.add_argument("--secs", type=float, default=20, help="how long to record from --source")
    p.add_argument("--near-mm", type=float, default=600, help="anything closer than this counts as 'self' (default 600)")
    p.add_argument("--min-frac", type=float, default=0.2, help="zone must read near in this share of frames (default 0.2)")
    p.add_argument("--margin-mm", type=float, default=30, help="added to the learned threshold (default 30)")
    p.add_argument("--out", help="output file (default config/selfmask_<rig json name>.json)")
    p.add_argument("--dry-run", action="store_true", help="print the result, don't write the file")
    a = p.parse_args()

    rig_path = Path(a.rig) if Path(a.rig).exists() else ROOT / a.rig
    rig = load_mounts(rig_path)
    ids = [m.id for m in rig.sensors]

    if a.csv:
        from geometry.calibrate import load_tof6_csv
        frames = load_tof6_csv(a.csv)
    else:
        from streams.sources import open_source
        frames, t0 = [], time.monotonic()
        print(f"recording {a.secs:g} s from {a.source}: keep the cap on, stay in open space...", flush=True)
        for f in open_source(a.source, rig):
            frames.append(f)
            if time.monotonic() - t0 >= a.secs:
                break
    print(f"{len(frames)} frames")

    zones, stats = learn_mask(frames, ids, a.near_mm, a.min_frac, a.margin_mm)
    for sid in ids:
        m, n = stats[sid]
        thr = [v for v in zones[sid] if v]
        extra = f", thresholds {min(thr)}-{max(thr)} mm" if thr else ""
        warn = "   <- over half masked: is something covering it / was it really open space?" if m > n / 2 else ""
        print(f"  {sid}: {m:2d}/{n} zones masked{extra}{warn}")

    out = Path(a.out) if a.out else mask_path_for(rig)
    doc = {"_note": "Learned self-mask: per zone, readings at or below this many mm are treated as the cap itself "
                    "(0 = not masked). Made by tools/learn_selfmask.py; delete this file to disable.",
           "rig": rig_path.name, "res": frames[0]["res"],
           "created": datetime.datetime.now().isoformat(timespec="seconds"),
           "frames": len(frames), "near_mm": a.near_mm, "min_frac": a.min_frac, "margin_mm": a.margin_mm,
           "zones": zones}
    if a.dry_run:
        print("(dry run, not written)")
    else:
        out.write_text(json.dumps(doc, indent=1) + "\n")
        print(f"wrote {out}")


if __name__ == "__main__":
    main()

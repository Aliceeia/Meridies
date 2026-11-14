#!/usr/bin/env python3
"""ToF frames -> objects -> top-3 warning list for the audio engine (ZMQ PUB tcp://*:5556).

Run from sensing_pi/:
  .venv/bin/python run_objects.py --rig config/flat_mounts.json --source tcp:localhost:5557
  .venv/bin/python run_objects.py --rig config/flat_mounts.json --source csv:rec.csv --no-pub
  .venv/bin/python run_objects.py --rig config/cap_mounts.json  --source sim:corridor
Laptop debug view: python tools/objects_view.py --live <pi>:5558
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geometry import LevelHead, MPU6050Source, load_mounts  # noqa: E402
from objects import ObjectPipeline, load_cfg  # noqa: E402
from sources import open_source  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rig", default="config/flat_mounts.json", help="mount config (flat_mounts.json / cap_mounts.json)")
    p.add_argument("--cfg", help="pipeline settings (default config/objects.json)")
    p.add_argument("--source", default="tcp:localhost:5557", help="live | tcp:HOST:PORT | csv:FILE | sim:SCENE")
    p.add_argument("--res", type=int, choices=(4, 8), default=8, help="for live/sim sources")
    p.add_argument("--hz", type=int, default=10, help="for live/sim sources")
    p.add_argument("--imu", choices=("level", "mpu6050"), default="level")
    p.add_argument("--no-pub", action="store_true", help="don't publish to the audio engine")
    p.add_argument("--debug-port", type=int, help="TCP JSON debug stream port (default from cfg, 0 = off)")
    p.add_argument("--fast", action="store_true", help="csv/sim: don't wait for real time")
    p.add_argument("--frames", type=int, default=0, help="stop after N frames")
    p.add_argument("--quiet", action="store_true", help="no per-frame printout")
    return p.parse_args()


def main():
    a = parse_args()
    here = Path(__file__).resolve().parent
    rig = load_mounts(a.rig if Path(a.rig).exists() else here / a.rig)
    cfg = load_cfg(a.cfg, rig=rig)
    orientation = LevelHead() if a.imu == "level" else MPU6050Source()
    pipe = ObjectPipeline(rig, cfg, orientation)

    audio = debug = None
    if not a.no_pub:
        from objects.publish import AudioPublisher
        audio = AudioPublisher(cfg["audio_endpoint"])
        print(f"publishing warnings on {cfg['audio_endpoint']}", flush=True)
    dport = cfg["debug_port"] if a.debug_port is None else a.debug_port
    if dport:
        from objects.publish import DebugPublisher
        debug = DebugPublisher(dport, log=lambda m: print(m, flush=True))

    n, t_start = 0, time.monotonic()
    try:
        for frame in open_source(a.source, rig, a.res, a.hz, realtime=not a.fast):
            objs, warnings = pipe.step(frame)
            if audio:
                audio.send(warnings)
            if debug:
                debug.send(frame["t"], objs, warnings, pipe.last_scan)
            n += 1
            if not a.quiet:
                ws = "  ".join(f"{w['id']}:{w['kind']} az{w['az']:+.0f} {w['dist']:.2f}m {w['level']}" for w in warnings)
                print(f"#{n:5d} objs={len(objs):2d} pts={len(pipe.last_scan):3d} | {ws or '(silent)'}", flush=True)
            if a.frames and n >= a.frames:
                break
    except KeyboardInterrupt:
        pass
    finally:
        if audio:
            audio.send([])
            audio.close()
        if debug:
            debug.close()
    el = time.monotonic() - t_start
    print(f"\n--- {n} frames in {el:.1f} s ({n / el if el else 0:.1f}/s)")


if __name__ == "__main__":
    main()

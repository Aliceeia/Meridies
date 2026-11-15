"""Frame sources for run_objects.py. Each yields tof6 frames {"t", "res", "d", "s"}.

  live                 the six sensors directly (Pi only; don't run alongside tof6_record.py)
  tcp:HOST:PORT        a running `tof6_record.py --serve PORT` (no I2C conflict)
  csv:FILE             replay a tof6_record.py CSV in real time (add --fast to skip waiting)
  sim:SCENE            ray-cast demo scene through the rig (wall, pole_sweep, approach, pole_wall, corridor)
"""
from __future__ import annotations

import json
import socket
import time


def open_source(spec: str, rig=None, res: int = 8, hz: int = 10, realtime: bool = True):
    kind, _, arg = spec.partition(":")
    if kind == "live":
        return _live(res, hz)
    if kind == "tcp":
        host, _, port = arg.rpartition(":")
        return _tcp(host or "localhost", int(port or 5557))
    if kind == "csv":
        return _csv(arg, realtime)
    if kind == "sim":
        return _sim(arg, rig, res, hz, realtime)
    raise ValueError(f"unknown source {spec!r}")


def _live(res, hz):
    from sensors.tof6 import Tof6
    with Tof6(res=res, hz=hz, log=lambda m: print(m, flush=True), allow_missing=True) as tof:
        yield from tof.frames()


def _tcp(host, port):
    sock = socket.create_connection((host, port), timeout=5)
    sock.settimeout(None)
    print(f"reading frames from {host}:{port}", flush=True)
    for line in sock.makefile("r"):
        yield json.loads(line)


def _csv(path, realtime):
    from geometry.calibrate import load_tof6_csv
    frames = load_tof6_csv(path)
    t0_rec, t0 = frames[0]["t"], time.monotonic()
    for f in frames:
        if realtime:
            wait = (f["t"] - t0_rec) - (time.monotonic() - t0)
            if wait > 0:
                time.sleep(wait)
        yield f


def _sim(scene, rig, res, hz, realtime):
    from sim.scene import render, scene_shapes
    import numpy as np
    rng = np.random.default_rng(0)
    t0 = time.time()
    n = 0
    while True:
        t = n / hz
        yield render(rig, scene_shapes(scene, t), res=res, t=t0 + t, noise_mm=8, rng=rng)
        n += 1
        if realtime:
            wait = t0 + (n / hz) - time.time()
            if wait > 0:
                time.sleep(wait)

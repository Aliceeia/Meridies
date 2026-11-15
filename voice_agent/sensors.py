"""Laptop-side inputs: camera snapshot (from the Pi, a local webcam, or a file) and LiDAR obstacle warnings.

The obstacle feed is a read-only ZMQ SUB to sensing_pi's port 5556 (never binds or publishes).
Message: JSON list, <=3 entries nearest first: {"id","az","dist","level"}, az 0=ahead, -left, +right.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import config


class Camera:
    """source: http(s)://... snapshot URL | 'webcam[:N]' (laptop camera, for testing) | path to a .jpg"""

    def __init__(self, source: str = config.CAMERA_URL):
        self.source = source

    def snapshot(self) -> bytes:
        s = self.source
        if s.startswith("http"):
            try:
                with urllib.request.urlopen(s, timeout=5) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 503:
                    raise RuntimeError("the cap camera is connected to the Pi but not sending pictures yet") from None
                raise
            except urllib.error.URLError:
                raise RuntimeError("the cap camera server can't be reached (is camera_server.py running on the Pi?)") from None
        if s.startswith("webcam"):
            return _webcam_frame(s.partition(":")[2] or "0")
        return Path(s).read_bytes()


def _webcam_frame(idx: str) -> bytes:
    import platform
    sysname = platform.system()
    if sysname == "Darwin":
        inp = ["-f", "avfoundation", "-framerate", "30", "-i", f"{idx}:none"]
    elif sysname == "Windows":
        inp = ["-f", "dshow", "-i", f"video={idx}"]
    else:
        inp = ["-f", "v4l2", "-i", f"/dev/video{idx}"]
    # skip a few frames so auto-exposure settles
    out = subprocess.run(["ffmpeg", "-loglevel", "error", *inp, "-vf", "select=gte(n\\,5),scale=1024:-2",
                          "-frames:v", "1", "-f", "image2", "-vcodec", "mjpeg", "-"], capture_output=True, timeout=15)
    if not out.stdout:
        raise RuntimeError(f"webcam capture failed: {out.stderr.decode()[:200]}")
    return out.stdout


class Obstacles:
    """Latest LiDAR warnings from the hat. Silent no-op if pyzmq is missing or the Pi isn't reachable."""

    def __init__(self, endpoint: str | None = config.OBSTACLE_ENDPOINT):
        self.latest: list[dict] = []
        self.t = 0.0
        self.listeners = []          # callables(list) on every message
        if endpoint:
            threading.Thread(target=self._loop, args=(endpoint,), daemon=True).start()

    def _loop(self, endpoint):
        try:
            import zmq
        except ImportError:
            print("[obstacles] pyzmq not installed; LiDAR context disabled")
            return
        sock = zmq.Context.instance().socket(zmq.SUB)
        sock.setsockopt(zmq.CONFLATE, 1)           # only the newest message matters
        sock.setsockopt_string(zmq.SUBSCRIBE, "")
        sock.connect(endpoint)
        while True:
            try:
                msg = json.loads(sock.recv())
                if isinstance(msg, list):
                    self.latest, self.t = msg, time.time()
                    for f in self.listeners:
                        f(msg)
            except Exception as e:
                print("[obstacles]", e)
                time.sleep(0.5)

    def current(self, max_age: float = 1.0) -> list[dict]:
        return self.latest if time.time() - self.t < max_age else []

    @property
    def connected(self) -> bool:
        return time.time() - self.t < 3

    def summary(self) -> str:
        if not self.connected:
            return "LiDAR: no data (hat not connected)."
        obs = self.current()
        if not obs:
            return "LiDAR: nothing within about 1.5 metres."
        return "LiDAR: " + "; ".join(
            f"{o.get('level', 'warn')} obstacle {direction(o['az'])}, {o['dist']:.1f} m" for o in obs) + "."


def direction(az: float) -> str:
    a = abs(az)
    side = "left" if az < 0 else "right"
    if a <= 12:
        return "straight ahead"
    if a <= 35:
        return f"slightly {side}"
    if a <= 65:
        return f"ahead to the {side}"
    if a <= 110:
        return f"on your {side}"
    return f"behind you on the {side}"

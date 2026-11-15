#!/usr/bin/env python3
"""Run the whole sensing side with one command. From sensing_pi/:

    .venv/bin/python main.py                       # sensors -> raw stream :5557 -> objects :5556 (audio) + :5558 (debug)
    .venv/bin/python main.py --csv run.csv         # same, and record the raw frames
    .venv/bin/python main.py --rig config/flat_mounts.json
    .venv/bin/python main.py --raw-only            # only the raw sensor stream on :5557
    .venv/bin/python main.py --sim corridor        # no hardware: simulated scene -> objects
    .venv/bin/python main.py --replay run.csv      # replay a recording -> objects

Ctrl-C stops everything. It starts two programs and shows their output:
    [raw] python -m sensors.record   (the six sensors, raw frames on TCP :5557)
    [obj] python -m objects.run      (objects -> warnings on ZMQ :5556, debug on TCP :5558)
Either can still be run on its own; see their --help.
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_PORT = 5557


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rig", default="config/cap_mounts.json", help="sensor layout (default config/cap_mounts.json)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--raw-only", action="store_true", help="only stream raw frames, no object detection")
    mode.add_argument("--sim", metavar="SCENE", help="no sensors: simulated scene (wall, pole_sweep, approach, pole_wall, corridor)")
    mode.add_argument("--replay", metavar="CSV", help="no sensors: replay a recording")
    p.add_argument("--csv", help="also record the raw frames to this CSV (live mode)")
    p.add_argument("--require-all", action="store_true", help="stop if a sensor is missing (default: carry on without it)")
    p.add_argument("--no-audio", action="store_true", help="don't publish warnings on :5556")
    p.add_argument("--verbose", action="store_true", help="print every object frame")
    p.add_argument("--no-quality", action="store_true", help="object stream without the sigma/signal filter and self-mask")
    p.add_argument("--no-body", action="store_true", help="object stream without the body zone")
    return p.parse_args()


def local_ip() -> str:
    """This Pi's LAN address (no packet is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "<pi-address>"


def _child_setup():
    """In the child, before it starts: own process group (so we decide how it is stopped), and
    ask the kernel to SIGTERM it if main.py itself dies, so nothing is left holding the ports."""
    os.setsid()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)   # PR_SET_PDEATHSIG = 1
    except OSError:
        pass


class Child:
    """A child program whose output is shown with a prefix."""

    def __init__(self, tag: str, args: list[str]):
        self.tag = tag
        self.lines: list[str] = []
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1, preexec_fn=_child_setup)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            line = line.rstrip("\n")
            self.lines.append(line)
            print(f"[{self.tag}] {line}", flush=True)

    def running(self) -> bool:
        return self.proc.poll() is None

    def wait_for(self, text: str, timeout: float) -> bool:
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if any(text in ln for ln in self.lines):
                return True
            if not self.running():
                return False
            time.sleep(0.1)
        return False

    def stop(self, first=signal.SIGINT, grace: float = 3.0):
        """Ask nicely, then terminate, then kill. (The sensor library can swallow SIGINT while it's
        inside an I2C call, so the raw stream may need SIGTERM.)"""
        for sig, wait in ((first, grace), (signal.SIGTERM, 2.0), (signal.SIGKILL, 1.0)):
            if not self.running():
                return
            try:
                os.killpg(self.proc.pid, sig)
            except ProcessLookupError:
                return
            try:
                self.proc.wait(wait)
            except subprocess.TimeoutExpired:
                pass


def main():
    a = parse_args()
    obj_args = ["-m", "objects.run", "--rig", a.rig]
    if a.no_audio:
        obj_args.append("--no-pub")
    if not a.verbose:
        obj_args.append("--quiet")
    if a.no_quality:
        obj_args.append("--no-quality")
    if a.no_body:
        obj_args.append("--no-body")

    children: list[Child] = []
    stopping = {"flag": False}

    def shutdown(*_):
        stopping["flag"] = True

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    ip = local_ip()
    try:
        if a.sim or a.replay:
            src = f"sim:{a.sim}" if a.sim else f"csv:{a.replay}"
            children.append(Child("obj", obj_args + ["--source", src]))
        else:
            raw_args = ["-m", "sensors.record", "--serve", str(RAW_PORT)]
            if not a.require_all:
                raw_args.append("--allow-missing")
            if a.csv:
                raw_args += ["--csv", a.csv]
            raw = Child("raw", raw_args)
            children.append(raw)
            print("[main] starting the sensors (about 10 s)...", flush=True)
            if not raw.wait_for("ranging", timeout=60):
                print("[main] the raw stream did not start; see the [raw] lines above", flush=True)
                return 1
            if not a.raw_only:
                children.append(Child("obj", obj_args + ["--source", f"tcp:localhost:{RAW_PORT}"]))

        print("[main] running. Ctrl-C to stop.", flush=True)
        if not (a.sim or a.replay):
            print(f"[main]   raw frames     tcp {ip}:{RAW_PORT}   (laptop: python tools/tof6_view.py --live {ip}:{RAW_PORT})",
                  flush=True)
        if not a.raw_only:
            if not a.no_audio:
                print(f"[main]   audio warnings zmq tcp://{ip}:5556   (laptop: python engine.py --endpoint tcp://{ip}:5556)",
                      flush=True)
            print(f"[main]   object debug   tcp {ip}:5558   (laptop: python tools/objects_view.py --live {ip}:5558)",
                  flush=True)

        while not stopping["flag"]:
            dead = [c for c in children if not c.running()]
            if dead:
                print(f"[main] {dead[0].tag} stopped (exit {dead[0].proc.returncode}); stopping the rest", flush=True)
                break
            time.sleep(0.2)
    finally:
        print("[main] stopping...", flush=True)
        for c in reversed(children):          # objects first (it sends a final empty list to audio), then sensors
            c.stop()
        time.sleep(0.2)
        print("[main] stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

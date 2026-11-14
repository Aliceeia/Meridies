#!/usr/bin/env python3
"""Grayscale view of the 6 x VL53L5CX mosaic. Runs on a laptop (needs numpy + matplotlib).

Live from the Pi:   python tests/multi6/tof6_view.py --live raspberrypi.local:5557
Play a recording:   python tests/multi6/tof6_view.py --file rec.csv [--speed 2]

Near = white, far = black, invalid zone = dark red.
Playback keys: space = pause/resume, left/right = step one frame while paused.
"""
import argparse
import csv
import json
import socket
import sys
import threading
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tof6_common as C  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--live", metavar="HOST[:PORT]", help="connect to tof6_record.py --serve (default port 5557)")
    src.add_argument("--file", help="CSV written by tof6_record.py --csv")
    p.add_argument("--max-mm", type=int, default=2000, help="distance shown as black (default 2000)")
    p.add_argument("--tile", choices=("none", "flipx", "flipy", "rot180"), default="none",
                   help="re-orient every 8x8 tile to match how the boards are mounted")
    p.add_argument("--speed", type=float, default=1.0, help="playback speed for --file")
    return p.parse_args()


class Display:
    def __init__(self, res, max_mm, tile):
        self.tile = tile
        rows, cols = len(C.LAYOUT), len(C.LAYOUT[0])
        cmap = plt.get_cmap("gray_r").copy()
        cmap.set_bad("#5a0000")
        self.fig, self.ax = plt.subplots(figsize=(cols * 3, rows * 3 + 0.6))
        blank = np.ma.masked_all((rows * res, cols * res))
        self.im = self.ax.imshow(blank, cmap=cmap, vmin=0, vmax=max_mm, interpolation="nearest")
        self.fig.colorbar(self.im, ax=self.ax, label="distance (mm)", fraction=0.03)
        for x in range(1, cols):
            self.ax.axvline(x * res - 0.5, color="tab:cyan", lw=1.5)
        for y in range(1, rows):
            self.ax.axhline(y * res - 0.5, color="tab:cyan", lw=1.5)
        for r, row in enumerate(C.LAYOUT):
            for c, sensor in enumerate(row):
                self.ax.text(c * res - 0.3, r * res - 0.1, f"S{sensor}", color="tab:cyan",
                             fontsize=9, va="top", ha="left", fontweight="bold")
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.title = self.ax.set_title("waiting for data...")
        self.fig.tight_layout()

    def show(self, frame, label):
        self.im.set_data(C.mosaic(frame, self.tile))
        self.title.set_text(label)

    def alive(self):
        return plt.fignum_exists(self.fig.number)


def run_live(a):
    host, _, port = a.live.partition(":")
    port = int(port or 5557)
    try:
        sock = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        sys.exit(f"cannot connect to {host}:{port}: {e}")
    sock.settimeout(None)
    print(f"connected to {host}:{port}")

    latest = {"frame": None, "n": 0, "err": None}

    def reader():
        try:
            for line in sock.makefile("r"):
                latest["frame"] = json.loads(line)
                latest["n"] += 1
            latest["err"] = "recorder closed the connection"
        except (OSError, ValueError) as e:
            latest["err"] = str(e)

    threading.Thread(target=reader, daemon=True).start()

    disp = None
    shown, t_fps, n_fps, fps = 0, time.monotonic(), 0, 0.0
    plt.ion()
    while True:
        f = latest["frame"]
        if f is not None and latest["n"] != shown:
            if disp is None:
                disp = Display(f["res"], a.max_mm, a.tile)
                plt.show(block=False)
            n_fps += latest["n"] - shown
            shown = latest["n"]
            now = time.monotonic()
            if now - t_fps >= 1.0:
                fps, n_fps, t_fps = n_fps / (now - t_fps), 0, now
            disp.show(f, f"live {host}  |  {fps:4.1f} frames/s  |  frame {shown}")
        if latest["err"]:
            print(latest["err"])
            break
        if disp is not None and not disp.alive():
            break
        plt.pause(0.01)
    sock.close()
    if disp is not None and disp.alive():
        plt.ioff()
        plt.show()


def run_file(a):
    with open(a.file, newline="") as fh:
        meta_line = fh.readline()
        if not meta_line.startswith("# tof6"):
            sys.exit(f"{a.file} does not look like a tof6_record.py CSV")
        res = C.parse_meta(meta_line)["res"]
        rd = csv.reader(fh)
        next(rd)  # header
        frames = [C.row_to_frame(row, res) for row in rd if row]
    if not frames:
        sys.exit("no frames in file")
    print(f"{len(frames)} frames, {res}x{res}, {frames[-1]['t'] - frames[0]['t']:.1f} s")

    disp = Display(res, a.max_mm, a.tile)
    state = {"i": 0, "paused": False}

    def on_key(ev):
        if ev.key == " ":
            state["paused"] = not state["paused"]
        elif state["paused"] and ev.key in ("right", "left"):
            state["i"] = max(0, min(len(frames) - 1, state["i"] + (1 if ev.key == "right" else -1)))
    disp.fig.canvas.mpl_connect("key_press_event", on_key)

    plt.ion()
    plt.show(block=False)
    t0_rec = frames[0]["t"]
    t0_wall = time.monotonic()
    while disp.alive():
        i = state["i"]
        f = frames[i]
        tag = "  [paused]" if state["paused"] else ""
        disp.show(f, f"{Path(a.file).name}  |  frame {i + 1}/{len(frames)}  |  t={f['t'] - t0_rec:6.2f} s{tag}")
        plt.pause(0.01)
        if state["paused"]:
            t0_wall = time.monotonic() - (frames[state["i"]]["t"] - t0_rec) / a.speed
            continue
        target = (time.monotonic() - t0_wall) * a.speed + t0_rec
        while state["i"] < len(frames) - 1 and frames[state["i"] + 1]["t"] <= target:
            state["i"] += 1
        if state["i"] == len(frames) - 1:
            state["paused"] = True  # stop on the last frame


def main():
    a = parse_args()
    if a.live:
        run_live(a)
    else:
        run_file(a)


if __name__ == "__main__":
    main()

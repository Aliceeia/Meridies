#!/usr/bin/env python3
"""Top-down debug view of the object pipeline. Runs on a laptop (needs numpy + matplotlib, no zmq).

  python tools/objects_view.py --live <pi-address>:5558      # debug stream from main.py

Head at the origin, forward = up. Grey dots = scan points, colour = object kind,
thick outline + label = the (up to 3) warnings sent to the audio engine.
"""
import argparse
import json
import math
import socket
import sys
import threading
import time

import matplotlib.pyplot as plt

KIND_COLOR = {"narrow": "tab:red", "wide": "tab:orange", "wall_facing": "tab:purple",
              "wall_oblique": "tab:pink", "wall_parallel": "tab:blue"}


def to_xy(az_deg, dist):
    a = math.radians(az_deg)
    return dist * math.sin(a), dist * math.cos(a)   # screen x = right, screen y = forward


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", required=True, metavar="HOST[:PORT]")
    p.add_argument("--range", type=float, default=3.0, help="plot radius, m")
    a = p.parse_args()
    host, _, port = a.live.partition(":")
    try:
        sock = socket.create_connection((host, int(port or 5558)), timeout=5)
    except OSError as e:
        sys.exit(f"cannot connect: {e}")
    sock.settimeout(None)

    latest = {"msg": None, "n": 0, "err": None}

    def reader():
        try:
            for line in sock.makefile("r"):
                latest["msg"] = json.loads(line)
                latest["n"] += 1
            latest["err"] = "stream closed"
        except (OSError, ValueError) as e:
            latest["err"] = str(e)

    threading.Thread(target=reader, daemon=True).start()

    plt.ion()
    fig, ax = plt.subplots(figsize=(7, 7))
    shown, t_fps, n_fps, fps = 0, time.monotonic(), 0, 0.0
    while plt.fignum_exists(fig.number):
        if latest["err"]:
            print(latest["err"])
            break
        m = latest["msg"]
        if m is None or latest["n"] == shown:
            plt.pause(0.02)
            continue
        n_fps += latest["n"] - shown
        shown = latest["n"]
        now = time.monotonic()
        if now - t_fps >= 1:
            fps, n_fps, t_fps = n_fps / (now - t_fps), 0, now

        ax.clear()
        R = a.range
        for r in (0.5, 1.0, 1.5, 2.0, 3.0):
            if r <= R:
                ax.add_patch(plt.Circle((0, 0), r, fill=False, ls=":", lw=0.6, color="grey"))
                ax.text(0.02, r, f"{r} m", fontsize=7, color="grey")
        for deg in (-90, -45, 0, 45, 90):
            x, y = to_xy(deg, R)
            ax.plot([0, x], [0, y], lw=0.4, color="lightgrey")
        ax.plot(0, 0, marker="^", ms=12, color="black")

        if m["points"]:
            xs, ys = zip(*(to_xy(az, d) for az, d, *_ in m["points"]))
            ax.scatter(xs, ys, s=8, color="grey", zorder=2)
        warned = {w["id"]: w for w in m["warnings"]}
        for o in m["objects"]:
            col = KIND_COLOR.get(o["kind"], "black")
            # span arc at the nearest distance
            steps = max(2, int(abs(o["az_r"] - o["az_l"]) / 2) + 1)
            arc = [to_xy(o["az_l"] + (o["az_r"] - o["az_l"]) * i / (steps - 1), o["dist"]) for i in range(steps)]
            ax.plot(*zip(*arc), color=col, lw=3 if o["id"] in warned else 1.5, alpha=0.8)
            if "wall_line" in o:
                (x1, y1), (x2, y2) = o["wall_line"]          # head frame: x fwd, y right
                ax.plot([y1, y2], [x1, x2], color=col, ls="--", lw=1)
            nx, ny = to_xy(o["az"], o["dist"])
            ax.plot(nx, ny, "o", color=col, ms=9 if o["id"] in warned else 5,
                    mec="black" if o["id"] in warned else col, mew=2 if o["id"] in warned else 0)
            label = f"{o['id']} {o['kind']}\n{o['dist']:.2f} m"
            if o["id"] in warned:
                label += f"  [{warned[o['id']]['level']}]"
                if "ttc_s" in o:
                    label += f" ttc {o['ttc_s']:.1f}s"
            ax.annotate(label, (nx, ny), xytext=(6, 6), textcoords="offset points", fontsize=8,
                        fontweight="bold" if o["id"] in warned else "normal")
        ax.set_xlim(-R, R)
        ax.set_ylim(-0.3, R)
        ax.set_aspect("equal")
        ax.set_xlabel("right (m)")
        ax.set_ylabel("forward (m)")
        ax.set_title(f"{len(m['objects'])} objects, {len(m['warnings'])} sent to audio | {fps:4.1f} frames/s")
        plt.pause(0.01)
    sock.close()


if __name__ == "__main__":
    main()

"""Which way does the showcase `geocoordinates` transform apply? Overlay the wall graph on a
Vexcel ortho under each candidate and look. -> connect/spike/out/interior_georef_check.png"""
import json
import math
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from georef import vexcel_ortho, CACHE  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
fp = json.loads((ROOT / "connect" / "data" / "interior_floorplan.json").read_text())
g = fp["geocoordinates"]
q = g["rotation"]
theta = 2 * math.atan2(q["z"], q["w"])  # rotation about +Z
t = np.array([g["translation"]["x"], g["translation"]["y"]])
R = lambda a: np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
HALF = 25
img = vexcel_ortho(g["latitude"], g["longitude"], HALF, 1000, CACHE / f"vexcel_{fp['building']['model_id']}_{HALF}m.jpg")

cands = {
    "R p + t": lambda p: p @ R(theta).T + t,
    "R (p + t)": lambda p: (p + t) @ R(theta).T,
    "R^-1 (p - t)": lambda p: (p - t) @ R(-theta).T,
    "R^-1 p - t": lambda p: p @ R(-theta).T - t,
}
fig, axes = plt.subplots(1, 4, figsize=(24, 6.5))
for ax, (name, f) in zip(axes, cands.items()):
    ax.imshow(img, extent=[-HALF, HALF, -HALF, HALF])
    for w in fp["walls"]:
        a, b = f(np.array([w["a"], w["b"]]))
        ax.plot([a[0], b[0]], [a[1], b[1]], c="cyan", lw=1.5)
    door = f(np.array([[2.8, 0.0]]))[0]   # front door, from the wall graph
    porch = f(np.array([[3.44, -0.09]]))[0]
    ax.plot(*door, "s", c="lime"); ax.plot(*porch, "o", c="red")
    ax.set_title(f"{name}  (theta {math.degrees(theta):.1f} deg)"); ax.set_xlim(-HALF, HALF); ax.set_ylim(-HALF, HALF)
out = Path(__file__).resolve().parent / "out"
fig.savefig(out / "interior_georef_check.png", dpi=70, bbox_inches="tight")
print("theta", math.degrees(theta), "t", t, "->", out / "interior_georef_check.png")

# zoom on the chosen candidate: ortho alone vs ortho + walls
f = cands[sys.argv[1] if len(sys.argv) > 1 else "R p + t"]
fig, axes = plt.subplots(1, 2, figsize=(16, 9))
for ax, show in zip(axes, (False, True)):
    ax.imshow(img, extent=[-HALF, HALF, -HALF, HALF])
    if show:
        for w in fp["walls"]:
            p = f(np.array([w["a"], w["b"]]))
            ax.plot(p[:, 0], p[:, 1], c="cyan", lw=2)
        ax.plot(*f(np.array([[3.44, -0.09]]))[0], "o", c="red", ms=10)
    ax.set_xlim(-14, 12); ax.set_ylim(-14, 16)
fig.savefig(out / "interior_georef_zoom.png", dpi=60, bbox_inches="tight")

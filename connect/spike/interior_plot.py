"""Debug plot of connect/data/interior_floorplan.json -> connect/spike/out/interior_map.png"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
fp = json.loads((ROOT / "connect" / "data" / "interior_floorplan.json").read_text())
fig, ax = plt.subplots(figsize=(14, 8))
for r in fp["rooms"]:
    xs, ys = zip(*(r["polygon"] + r["polygon"][:1]))
    ax.fill(xs, ys, alpha=0.15)
    cx, cy = sum(xs[:-1]) / (len(xs) - 1), sum(ys[:-1]) / (len(ys) - 1)
    ax.text(cx, cy, r["label"], fontsize=9, ha="center", alpha=0.7)
for w in fp["walls"]:
    ax.plot([w["a"][0], w["b"][0]], [w["a"][1], w["b"][1]], c="k" if w["type"] == "wall" else "0.7", lw=2 if w["type"] == "wall" else 1)
for o in fp["openings"]:
    ax.plot(*o["center"], "s" if o["type"] == "doorway" else "^", c="tab:green" if o["type"] == "doorway" else "tab:blue", ms=6)
S = {s["index"]: s for s in fp["sweeps"]}
for s in fp["sweeps"]:
    for n in s["neighbours"]:
        if n > s["index"]:
            ax.plot([s["position"]["x"], S[n]["position"]["x"]], [s["position"]["y"], S[n]["position"]["y"]], c="tab:orange", lw=0.5, alpha=0.5)
for s in fp["sweeps"]:
    ax.plot(s["position"]["x"], s["position"]["y"], "o", c="tab:red", ms=5)
    ax.text(s["position"]["x"] + 0.1, s["position"]["y"] + 0.1, str(s["index"]), fontsize=8, c="tab:red")
for hz in fp.get("hazards", []):
    ax.plot(*hz["position"][:2], "X", c="magenta", ms=12)
    ax.text(hz["position"][0], hz["position"][1] - 0.35, f"{hz['height_m']:.2f} m {hz['kind']}", c="magenta", fontsize=8, ha="center")
ax.set_aspect("equal"); ax.set_title(f"{fp['building']['name']}: rooms, walls, doors (green), windows (blue), sweeps (red), neighbours (orange)")
out = Path(__file__).resolve().parent / "out"; out.mkdir(exist_ok=True)
fig.savefig(out / "interior_map.png", dpi=90, bbox_inches="tight")
print("wrote", out / "interior_map.png")

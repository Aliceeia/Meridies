"""Georeference the splat map frame against a Vexcel ortho (one-off).

    python connect/spike/georef.py

The splat frame is local and not north-aligned. We fetch a Vexcel ortho around the
model's lat/lon, then search rotation (0-360 deg) x translation for the best
normalised cross-correlation between edge images of the splat top-down render and
the ortho. Result is merged into floorplan.json as

    georef: {lat0, lon0, rotation_deg, score, ...}
    east, north = R(rotation_deg) @ [x, y] + 0   (metres from lat0/lon0; R is counter-clockwise)
    compass_heading = map_heading - rotation_deg
"""
from __future__ import annotations

import io
import json
import math
import sys
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy import ndimage
from scipy.signal import fftconvolve

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_floorplan import DATA, CACHE, secret  # noqa: E402

M_PER_DEG = 111_320.0
VEXCEL = "https://api.vexcelgroup.com/v2"
V_HALF = 40.0     # ortho half-size, metres
RES = 0.2         # matching resolution, m/px
MAX_SHIFT = 15.0  # house centre within this of the model's lat/lon


def square(lat, lon, half_m):
    dlat, dlon = half_m / M_PER_DEG, half_m / (M_PER_DEG * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    return f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


def vexcel_ortho(lat, lon, half_m, px, cache: Path | None = None) -> Image.Image:
    if cache and cache.exists():
        return Image.open(cache).convert("RGB")
    r = requests.post(f"{VEXCEL}/ortho/extract", headers={"X-API-KEY": secret("VEXCEL_API_KEY")}, timeout=300, json={
        "wkt": square(lat, lon, half_m), "srid": 4326, "layer": "urban,urban-r,wide-area,urban-g",
        "crop": "bounding_box", "image-format": "jpeg", "destination-dimensions": f"{px},{px}", "attribution": "false"})
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content)).convert("RGB")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        img.save(cache, quality=90)
    return img


def edges(gray):
    g = ndimage.gaussian_filter(gray, 1.0)
    return np.hypot(ndimage.sobel(g, 0), ndimage.sobel(g, 1))


def main():
    fp = json.loads((DATA / "floorplan.json").read_text())
    b = fp["building"]
    lat, lon = b["lat"], b["lon"]
    x0, x1, y0, y1 = fp["backdrop"]["extent"]
    half_s = (x1 - x0) / 2

    vimg = vexcel_ortho(lat, lon, V_HALF, 1000, CACHE / f"vexcel_{b['id']}_{int(V_HALF)}m.jpg")
    nv = int(2 * V_HALF / RES)
    V = np.asarray(vimg.convert("L").resize((nv, nv), Image.BILINEAR), float) / 255
    EV = edges(V)

    simg = Image.open(DATA / fp["backdrop"]["image"])
    ns = int(2 * half_s / RES)
    S = np.asarray(simg.convert("L").resize((ns, ns), Image.BILINEAR), float) / 255
    valid_s = S > 0.02
    ES = edges(S) * ndimage.binary_erosion(valid_s, iterations=2)

    # template grid in east/north, radius half_s/sqrt2 so it stays inside the splat image at any rotation
    ht = half_s * 0.7
    nt = int(2 * ht / RES)
    e_, n_ = np.meshgrid((np.arange(nt) + 0.5) * RES - ht, ht - (np.arange(nt) + 0.5) * RES)  # row 0 = north

    def template(theta_deg):
        th = math.radians(theta_deg)
        x = math.cos(th) * e_ + math.sin(th) * n_          # [x,y] = R(-theta) [e,n]
        y = -math.sin(th) * e_ + math.cos(th) * n_
        col = (x - x0) / RES - 0.5
        row = (y1 - y) / RES - 0.5
        T = ndimage.map_coordinates(ES, [row, col], order=1, cval=0)
        M = ndimage.map_coordinates(valid_s.astype(float), [row, col], order=0, cval=0) > 0.5
        return T, M

    V2 = EV**2
    lim = int(MAX_SHIFT / RES)
    c = nv // 2

    def score(theta):
        T, M = template(theta)
        T = np.where(M, T - T[M].mean(), 0)
        tn = np.linalg.norm(T)
        if tn == 0 or M.mean() < 0.2:
            return -1, (0, 0)
        num = fftconvolve(EV, T[::-1, ::-1], mode="same")
        mloc = fftconvolve(EV, M[::-1, ::-1].astype(float), mode="same") / M.sum()
        den = np.sqrt(np.maximum(fftconvolve(V2, M[::-1, ::-1].astype(float), mode="same") - M.sum() * mloc**2, 1e-9)) * tn
        ncc = num / den
        win = ncc[c - lim:c + lim + 1, c - lim:c + lim + 1]
        i, j = np.unravel_index(np.argmax(win), win.shape)
        # template centre at (row, col) -> east/north of splat origin
        east = (j - lim) * RES
        north = -(i - lim) * RES
        return float(win[i, j]), (east, north)

    coarse = [(score(t), t) for t in range(0, 360, 2)]
    coarse.sort(key=lambda r: -r[0][0])
    best_t = coarse[0][1]
    fine = [(score(t), t) for t in np.arange(best_t - 2, best_t + 2.01, 0.25)]
    (s_best, (de, dn)), theta = max(fine, key=lambda r: r[0][0])
    # runner-up from a clearly different rotation, to judge how unambiguous this is
    runner = next(r for r in coarse if abs(((r[1] - theta + 180) % 360) - 180) > 20)
    theta = float(theta % 360)
    print(f"rotation {theta:.2f} deg, splat origin at east {de:+.1f} m, north {dn:+.1f} m of the model lat/lon")
    print(f"NCC {s_best:.3f}; best rotation >20 deg away: {runner[1]} deg at {runner[0][0]:.3f} "
          f"(ratio {s_best / max(runner[0][0], 1e-6):.2f})")

    lat0 = lat + dn / M_PER_DEG
    lon0 = lon + de / (M_PER_DEG * math.cos(math.radians(lat)))
    fp["georef"] = dict(lat0=round(lat0, 7), lon0=round(lon0, 7), rotation_deg=round(theta, 2),
                        score=round(s_best, 3), runner_up_score=round(runner[0][0], 3),
                        method="splat top-down vs Vexcel ortho, edge NCC over rotation x translation",
                        convention="east,north = R_ccw(rotation_deg) @ [x,y]; compass heading = map heading - rotation_deg")
    # rename zones by compass direction from the house now that we know where north is
    th = math.radians(theta)
    compass = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    for z in fp["zones"]:
        x, y = z["center"]
        e, n = math.cos(th) * x - math.sin(th) * y, math.sin(th) * x + math.cos(th) * y
        letter = z["id"][-1]
        z["name"] = f"Yard {letter}, {compass[int(((math.degrees(math.atan2(e, n)) % 360) + 22.5) // 45) % 8]} of house"
        print("  ", z["name"])
    (DATA / "floorplan.json").write_text(json.dumps(fp, indent=1))
    print("merged georef into floorplan.json")

    # debug overlay: ortho with the walkable polygon and zones projected onto it
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    th = math.radians(theta)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    poly = np.array(fp["walkable"]) @ R.T + [de, dn]
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(vimg, extent=[-V_HALF, V_HALF, -V_HALF, V_HALF])
    ax.plot(*np.vstack([poly, poly[:1]]).T, c="cyan", lw=2)
    for z in fp["zones"]:
        p = np.array(z["center"]) @ R.T + [de, dn]
        ax.add_patch(plt.Circle(p, z["radius_m"], fill=False, color="yellow", ls="--"))
    up = R @ [0, 6]
    ax.annotate("", xy=(de + up[0], dn + up[1]), xytext=(de, dn), arrowprops=dict(color="w", width=2))
    ax.set_title(f"walkable polygon on Vexcel ortho; rotation {theta:.1f} deg, NCC {s_best:.2f} (arrow = map +Y)")
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    fig.savefig(CACHE / "georef_check.png", dpi=100, bbox_inches="tight")
    print(f"wrote {CACHE / 'georef_check.png'}")


if __name__ == "__main__":
    main()

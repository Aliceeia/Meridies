"""STEP A spike: real splat geometry -> connect/data/floorplan.json.

    python connect/spike/build_floorplan.py [sid]

Reads a prefix of the model's Gaussian splat (Gaussians are stored in no spatial
order, so the first N MB is a fair sample), then:
  1. detects the vertical axis empirically (RANSAC ground plane, not an assumption)
  2. estimates the ground/floor height from the height histogram (z=0 is not the floor)
  3. HAZARDS: clusters at 1.6-2.1 m above the floor with open space below (head height)
  4. WALKABLE: cells with points < 0.5 m above the floor and no body-height obstacle
  5. writes floorplan.json + a debug PNG

NOTE: these splats are aerial (drone) reconstructions of the property's exterior,
so "floor" is the ground and hazards are eaves/branches/carport edges.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import requests
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "connect" / "data"
# models_index.csv and .env are sponsor/secret files kept OUTSIDE the team repo (one folder up).
KIT = next((d for d in (ROOT, ROOT.parent) if (d / "models_index.csv").exists()), ROOT)
CACHE = DATA / "cache"
SITE = os.environ.get("HACKATHON_SITE", "https://beta-app.matterport.com/2026-princeton-hackathon").rstrip("/")

SID = sys.argv[1] if len(sys.argv) > 1 else "1PVeAYmPNJz"  # 4606 Kinglet St, Houston (1 storey)
PREFIX_MB = 150
HALF = 25.0      # metres around the scene centre
CELL = 0.25      # grid cell, metres
HAZ_LO, HAZ_HI = 1.6, 2.1
WALK_HI = 0.5
BODY_LO, BODY_HI = 0.5, 1.5


def secret(name):
    v = os.environ.get(name)
    env = KIT / ".env"
    if not v and env.exists():
        for line in env.read_text().splitlines():
            k, _, val = line.partition("=")
            if k.strip() == name:
                v = val.strip().strip('"').strip("'")
    return v


def site_auth():
    u, p = secret("HACKATHON_SITE_USER"), secret("HACKATHON_SITE_PASSWORD")
    return (u, p) if u and p else None


def fetch_prefix(sid: str, mb: int) -> bytes:
    path = CACHE / f"{sid}_splat_{mb}MB.ply"
    if path.exists():
        return path.read_bytes()
    CACHE.mkdir(parents=True, exist_ok=True)
    limit = mb * 2**20
    r = requests.get(f"{SITE}/resources/{sid}/splat/splat.ply", headers={"Range": f"bytes=0-{limit - 1}"},
                     auth=site_auth(), stream=True, timeout=300)
    r.raise_for_status()
    parts, got = [], 0
    for chunk in r.iter_content(1 << 16):
        parts.append(chunk[: limit - got])
        got += len(parts[-1])
        if got >= limit:
            break
    r.close()
    data = b"".join(parts)
    path.write_bytes(data)
    return data


def read_ply(data: bytes):
    head_end = data.index(b"end_header\n") + len(b"end_header\n")
    header = data[:head_end].decode()
    props = re.findall(r"property float (\w+)", header)
    dt = np.dtype([(p, "<f4") for p in props])
    total = int(re.search(r"element vertex (\d+)", header).group(1))
    n = min(total, (len(data) - head_end) // dt.itemsize)
    return np.frombuffer(data, dt, count=n, offset=head_end), total


def ransac_plane(p: np.ndarray, iters=400, tol=0.08, seed=0):
    rng = np.random.default_rng(seed)
    best = (0, None, None)
    for _ in range(iters):
        a, b, c = p[rng.choice(len(p), 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-6:
            continue
        n /= norm
        d = n @ a
        inl = np.abs(p @ n - d) < tol
        if inl.sum() > best[0]:
            best = (inl.sum(), n, inl)
    _, n, inl = best
    # refine with least squares (SVD) on inliers
    q = p[inl]
    c = q.mean(0)
    n = np.linalg.svd(q - c)[2][-1]
    return n / np.linalg.norm(n), c, inl.mean()


def rot_to_z(n: np.ndarray) -> np.ndarray:
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(n, z)
    s, c = np.linalg.norm(v), n @ z
    if s < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s**2)


def dp_simplify(pts: np.ndarray, tol: float) -> np.ndarray:
    if len(pts) < 3:
        return pts
    a, b = pts[0], pts[-1]
    ab = b - a
    L = np.linalg.norm(ab)
    r = pts - a
    d = np.abs(ab[0] * r[:, 1] - ab[1] * r[:, 0]) / L if L > 1e-9 else np.linalg.norm(r, axis=1)
    i = int(np.argmax(d))
    if d[i] > tol:
        return np.vstack([dp_simplify(pts[: i + 1], tol)[:-1], dp_simplify(pts[i:], tol)])
    return np.vstack([a, b])


def main():
    import pandas as pd

    idx = pd.read_csv(KIT / "models_index.csv")
    row = idx[idx.sid == SID].iloc[0]
    print(f"{SID}: {row['name']} | {row['address']}")

    g, total = read_ply(fetch_prefix(SID, PREFIX_MB))
    xyz = np.stack([g["x"], g["y"], g["z"]], 1).astype(np.float64)
    rgb = np.clip(0.5 + 0.28209479 * np.stack([g["f_dc_0"], g["f_dc_1"], g["f_dc_2"]], 1), 0, 1)
    alpha = 1 / (1 + np.exp(-g["opacity"]))
    scale = np.exp(np.stack([g["scale_0"], g["scale_1"], g["scale_2"]], 1)).max(1)
    print(f"read {len(g):,} of {total:,} Gaussians ({len(g) / total:.0%})")

    keep = (alpha > 0.1) & (scale < 1.0) & np.isfinite(xyz).all(1)
    xyz, rgb = xyz[keep], rgb[keep]
    centre = np.median(xyz, 0)
    near = np.linalg.norm(xyz - centre, axis=1) < HALF * 1.5
    xyz, rgb = xyz[near] - centre, rgb[near]
    print(f"kept {len(xyz):,} solid, small Gaussians within {HALF * 1.5:.0f} m of the median centre")

    # 1. vertical axis, empirically
    sub = xyz[np.random.default_rng(1).choice(len(xyz), min(80_000, len(xyz)), replace=False)]
    n, c0, frac = ransac_plane(sub)
    h0 = (xyz - c0) @ n
    if np.percentile(h0, 90) < -np.percentile(h0, 10):  # most structure (house, trees) sits ABOVE the ground
        n = -n
    axis = int(np.argmax(np.abs(n)))
    print(f"ground plane normal {np.round(n, 3)} ({frac:.0%} of points within 8 cm) -> "
          f"up is {'+' if n[axis] > 0 else '-'}{'XYZ'[axis]}, {np.degrees(np.arccos(abs(n[axis]))):.1f} deg off-axis")
    R = rot_to_z(n)
    p = xyz @ R.T

    # 2. floor height from the distribution
    hz = p[:, 2]
    lo, hi = np.percentile(hz, [1, 99])
    hist, edges = np.histogram(hz, bins=np.arange(lo, hi, 0.05))
    floor = float(edges[np.argmax(hist)] + 0.025)
    raw_floor = float(centre[axis] + (R.T @ [0, 0, floor])[axis])  # floor directly under the scene centre, raw frame
    print(f"floor at {floor:+.2f} m in the levelled frame (raw {'XYZ'[axis]} = {raw_floor:+.2f}); "
          f"histogram peak holds {hist.max() / len(hz):.1%} of points")
    p[:, 2] -= floor
    h = p[:, 2]

    # recentre horizontally on the tall structure (the house) if there is one
    tall = (h > 2.5) & (h < 8)
    if tall.sum() > 1000:
        p[:, :2] -= np.median(p[tall, :2], 0)

    # grid
    nx = int(2 * HALF / CELL)
    ij = np.floor((p[:, :2] + HALF) / CELL).astype(int)
    inb = (ij >= 0).all(1) & (ij < nx).all(1)

    def count(mask):
        m = mask & inb
        out = np.zeros((nx, nx), int)
        np.add.at(out, (ij[m, 0], ij[m, 1]), 1)
        return out

    ground = count((h > -0.25) & (h < WALK_HI))
    body = count((h >= BODY_LO) & (h < BODY_HI))
    head = count((h >= HAZ_LO) & (h <= HAZ_HI))
    print(f"cells: ground {np.mean(ground >= 3):.0%}, body-height {np.mean(body >= 3):.0%}, head-height {np.mean(head >= 3):.0%}")

    # 5. walkable
    blocked = ndimage.binary_dilation(body >= 3, iterations=1)
    walk = (ground >= 3) & ~blocked
    walk = ndimage.binary_opening(ndimage.binary_closing(walk, iterations=2), iterations=1)
    lab, k = ndimage.label(walk)
    if k == 0:
        raise SystemExit("no walkable area found")
    sizes = ndimage.sum(walk, lab, range(1, k + 1))
    walk = lab == (1 + int(np.argmax(sizes)))
    walk = ndimage.binary_fill_holes(walk) & ~blocked | walk
    print(f"walkable: {walk.sum() * CELL**2:.0f} m^2 in the largest region ({k} regions)")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cs = plt.contour(np.arange(nx) * CELL - HALF + CELL / 2, np.arange(nx) * CELL - HALF + CELL / 2,
                     walk.T.astype(float), levels=[0.5])
    paths = [v for seg in cs.allsegs[0] for v in [seg]]
    plt.close()
    outer = max(paths, key=len)
    poly = dp_simplify(outer, 0.3)
    print(f"walkable polygon: {len(outer)} -> {len(poly)} vertices")

    # 4. hazards: head-height returns with open space beneath, near walkable ground
    near_walk = ndimage.binary_dilation(walk, iterations=4)
    haz = (head >= 3) & (body < 2) & near_walk
    hl, hk = ndimage.label(haz)
    hazards = []
    cell_of = np.full((nx, nx), -1)
    for lab_id in range(1, hk + 1):
        cells = np.argwhere(hl == lab_id)
        if len(cells) < 2:
            continue
        cxy = (cells.mean(0) + 0.5) * CELL - HALF
        m = inb & (h >= HAZ_LO) & (h <= HAZ_HI)
        sel = m.copy()
        sel[m] = hl[ij[m, 0], ij[m, 1]] == lab_id
        hs, col = h[sel], rgb[sel].mean(0)
        kind = "branch" if col[1] > col[0] * 1.08 and col[1] > col[2] * 1.08 else "overhang"
        hazards.append(dict(position=[round(float(cxy[0]), 2), round(float(cxy[1]), 2)],
                            height_m=round(float(np.percentile(hs, 10)), 2), kind=kind,
                            extent_m2=round(len(cells) * CELL**2, 2), points=int(sel.sum())))
    hazards.sort(key=lambda d: -d["extent_m2"])
    hazards = hazards[:20]
    print(f"hazards: {hk} raw clusters -> {len(hazards)} kept")
    for d in hazards[:8]:
        print("   ", d)

    # zones: 4 clusters of walkable cells (k-means), named by direction from the house
    wc = (np.argwhere(walk) + 0.5) * CELL - HALF
    rng = np.random.default_rng(0)
    cent = wc[rng.choice(len(wc), 4, replace=False)]
    for _ in range(30):
        a = np.argmin(((wc[:, None] - cent[None]) ** 2).sum(-1), 1)
        cent = np.array([wc[a == j].mean(0) if (a == j).any() else cent[j] for j in range(4)])
    zones = []
    for j, cxy in enumerate(cent):
        members = wc[a == j]
        ang = np.degrees(np.arctan2(cxy[1], cxy[0])) % 360
        side = ["+X side", "+Y side", "-X side", "-Y side"][int(((ang + 45) % 360) // 90)]
        zones.append(dict(id=f"z{j + 1}", name=f"Yard, {side}", center=[round(float(cxy[0]), 2), round(float(cxy[1]), 2)],
                          radius_m=round(float(np.percentile(np.linalg.norm(members - cxy, axis=1), 70)), 1)))

    out = dict(
        building=dict(name=str(row["name"]), address=str(row["address"]), sid=SID),
        walkable=[[round(float(x), 2), round(float(y), 2)] for x, y in poly],
        hazards=[{k: v for k, v in d.items() if k in ("position", "height_m", "kind")} for d in hazards],
        zones=zones,
        meta=dict(
            source=f"aerial Gaussian splat, first {PREFIX_MB} MB ({len(g):,} of {total:,} Gaussians)",
            frame="metres; splat local frame re-levelled so +Z is up and floor is z=0; recentred on the house; NOT north-aligned",
            up_axis_detected=f"{'+' if n[axis] > 0 else '-'}{'XYZ'[axis]}",
            ground_normal_raw=[round(float(v), 4) for v in n],
            floor_offset_raw=round(raw_floor, 3),
            hazard_band_m=[HAZ_LO, HAZ_HI], walkable_below_m=WALK_HI,
            hazard_detail=hazards,
        ),
    )
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "floorplan.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {DATA / 'floorplan.json'}")

    # debug image: top-down colour + walkable outline + hazards
    fig, ax = plt.subplots(figsize=(9, 9))
    order = np.argsort(h)
    m = inb[order] & (h[order] < 8)
    o = order[m][:: max(1, m.sum() // 400_000)]
    ax.scatter(p[o, 0], p[o, 1], c=rgb[o], s=0.3, linewidths=0)
    ax.plot(*np.vstack([poly, poly[:1]]).T, color="cyan", lw=2, label="walkable")
    for d in hazards:
        ax.scatter(*d["position"], s=80, c="red" if d["kind"] == "overhang" else "orange", edgecolors="k", zorder=5)
        ax.annotate(f"{d['height_m']:.2f} m", d["position"], color="w", fontsize=8, xytext=(4, 4), textcoords="offset points")
    for z in zones:
        ax.add_patch(plt.Circle(z["center"], z["radius_m"], fill=False, color="yellow", ls="--"))
    ax.set_xlim(-HALF, HALF); ax.set_ylim(-HALF, HALF); ax.set_aspect("equal"); ax.set_facecolor("k")
    ax.set_title(f"{row['address']}  (walkable cyan, hazards red/orange)")
    fig.savefig(DATA / "floorplan_debug.png", dpi=110, bbox_inches="tight")
    print(f"wrote {DATA / 'floorplan_debug.png'}")


if __name__ == "__main__":
    main()

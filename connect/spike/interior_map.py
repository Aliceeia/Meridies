"""PART 2: map the sweeps of a public Matterport showcase -> connect/data/interior_floorplan.json

    python connect/spike/interior_map.py [model]

Source (a): the showcase page's own data, read in headless Chrome, no key:
  window.MP_PREFETCHED_MODELDATA.queries.GetModelPrefetch.data.model.locations
      -> sweeps: index, floor position, camera (pano) position, neighbours
  GraphQL GetModelViewPrefetch  -> geocoordinates (lat/lon + model->geo transform)
  GraphQL GetRoomBounds         -> room outlines (walkable area)
The vertical axis is measured: the camera sits a fixed height above its floor point.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "connect" / "data"
CACHE = DATA / "cache" / "interior"
MODEL = sys.argv[1] if len(sys.argv) > 1 else "zvKHR9DHNjJ"


def capture(model: str) -> dict:
    raw = CACHE / f"{model}_showcase_capture.json"
    if raw.exists():
        return json.loads(raw.read_text())
    from playwright.sync_api import sync_playwright
    got = {}
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        page = b.new_page(viewport={"width": 1280, "height": 800})

        def on_resp(r):
            for op in ("GetModelViewPrefetch", "GetRoomBounds"):
                if f"operationName={op}" in r.url or (r.request.post_data or "").find(f'"{op}"') >= 0:
                    try:
                        got[op] = r.json()
                    except Exception:
                        pass
        page.on("response", on_resp)
        page.goto(f"https://my.matterport.com/show/?m={model}&qs=1&play=1", wait_until="domcontentloaded", timeout=60_000)
        t0 = time.time()
        while time.time() - t0 < 40 and not ("GetRoomBounds" in got and "GetModelViewPrefetch" in got):
            time.sleep(1)
        got["prefetched"] = json.loads(page.evaluate("JSON.stringify(window.MP_PREFETCHED_MODELDATA)"))
        got["title"] = page.title()
        b.close()
    CACHE.mkdir(parents=True, exist_ok=True)
    raw.write_text(json.dumps(got))
    return got


def chain(es, vpos):
    """Order a room's boundary edges into a closed polygon of (x, y)."""
    if not es:
        return None
    adj = {}
    for e in es:
        a, b = e["vertices"][0]["id"], e["vertices"][1]["id"]
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    start = es[0]["vertices"][0]["id"]
    path, prev, cur = [start], None, start
    for _ in range(len(es)):
        nxt = [n for n in adj[cur] if n != prev]
        if not nxt:
            return None
        prev, cur = cur, nxt[0]
        if cur == start:
            return [vpos[v] for v in path]
        path.append(cur)
    return None


def inside(x, y, poly):
    c = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def midpoint(e, o, vpos):
    (ax, ay), (bx, by) = vpos[e["vertices"][0]["id"]], vpos[e["vertices"][1]["id"]]
    t = o.get("relativeCenter", 0.5)
    return [round(ax + (bx - ax) * t, 3), round(ay + (by - ay) * t, 3)]


def main():
    cap = capture(MODEL)
    print("captured:", [k for k in cap])
    m = cap["prefetched"]["queries"]["GetModelPrefetch"]["data"]["model"]
    locs = sorted(m["locations"], key=lambda l: l["index"])
    id2idx = {l["id"]: l["index"] for l in locs}
    floor = np.array([[l["position"][k] for k in "xyz"] for l in locs])
    cam = np.array([[l["pano"]["position"][k] for k in "xyz"] for l in locs])

    # vertical axis, empirically: the axis along which camera - floor is a constant, non-zero offset
    off = cam - floor
    axis = int(np.argmax(np.abs(off.mean(0))))
    h = off.mean(0)[axis]
    print(f"camera - floor offset mean {off.mean(0).round(3)} std {off.std(0).round(4)} -> "
          f"up is {'+' if h > 0 else '-'}{'XYZ'[axis]}, camera height {abs(h):.3f} m")
    if axis != 2 or h < 0:
        raise SystemExit("expected +Z up (Model API frame); refusing to guess a conversion")
    floor_z = float(np.median(floor[:, 2]))
    print(f"floor z = {floor_z:+.3f} (median over sweeps, spread {np.ptp(floor[:, 2]):.2f} m)")

    sweeps = [dict(index=l["index"], id=l["id"], label=l["pano"].get("label"),
                   position=dict(x=round(l["position"]["x"], 3), y=round(l["position"]["y"], 3),
                                 z=round(l["position"]["z"] - floor_z, 3)),
                   camera_height_m=round(l["pano"]["position"]["z"] - l["position"]["z"], 3),
                   neighbours=sorted(id2idx[n] for n in l["neighbors"] if n in id2idx))
              for l in locs]

    # rooms -> walkable polygons (the wall graph: rooms are cycles of wall/boundary edges)
    rb = cap["GetRoomBounds"]["data"]["model"]
    edges = {e["id"]: e for f in rb["floors"] for e in f["edges"]}
    vpos = {v["id"]: (v["position"]["x"], v["position"]["y"]) for e in edges.values() for v in e["vertices"]}
    rooms, walkable = [], []
    for r in rb["rooms"]:
        label = (r.get("label") or (r["classifications"][0]["label"] if r["classifications"] else "Room"))
        if "nonArea" in (r.get("keywords") or []) or "hide" in (r.get("keywords") or []):
            continue
        poly = chain([edges[e["id"]] for e in r["boundary"]["edges"] if e["id"] in edges], vpos)
        if poly is None or len(poly) < 3:
            print("  could not close room", label)
            continue
        rooms.append(dict(id=r["id"], label=label, polygon=[[round(x, 3), round(y, 3)] for x, y in poly],
                          area_m2=round((r.get("dimensionEstimates") or {}).get("area") or 0, 1),
                          ceiling_min_m=(r.get("ceiling") or {}).get("minHeight")))
        walkable.append(rooms[-1]["polygon"])
    for s in sweeps:
        hit = [r for r in rooms if inside(s["position"]["x"], s["position"]["y"], r["polygon"])]
        s["room"] = hit[0]["label"] if hit else "Outside"
    # number repeated labels so every room name is unique ("Bedroom 1", "Bedroom 2")
    counts = {}
    for r in rooms:
        counts[r["label"]] = counts.get(r["label"], 0) + 1
    print(f"rooms: {len(rooms)} kept -> {sorted(counts.items())}")
    print("sweeps per room:", sorted({s['room'] for s in sweeps}))
    openings = [dict(type=o["type"], label=o["label"], width_m=round(o["width"], 2), top_m=round(o["height"] + o["lowerElevation"], 2),
                     bottom_m=round(o["lowerElevation"], 2), center=midpoint(e, o, vpos))
                for e in edges.values() for o in e.get("openings") or []]

    geo = (cap.get("GetModelViewPrefetch") or {}).get("data", {}).get("model", {}).get("geocoordinates")
    georef = None
    if geo:
        # Checked by eye against a Vexcel ortho (spike/interior_georef_check.py): east,north = R(theta) p + t,
        # theta = rotation about +Z from the quaternion. Rotation matches the roof; translation is GPS-grade (~2-3 m).
        q, t = geo["rotation"], geo["translation"]
        theta = math.degrees(2 * math.atan2(q["z"], q["w"]))
        lat0 = geo["latitude"] + t["y"] / 111_320.0
        lon0 = geo["longitude"] + t["x"] / (111_320.0 * math.cos(math.radians(geo["latitude"])))
        georef = dict(lat0=round(lat0, 7), lon0=round(lon0, 7), rotation_deg=round(theta % 360, 2),
                      method="Matterport showcase geocoordinates (east,north = R(theta) p + t), verified by eye on Vexcel; ~2-3 m offset",
                      convention="east,north = R_ccw(rotation_deg) @ [x,y]; compass heading = map heading - rotation_deg")
    out = dict(
        building=dict(model_id=MODEL, id=MODEL, name=cap.get("title", MODEL),
                      lat=geo and geo["latitude"], lon=geo and geo["longitude"]),
        sweeps=sweeps, walkable=walkable, hazards=[], rooms=rooms, openings=openings,
        walls=[dict(a=vpos[e["vertices"][0]["id"]], b=vpos[e["vertices"][1]["id"]], type=e["type"],
                    thickness=e.get("thickness")) for e in edges.values()],
        geocoordinates=geo, georef=georef,
        meta=dict(source="public showcase page data (MP_PREFETCHED_MODELDATA, GetModelViewPrefetch, GetRoomBounds)",
                  frame="metres, Model API frame (+Z up, measured), floor z=0",
                  showcase_ss="ss = index + 1 (1-based, measured)"),
    )
    DATA.mkdir(parents=True, exist_ok=True)
    prev = DATA / "interior_floorplan.json"
    if prev.exists():  # keep hazards produced by interior_hazards.py
        old = json.loads(prev.read_text())
        out["hazards"] = old.get("hazards", [])
        if "hazards" in old.get("meta", {}):
            out["meta"]["hazards"] = old["meta"]["hazards"]
    (DATA / "interior_floorplan.json").write_text(json.dumps(out, indent=1))
    print(f"wrote {DATA / 'interior_floorplan.json'}: {len(sweeps)} sweeps")


if __name__ == "__main__":
    main()

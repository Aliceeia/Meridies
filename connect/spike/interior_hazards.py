"""PART 3: head-height hazards from first-person renders with Gemini Vision, cross-checked.

    python connect/spike/interior_hazards.py [--render-procs 3] [--gemini-threads 8]

No interior geometry was reachable (the CoStar splat is aerial; the showcase mesh tiles are
partial, signed and compressed), so per the plan we ask Gemini about each render and only
trust a hazard seen from more than one sweep.

Yaw convention (read off the contact sheet): view direction = (-sin yaw, cos yaw) in the
model frame, i.e. yaw is counter-clockwise from +Y.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connect.backend import gemini  # noqa: E402
from connect.backend.interior_render import CACHE, MODEL, Renderer  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "connect" / "data"
ANS = CACHE / "hazard_answers"
YAWS = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2]
HFOV_DEG = 100.0          # approx.; adjacent 90-degree views overlap
CAM_H = 1.496             # measured camera height above the floor point
CLUSTER_M, CLUSTER_DZ = 1.0, 0.3
PROMPT = ("List anything in this image that a standing adult could hit with their head or upper body — "
          "low signs, beams, open cabinet doors, protruding shelves, stair undersides. For each, give its "
          "approximate height above the floor in metres and roughly where it is in the frame. If there is "
          "nothing, say NONE.")
FORMAT = ("Answer only with JSON: {\"hazards\": [{\"kind\": short name, \"height_m\": height of its LOWEST edge "
          "above the floor, \"frame_x\": 0 (left edge) to 1 (right edge), \"frame_y\": 0 (top) to 1 (bottom), "
          "\"distance_m\": rough distance from the camera}]}. Use {\"hazards\": []} for NONE. "
          f"The camera is {CAM_H:.1f} m above the floor, looking horizontally, with a horizontal field of view "
          f"of about {HFOV_DEG:.0f} degrees.")


def render_chunk(sweeps):
    done = []
    with Renderer() as r:
        for s in sweeps:
            for y in YAWS:
                try:
                    r.render_view(s, y)
                    done.append((s, y))
                except Exception as e:
                    print(f"  render failed sweep {s} yaw {y:.2f}: {e!r}"[:160], flush=True)
    return done


def ask_group(sweeps_):
    """All four views of several sweeps in ONE request (free tier allows ~20 requests/day/model).
    Returns one answer per view, cached per view; fully cached groups cost nothing."""
    outs = {(s, y): ANS / f"{MODEL}_s{s}_y{y:.3f}.json" for s in sweeps_ for y in YAWS}
    if all(p.exists() for p in outs.values()):
        return [json.loads(p.read_text()) for p in outs.values()]
    parts = [{"text": PROMPT + "\n\n" + FORMAT + f"\n\nThere are {len(outs)} separate images below, each labelled with "
              "an id like s3_v0. Judge each image on its own. Answer with JSON {\"views\": [{\"id\": \"s3_v0\", "
              "\"hazards\": [...]}, ...]} with one entry per image id, and do not report the same object twice "
              "within one image."}]
    for s in sweeps_:
        for i, y in enumerate(YAWS):
            jp = (CACHE / f"{MODEL}_ss{s + 1}_sr0.000,{y:.3f}.jpg")
            if not jp.exists():  # send JPEG: a 12-image request stays small
                from PIL import Image
                Image.open(CACHE / f"{MODEL}_ss{s + 1}_sr0.000,{y:.3f}.png").convert("RGB").save(jp, "JPEG", quality=85)
            parts += [{"text": f"IMAGE s{s}_v{i}:"}, gemini.image_part(jp.read_bytes())]
    text = gemini.generate(parts, json_out=True, timeout=300)
    try:
        views = {str(v["id"]): v.get("hazards") or [] for v in json.loads(text)["views"]}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        views, bad = {}, text[:500]
    else:
        bad = None
    res = []
    for s in sweeps_:
        for i, y in enumerate(YAWS):
            r = {"sweep": s, "yaw": y, "model": gemini.model_name(), "hazards": views.get(f"s{s}_v{i}", [])}
            if bad:
                r["unparsed"] = bad
            outs[(s, y)].write_text(json.dumps(r, indent=1))
            res.append(r)
    return res


def inside(x, y, poly):
    c = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render-procs", type=int, default=3)
    ap.add_argument("--gemini-threads", type=int, default=2)
    ap.add_argument("--per-request", type=int, default=3, help="sweeps (x4 views) per Gemini request")
    a = ap.parse_args()
    fp = json.loads((DATA / "interior_floorplan.json").read_text())
    sweeps = {s["index"]: s for s in fp["sweeps"]}
    t0 = time.time()

    # 1. renders (cached ones return immediately)
    todo = [s for s in sweeps if not all(
        (CACHE / f"{MODEL}_ss{s + 1}_sr0.000,{y:.3f}.png").exists() for y in YAWS)]
    print(f"rendering {len(todo)} sweeps x {len(YAWS)} yaws in {a.render_procs} browsers", flush=True)
    if todo:
        chunks = [todo[i::a.render_procs] for i in range(a.render_procs)]
        with ProcessPoolExecutor(a.render_procs) as ex:
            for f in as_completed([ex.submit(render_chunk, c) for c in chunks if c]):
                print(f"  chunk done: {len(f.result())} renders  ({time.time() - t0:.0f}s)", flush=True)

    # 2. Gemini on every render
    jobs = [s for s in sweeps if all((CACHE / f"{MODEL}_ss{s + 1}_sr0.000,{y:.3f}.png").exists() for y in YAWS)]
    ANS.mkdir(parents=True, exist_ok=True)
    answers, failed = [], []
    groups = [jobs[i:i + a.per_request] for i in range(0, len(jobs), a.per_request)]
    print(f"gemini ({gemini.model_name()}): {len(jobs)} sweeps in {len(groups)} requests", flush=True)
    with ThreadPoolExecutor(a.gemini_threads) as ex:
        futs = {ex.submit(ask_group, g): g for g in groups}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                answers.extend(f.result())
            except Exception as e:
                msg = repr(e)[:160]
                if hasattr(e, "read"):
                    msg += " " + e.read()[:200].decode(errors="replace").replace("\n", " ")
                failed.append((futs[f], msg))
            print(f"  gemini request {i}/{len(groups)} done  ({time.time() - t0:.0f}s)", flush=True)
    print(f"gemini: {len(answers)} views answered, {len(failed)} sweeps failed", flush=True)
    for s, msg in failed[:3]:
        print(f"   sweep {s}: {msg}", flush=True)
    if failed and not answers:
        raise SystemExit("no Gemini answers at all; hazards left unchanged")

    # 3. project detections into the floor plan
    half = math.radians(HFOV_DEG / 2)
    dets = []
    for ans in answers:
        s = sweeps[ans["sweep"]]
        for h in ans.get("hazards") or []:
            try:
                fx, d, hz = float(h["frame_x"]), float(h["distance_m"]), float(h["height_m"])
            except (KeyError, TypeError, ValueError):
                continue
            off = math.atan((fx - 0.5) * 2 * math.tan(half))     # + = right of centre = clockwise
            yaw = ans["yaw"] - off
            x = s["position"]["x"] - d * math.sin(yaw)
            y = s["position"]["y"] + d * math.cos(yaw)
            room = next((r["label"] for r in fp["rooms"] if inside(x, y, r["polygon"])), None)
            dets.append(dict(sweep=ans["sweep"], yaw=round(ans["yaw"], 3), kind=str(h.get("kind", "?"))[:40],
                             height_m=round(hz, 2), x=round(x, 2), y=round(y, 2), distance_m=d, room=room))
    raw = len(dets)
    dets = [d for d in dets if d["room"] and 0.3 <= d["height_m"] <= 2.3]
    print(f"detections: {raw} raw, {len(dets)} inside a room at 0.3-2.3 m", flush=True)

    # 4. cross-check: greedy clustering; trust clusters seen from >= 2 different sweeps
    left, clusters = list(range(len(dets))), []
    while left:
        i = left.pop(0)
        members = [i]
        for j in left[:]:
            if (math.hypot(dets[i]["x"] - dets[j]["x"], dets[i]["y"] - dets[j]["y"]) <= CLUSTER_M
                    and abs(dets[i]["height_m"] - dets[j]["height_m"]) <= CLUSTER_DZ):
                members.append(j); left.remove(j)
        clusters.append([dets[k] for k in members])
    hazards, single = [], []
    for c in clusters:
        seen = sorted({d["sweep"] for d in c})
        kinds = [d["kind"].lower() for d in c]
        item = dict(position=[round(float(np.mean([d["x"] for d in c])), 2), round(float(np.mean([d["y"] for d in c])), 2)],
                    height_m=round(float(np.median([d["height_m"] for d in c])), 2),
                    kind=max(set(kinds), key=kinds.count), room=c[0]["room"],
                    seen_from_sweeps=seen, detections=len(c),
                    method="gemini-vision on first-person renders, cross-checked across sweeps")
        (hazards if len(seen) >= 2 else single).append(item)
    hazards.sort(key=lambda h: (-len(h["seen_from_sweeps"]), h["height_m"]))
    print(f"hazards: {len(hazards)} trusted (>=2 sweeps), {len(single)} single-sweep rejected", flush=True)
    for h in hazards:
        print("   ", h["kind"], h["height_m"], "m", h["room"], "seen from", h["seen_from_sweeps"])

    fp["hazards"] = hazards
    fp.setdefault("meta", {})["hazards"] = dict(
        method="Gemini Vision (" + gemini.model_name() + ") on 4 first-person renders per sweep; detection placed "
               f"from sweep position + yaw + frame_x (HFOV~{HFOV_DEG:.0f} deg) + estimated distance; trusted only "
               f"if seen from >=2 sweeps within {CLUSTER_M} m / {CLUSTER_DZ} m",
        renders=len(jobs), answered=len(answers), failed=len(failed), raw_detections=raw,
        rejected_single_sweep=single)
    (DATA / "interior_floorplan.json").write_text(json.dumps(fp, indent=1))
    print(f"wrote hazards into interior_floorplan.json  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()

"""Places are derived from memories, never from movement.

A Place exists only because a memory was saved there: it is computed on every request from the memories
the viewer may see, and nothing about it is stored. No visits, paths or dwell times exist anywhere.

Grouping: building + room (interior: the room polygon that holds the memory's sweep, on that sweep's floor;
falling back to the sweep's room label) or yard zone (yard: the nearest zone the memory falls inside).
"""
from __future__ import annotations

import math


def _inside(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        (xi, yi), (xj, yj) = poly[i], poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _sweep(plan, m):
    sw = plan.get("sweeps") or []
    if m.get("sweep") is not None:
        s = next((s for s in sw if s["index"] == m["sweep"]), None)
        if s:
            return s
    x, y = m["position"]["x"], m["position"]["y"]
    return min(sw, key=lambda s: math.hypot(s["position"]["x"] - x, s["position"]["y"] - y)) if sw else None


def place_of(m: dict, plan: dict) -> tuple[str, str]:
    """(place id, place name) for one memory. Pure: depends only on the memory and the building plan."""
    bid = m["building_id"]
    x, y = m["position"]["x"], m["position"]["y"]
    if plan.get("kind") == "interior":
        s = _sweep(plan, m)
        floor = s.get("floor", 0) if s else 0
        px, py = (s["position"]["x"], s["position"]["y"]) if s else (x, y)
        for r in plan.get("rooms", []):
            if r.get("floor", 0) == floor and _inside(px, py, r["polygon"]):
                return f"{bid}.{r['id']}", r["label"]
        if s:
            return f"{bid}.sweep{s['index']}", s.get("room") or "Inside"
        return f"{bid}.inside", "Inside"
    best = None
    for i, z in enumerate(plan.get("zones", [])):
        d = math.hypot(x - z["center"][0], y - z["center"][1])
        if d <= z["radius_m"] and (best is None or d < best[0]):
            best = (d, i, z)
    if best:
        return f"{bid}.zone{best[1]}", best[2]["name"]          # "Front yard, under the big tree"
    return f"{bid}.yard", "The yard"


def group(memories: list[dict], plan_for) -> list[dict]:
    """memories: already filtered to what this viewer may see. plan_for(bid) -> plan dict (with "kind" and "building").
    Returns places, most recent first; each holds its memories, most recent first."""
    places: dict[str, dict] = {}
    for m in memories:
        plan = plan_for(m["building_id"])
        pid, name = place_of(m, plan)
        p = places.setdefault(pid, dict(id=pid, name=name, building=dict(id=m["building_id"], name=plan["building"]["name"],
                                                                         kind=plan.get("kind")), memories=[]))
        p["memories"].append(m)
    out = []
    for p in places.values():
        p["memories"].sort(key=lambda m: m["created_at"], reverse=True)
        p["memory_count"] = len(p["memories"])
        p["latest_at"] = p["memories"][0]["created_at"]
        out.append(p)
    # two rooms both labelled "Hallway" in one building: tell them apart without inventing anything
    seen: dict[tuple, int] = {}
    for p in sorted(out, key=lambda p: p["id"]):
        k = (p["building"]["id"], p["name"])
        seen[k] = seen.get(k, 0) + 1
        if seen[k] > 1:
            p["name"] = f"{p['name']} {seen[k]}"
    out.sort(key=lambda p: p["latest_at"], reverse=True)
    return out

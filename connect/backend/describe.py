"""AI description of the place a memory was left.

Interior building (Matterport showcase): THREE images, in this order
  1. first-person render at the memory's sweep, facing the recorded heading   <- primary
  2. Vexcel aerial crop around the anchor                                    <- layout context
  3. top-down floor plan from the scan (walls, doors, rooms, hazards), north up <- layout context
Yard building (aerial splat only): the original two-image description.
"""
from __future__ import annotations

import io
import json
import math
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import gemini

DATA = Path(__file__).resolve().parents[1] / "data"
VEXCEL = "https://api.vexcelgroup.com/v2"
M_PER_DEG = 111_320.0
BUILDINGS = {  # id -> (plan file, kind)
    "zvKHR9DHNjJ": ("interior_floorplan.json", "interior"),
    "1PVeAYmPNJz": ("floorplan.json", "yard"),
}
AERIAL_HALF, SPLAT_HALF, PLAN_HALF = 15.0, 12.0, 8.0

PROMPT_INTERIOR = (
    "The first image is what a blind person would be facing at eye level while standing at this spot. "
    "The other two are overhead views of the same building for layout context.\n\n"
    "Describe this place in two sentences for someone who cannot see it. Lead with what is directly ahead "
    "of them and roughly how far away it is, in steps or arm's lengths. Then describe what is on their left, "
    "on their right and behind them, relative to their own body. Never use compass directions (north, south, "
    "east, west): a blind person orients by their body, not by the map. "
    "Mention what they could hear, touch, or walk to, and anything at head height they should know about. "
    "Do not mention colours or anything that is purely visual. Answer in English.")
PROMPT_YARD = ("This is imagery of a location a blind person is standing at. Describe it in two sentences, "
               "focusing on what they could hear, touch, or walk to. Never use compass directions (north, south, "
               "east, west); say ahead, left, right or behind. Do not describe colours or anything purely visual. "
               "Answer in English.")


def load_plan(bid: str) -> dict:
    fname, kind = BUILDINGS[bid]
    fp = json.loads((DATA / fname).read_text())
    fp["kind"] = kind
    return fp


def to_latlon(fp, x, y):
    g = fp["georef"]
    th = math.radians(g["rotation_deg"])
    e, n = math.cos(th) * x - math.sin(th) * y, math.sin(th) * x + math.cos(th) * y
    return g["lat0"] + n / M_PER_DEG, g["lon0"] + e / (M_PER_DEG * math.cos(math.radians(g["lat0"])))


def compass(fp, heading_deg: float) -> str:
    c = (heading_deg - fp["georef"]["rotation_deg"]) % 360
    return ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"][int((c + 22.5) // 45) % 8]


def aerial_crop(lat, lon, half=AERIAL_HALF, px=640) -> bytes:
    dlat, dlon = half / M_PER_DEG, half / (M_PER_DEG * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    req = urllib.request.Request(f"{VEXCEL}/ortho/extract", method="POST",
        headers={"X-API-KEY": gemini.secret("VEXCEL_API_KEY") or "", "Content-Type": "application/json"},
        data=json.dumps({"wkt": f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))", "srid": 4326,
                         "layer": "urban,urban-r,wide-area,urban-g", "crop": "bounding_box", "image-format": "jpeg",
                         "destination-dimensions": f"{px},{px}", "attribution": "false"}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def splat_crop(fp, x, y, half=SPLAT_HALF) -> bytes:
    """Yard: north-up crop of the splat top-down render centred on (x, y)."""
    from PIL import Image
    img = Image.open(DATA / fp["backdrop"]["image"])
    x0, x1, y0, y1 = fp["backdrop"]["extent"]
    ppm = img.width / (x1 - x0)
    cx, cy = (x - x0) * ppm, (y1 - y) * ppm
    r = half * 1.5 * ppm
    big = img.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r)))
    big = big.rotate(fp["georef"]["rotation_deg"], resample=Image.BICUBIC)  # map +Y up -> north up
    k = (big.width - 2 * half * ppm) / 2
    out = io.BytesIO()
    big.crop((int(k), int(k), int(big.width - k), int(big.height - k))).save(out, "JPEG", quality=85)
    return out.getvalue()


def plan_crop(fp, x, y, heading_deg, half=PLAN_HALF) -> bytes:
    """Interior: HEADING-UP top-down floor plan around (x, y): the top of the image is the way the person faces.
    Walls, doors, room names, hazards, person."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    hd = math.radians(heading_deg)
    sh, ch = math.sin(hd), math.cos(hd)
    en = lambda px, py: (ch * (px - x) - sh * (py - y), sh * (px - x) + ch * (py - y))  # (right, ahead) of the person
    fig = Figure(figsize=(6, 6), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor("white")
    for r in fp["rooms"]:
        pts = [en(*p) for p in r["polygon"]]
        ax.fill(*zip(*pts), color="0.93")
        cx, cy = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        if abs(cx) < half and abs(cy) < half:
            ax.text(cx, cy, r["label"], ha="center", va="center", fontsize=11, color="0.3")
    for w in fp["walls"]:
        (ax_, ay_), (bx, by) = en(*w["a"]), en(*w["b"])
        ax.plot([ax_, bx], [ay_, by], color="black", lw=4 if w["type"] == "wall" else 1)
    for o in fp.get("openings", []):
        ox, oy = en(*o["center"])
        if o["type"] == "doorway":
            ax.plot(ox, oy, "s", color="white", mec="tab:green", mew=2, ms=11)
    for h in fp.get("hazards", []):
        hx, hy = en(*h["position"][:2])
        ax.plot(hx, hy, "X", color="magenta", ms=13)
        ax.text(hx, hy - 0.5, f"{h['kind']} {h['height_m']:.1f} m", ha="center", fontsize=9, color="magenta")
    ax.annotate("", xy=(0, 1.6), xytext=(0, 0), arrowprops=dict(color="red", width=3, headwidth=12))
    ax.plot(0, 0, "o", color="red", ms=14)
    ax.text(0, half * 0.9, "AHEAD", ha="center", fontsize=14, weight="bold")
    ax.text(-half * 0.9, 0, "LEFT", ha="left", va="center", fontsize=12, weight="bold")
    ax.text(half * 0.9, 0, "RIGHT", ha="right", va="center", fontsize=12, weight="bold")
    ax.set_xlim(-half, half); ax.set_ylim(-half, half); ax.set_aspect("equal"); ax.axis("off")
    out = io.BytesIO()
    fig.savefig(out, format="jpeg")
    return out.getvalue()


def heading_up(jpeg: bytes, bearing_deg: float) -> bytes:
    """Rotate a north-up image so the given compass bearing points up, then trim the rotated corners."""
    from PIL import Image
    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    img = img.rotate(bearing_deg, resample=Image.BICUBIC)   # counter-clockwise by the bearing: that bearing ends up on top
    k = img.width * (1 - 1 / 1.42) / 2
    out = io.BytesIO()
    img.crop((int(k), int(k), int(img.width - k), int(img.height - k))).resize((640, 640)).save(out, "JPEG", quality=85)
    return out.getvalue()


def nearest_sweep(fp, x, y) -> int:
    return min(fp["sweeps"], key=lambda s: math.hypot(s["position"]["x"] - x, s["position"]["y"] - y))["index"]


def first_person(bid: str, sweep: int, heading_deg: float) -> bytes:
    """Eye-level render facing the heading (map heading is clockwise; showcase yaw is counter-clockwise)."""
    from PIL import Image
    from .interior_render import Renderer
    with Renderer(model=bid) as r:
        png = r.render_view(sweep, -math.radians(heading_deg))
    out = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").save(out, "JPEG", quality=88)
    return out.getvalue()


def describe(m: dict, mem_dir: Path) -> dict:
    """Builds the images (cached next to the memory), asks Gemini, returns the description record."""
    bid = m["building_id"]
    if bid not in BUILDINGS:
        raise LookupError(f"unknown building {bid}")
    fp = load_plan(bid)
    if not fp.get("georef"):
        raise LookupError(f"{BUILDINGS[bid][0]} has no georef")
    x, y = m["position"]["x"], m["position"]["y"]
    heading = float(m.get("heading_deg") or 0.0)
    lat, lon = to_latlon(fp, x, y)

    def cached(kind, make):
        p = mem_dir / f"{m['id']}_{kind}.jpg"
        if not p.exists():
            p.write_bytes(make())
        return p.read_bytes()

    if fp["kind"] == "interior":
        sweep = m.get("sweep")
        if sweep is None:
            sweep = nearest_sweep(fp, x, y)
        turn = (heading - fp["georef"]["rotation_deg"]) % 360   # compass bearing they face; used only to rotate images
        images = [
            ("firstperson", "1 · What's ahead, at eye level (primary)",
             "Image 1: first-person view at eye level (1.5 m) from where the person stands, looking straight ahead.",
             cached("firstperson", lambda: first_person(bid, sweep, heading))),
            ("aerial2", "2 · From above (layout)",
             f"Image 2: aerial photo of the building, rotated so the TOP of the image is the way the person faces, "
             f"{2 * AERIAL_HALF:.0f} m across, person at the centre.",
             cached("aerial2", lambda: heading_up(aerial_crop(lat, lon, half=AERIAL_HALF * 1.42, px=900), turn))),
            ("topdown2", "3 · Floor plan, ahead is up (layout)",
             f"Image 3: top-down floor plan from the 3D scan, rotated so the TOP is the way the person faces, "
             f"{2 * PLAN_HALF:.0f} m across; red dot and arrow = the person; magenta X = head-height hazard with its height.",
             cached("topdown2", lambda: plan_crop(fp, x, y, heading))),
        ]
        prompt, extra = PROMPT_INTERIOR, dict(sweep=sweep)
    else:
        images = [
            ("aerial", "Vexcel aerial", f"Image 1: aerial orthophoto (Vexcel), north up, {2 * AERIAL_HALF:.0f} m across; "
             "the person is at the centre.", cached("aerial", lambda: aerial_crop(lat, lon))),
            ("splat", "Drone scan, top-down", f"Image 2: top-down render of a drone 3D scan of the same spot, north up, "
             f"{2 * SPLAT_HALF:.0f} m across; the person is at the centre.", cached("splat", lambda: splat_crop(fp, x, y))),
        ]
        prompt, extra = PROMPT_YARD, {}
    parts = [{"text": prompt}]
    for _, _, caption, data in images:
        parts += [{"text": caption}, gemini.image_part(data)]
    text = gemini.generate(parts)
    return dict(text=text, model=gemini.last_model or gemini.model_name(), lat=round(lat, 7), lon=round(lon, 7),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                images=[dict(kind=k, label=lbl) for k, lbl, _, _ in images], **extra)

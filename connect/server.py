"""Memory layer server: static frontend + walk map + memory store.

    python connect/server.py            # http://localhost:8765

Stdlib only. Memories live on disk in connect/data/memories/ (git-ignored):
<id>.json (metadata) next to <id>.<ext> (audio).

Identity is a demo switch (author_id sent by the client), not authentication.
"""
from __future__ import annotations

import base64
import io
import json
import math
import mimetypes
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
FRONT = HERE / "frontend"
DATA = HERE / "data"
MEM = DATA / "memories"
PORT = 8765
MAX_AUDIO = 5 * 2**20            # 10 s of opus is ~100-200 KB; refuse anything silly
AUDIO_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}
ID_RE = re.compile(r"^[0-9a-f]{32}$")
PUBLIC_DATA = {"floorplan.json", "splat_topdown.jpg"}


# ---------- AI description (step 4) ----------
KIT = HERE.parent.parent  # sponsor kit folder holding the git-ignored .env
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VEXCEL = "https://api.vexcelgroup.com/v2"
M_PER_DEG = 111_320.0
PROMPT = ("This is imagery of a location a blind person is standing at. Describe it in two sentences, "
          "focusing on what they could hear, touch, or walk to. Do not describe colours or anything purely visual.")
AERIAL_HALF, SPLAT_HALF = 15.0, 12.0


def secret(name):
    v = os.environ.get(name)
    env = KIT / ".env"
    if not v and env.exists():
        for line in env.read_text().splitlines():
            k, _, val = line.partition("=")
            if k.strip() == name:
                v = val.strip().strip('"').strip("'")
    return v


def floorplan():
    return json.loads((DATA / "floorplan.json").read_text())


def to_latlon(fp, x, y):
    g = fp["georef"]
    th = math.radians(g["rotation_deg"])
    e, n = math.cos(th) * x - math.sin(th) * y, math.sin(th) * x + math.cos(th) * y
    return g["lat0"] + n / M_PER_DEG, g["lon0"] + e / (M_PER_DEG * math.cos(math.radians(g["lat0"])))


def aerial_crop(lat, lon, half=AERIAL_HALF, px=640) -> bytes:
    dlat, dlon = half / M_PER_DEG, half / (M_PER_DEG * math.cos(math.radians(lat)))
    w, e, s, n = lon - dlon, lon + dlon, lat - dlat, lat + dlat
    req = urllib.request.Request(f"{VEXCEL}/ortho/extract", method="POST",
        headers={"X-API-KEY": secret("VEXCEL_API_KEY") or "", "Content-Type": "application/json"},
        data=json.dumps({"wkt": f"POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))", "srid": 4326,
                         "layer": "urban,urban-r,wide-area,urban-g", "crop": "bounding_box", "image-format": "jpeg",
                         "destination-dimensions": f"{px},{px}", "attribution": "false"}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def splat_crop(fp, x, y, half=SPLAT_HALF) -> bytes:
    """North-up crop of the splat top-down render centred on (x, y)."""
    from PIL import Image
    img = Image.open(DATA / fp["backdrop"]["image"])
    x0, x1, y0, y1 = fp["backdrop"]["extent"]
    ppm = img.width / (x1 - x0)
    cx, cy = (x - x0) * ppm, (y1 - y) * ppm
    r = half * 1.5 * ppm
    big = img.crop((int(cx - r), int(cy - r), int(cx + r), int(cy + r)))
    big = big.rotate(fp["georef"]["rotation_deg"], resample=Image.BICUBIC)  # image-up (map +Y) -> north-up
    k = (big.width - 2 * half * ppm) / 2
    out = io.BytesIO()
    big.crop((int(k), int(k), int(big.width - k), int(big.height - k))).save(out, "JPEG", quality=85)
    return out.getvalue()


def gemini(images: list[tuple[str, bytes]]) -> str:
    key = secret("GEMINI_API_KEY") or secret("GOOGLE_API_KEY")
    if not key:
        raise LookupError("GEMINI_API_KEY is not set (add it to the .env next to models_index.csv)")
    parts = [{"text": PROMPT}]
    for caption, data in images:
        parts += [{"text": caption}, {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(data).decode()}}]
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent", method="POST",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        data=json.dumps({"contents": [{"role": "user", "parts": parts}]}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.loads(r.read())
    return " ".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"]).strip()


def describe(m) -> dict:
    fp = floorplan()
    if "georef" not in fp:
        raise LookupError("floorplan.json has no georef; run connect/spike/georef.py")
    x, y = m["position"]["x"], m["position"]["y"]
    lat, lon = to_latlon(fp, x, y)
    aerial = MEM / f"{m['id']}_aerial.jpg"
    if not aerial.exists():
        aerial.write_bytes(aerial_crop(lat, lon))
    splat = MEM / f"{m['id']}_splat.jpg"
    if not splat.exists():
        splat.write_bytes(splat_crop(fp, x, y))
    text = gemini([
        (f"Image 1: aerial orthophoto (Vexcel), north up, {2 * AERIAL_HALF:.0f} m across; the person is at the centre.",
         aerial.read_bytes()),
        (f"Image 2: top-down render of a drone 3D scan of the same spot, north up, {2 * SPLAT_HALF:.0f} m across; "
         f"the person is at the centre.", splat.read_bytes()),
    ])
    return dict(text=text, model=GEMINI_MODEL, lat=round(lat, 7), lon=round(lon, 7),
                created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                sources=["vexcel ortho", "drone splat render"])


def load(mid):
    return json.loads((MEM / f"{mid}.json").read_text())


def save(meta):
    MEM.mkdir(parents=True, exist_ok=True)
    (MEM / f"{meta['id']}.json").write_text(json.dumps(meta, indent=1))


def can_see(meta, viewer):
    return meta["author_id"] == viewer or meta["visibility"] == "community"


def public(meta):
    out = {k: v for k, v in meta.items() if k != "audio_file"}
    out["audio"] = f"/api/memories/{meta['id']}/audio"
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)

    # helpers
    def send(self, code, body=b"", ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def err(self, code, msg):
        self.send(code, {"error": msg})

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_AUDIO:
            raise ValueError("body too large")
        return self.rfile.read(n)

    def file(self, path: Path, ctype=None):
        if not path.is_file():
            return self.err(404, "not found")
        self.send(200, path.read_bytes(), ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    # routes
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        if not parts:
            return self.file(FRONT / "index.html")
        if parts[0] == "data" and len(parts) == 2 and parts[1] in PUBLIC_DATA:
            return self.file(DATA / parts[1])
        if parts == ["api", "floorplan"]:
            fp = json.loads((DATA / "floorplan.json").read_text())
            fp.pop("meta", None)
            return self.send(200, fp)
        if parts == ["api", "memories"]:
            viewer, bid = q.get("viewer", ""), q.get("building_id")
            out = []
            for f in sorted(MEM.glob("*.json")) if MEM.exists() else []:
                m = json.loads(f.read_text())
                if can_see(m, viewer) and (bid is None or m["building_id"] == bid):
                    out.append(public(m))
            return self.send(200, out)
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "audio" and ID_RE.match(parts[2]):
            try:
                m = load(parts[2])
            except FileNotFoundError:
                return self.err(404, "no such memory")
            if not can_see(m, q.get("viewer", "")):
                return self.err(403, "private memory")
            return self.file(MEM / m["audio_file"], m["audio_type"])
        if (len(parts) == 5 and parts[:2] == ["api", "memories"] and parts[3] == "image"
                and parts[4] in ("aerial", "splat") and ID_RE.match(parts[2])):
            try:
                m = load(parts[2])
            except FileNotFoundError:
                return self.err(404, "no such memory")
            if not can_see(m, q.get("viewer", "")):
                return self.err(403, "private memory")
            return self.file(MEM / f"{m['id']}_{parts[4]}.jpg", "image/jpeg")
        if len(parts) == 1 and (FRONT / parts[0]).is_file():
            return self.file(FRONT / parts[0])
        return self.err(404, "not found")

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        if parts == ["api", "memories"]:
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype not in AUDIO_EXT:
                return self.err(415, f"audio type {ctype!r} not accepted")
            try:
                audio = self.body()
                pos = {k: round(float(q[k]), 3) for k in ("x", "y", "z")}
                author, bid = q["author_id"], q["building_id"]
            except (KeyError, ValueError) as e:
                return self.err(400, f"bad request: {e}")
            if not audio or not author or not bid:
                return self.err(400, "empty audio, author_id or building_id")
            mid = uuid.uuid4().hex
            MEM.mkdir(parents=True, exist_ok=True)
            fname = f"{mid}.{AUDIO_EXT[ctype]}"
            (MEM / fname).write_bytes(audio)
            meta = dict(id=mid, position=pos, building_id=bid, author_id=author,
                        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        visibility="private", description=None, audio_type=ctype, audio_file=fname)
            save(meta)
            return self.send(201, public(meta))
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "describe" and ID_RE.match(parts[2]):
            try:
                m, req = load(parts[2]), json.loads(self.body() or b"{}")
            except (FileNotFoundError, ValueError):
                return self.err(404, "no such memory")
            if not can_see(m, req.get("viewer", "")):
                return self.err(403, "private memory")
            try:
                m["description"] = describe(m)
            except LookupError as e:
                return self.err(503, str(e))
            except urllib.error.HTTPError as e:
                return self.err(502, f"{e.url.split('?')[0]} answered {e.code}: {e.read()[:200].decode(errors='replace')}")
            except (urllib.error.URLError, KeyError, IndexError) as e:
                return self.err(502, f"description failed: {e!r}")
            save(m)
            return self.send(200, public(m))
        return self.err(404, "not found")

    def do_PATCH(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if len(parts) == 3 and parts[:2] == ["api", "memories"] and ID_RE.match(parts[2]):
            try:
                m, req = load(parts[2]), json.loads(self.body() or b"{}")
            except (FileNotFoundError, ValueError):
                return self.err(404, "no such memory")
            if req.get("viewer") != m["author_id"]:
                return self.err(403, "only the author can change visibility")
            if req.get("visibility") not in ("private", "community"):
                return self.err(400, "visibility must be private or community")
            m["visibility"] = req["visibility"]
            save(m)
            return self.send(200, public(m))
        return self.err(404, "not found")


if __name__ == "__main__":
    print(f"memory layer on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

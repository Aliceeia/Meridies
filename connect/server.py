"""Memory layer server: static frontend + walk map + memory store.

    python connect/server.py            # http://localhost:8765

Stdlib only. Memories live on disk in connect/data/memories/ (git-ignored):
<id>.json (metadata) next to <id>.<ext> (audio).

Identity is a demo switch (author_id sent by the client), not authentication.
"""
from __future__ import annotations

import json
import mimetypes
import re
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
        print(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}")

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

    def file(self, path: Path):
        if not path.is_file():
            return self.err(404, "not found")
        self.send(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")

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
            return self.file(MEM / m["audio_file"])
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

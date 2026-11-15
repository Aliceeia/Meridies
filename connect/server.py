"""Memory layer server: gallery, realtime page, 3D walk, memory store.

    python connect/server.py            # http://localhost:8765

Stdlib only. Memories live on disk in connect/data/memories/ (git-ignored):
<id>.json (metadata) next to <id>.<ext> (audio), <id>_camera.jpg (hat frame, if sent), <id>_cover.jpg.

Visibility: private (default) | family | community. Only the author changes it.
  author  (?viewer=<author_id>)   sees all of their own memories
  family  (?family=<link token>)  sees ONLY the link owner's family memories; read-only
  anyone                          community memories (data model only: no browsing UI yet)
Places are computed from the memories a viewer may see, per request; nothing about movement is stored.

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
import secrets
import threading
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
MAX_AUDIO = 5 * 2**20            # backstop; opus is ~15-30 KB/s, the page stops at 4.5 MB or 5 min first
AUDIO_EXT = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}
ID_RE = re.compile(r"^[0-9a-f]{32}$")
PUBLIC_DATA = {"floorplan.json", "splat_topdown.jpg"}
VISIBILITY = ("private", "family", "community")
LINKS = DATA / "family_links.json"          # token -> author_id; git-ignored
MAX_FRAME = 3 * 2**20
AUTO_PROCESS = os.environ.get("MERIDIES_AUTO_PROCESS", "1") == "1"    # transcribe + cover (+ describe) after save
AUTO_DESCRIBE = os.environ.get("MERIDIES_AUTO_DESCRIBE", "1") == "1"
VOICE_ON = os.environ.get("MERIDIES_VOICE", "1") == "1"          # cached Gemini clips for system speech
VOICE_AUTOGEN = os.environ.get("MERIDIES_VOICE_AUTOGEN", "1") == "1"   # make missing clips in the background


# ---------- AI description: see connect/backend/describe.py ----------
import sys  # noqa: E402
sys.path.insert(0, str(HERE.parent))
from connect.backend import describe as desc  # noqa: E402

DEFAULT_BUILDING = "zvKHR9DHNjJ"  # the interior scan; the yard (1PVeAYmPNJz) is the second building
VIEW_LOCK = __import__("threading").Lock()  # one headless render at a time


def view_png(bid: str, sweep: int, heading_deg: float) -> bytes:
    """First-person view at a sweep, heading snapped to the nearest of the 4 pre-rendered yaws."""
    import math as _m
    from connect.backend.interior_render import Renderer
    k = round(((-heading_deg) % 360) / 90) % 4          # map heading is clockwise, showcase yaw counter-clockwise
    with VIEW_LOCK, Renderer(model=bid) as r:
        jpg = r.cache_path(sweep + 1, f"0.000,{k * _m.pi / 2:.3f}").with_suffix(".jpg")
        if not jpg.exists():                              # ~10x smaller than the PNG master
            from PIL import Image
            Image.open(io.BytesIO(r.render_view(sweep, k * _m.pi / 2))).convert("RGB").save(jpg, "JPEG", quality=85)
        return jpg.read_bytes()


MEM_LOCK = threading.RLock()   # every read-modify-write of a memory record goes through update()


def load(mid):
    return json.loads((MEM / f"{mid}.json").read_text())


def save(meta):
    MEM.mkdir(parents=True, exist_ok=True)
    tmp = MEM / f"{meta['id']}.json.tmp"
    tmp.write_text(json.dumps(meta, indent=1))
    tmp.replace(MEM / f"{meta['id']}.json")


def update(mid, fn):
    """Load the freshest record, apply fn(m) (which may return (code, error)), save. Returns (m, code, error)."""
    with MEM_LOCK:
        m = load(mid)
        r = fn(m)
        if r and r[1]:
            return m, r[0], r[1]
        save(m)
        return m, 200, None


SETTINGS = DATA / "settings.json"   # {"hide_samples": bool}; git-ignored


def settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def all_memories(include_hidden=False):
    """Every memory, minus the seeded samples while they are hidden (one switch hides them from every view)."""
    files = sorted(f for f in MEM.glob("*.json") if ID_RE.match(f.stem)) if MEM.exists() else []
    out = [json.loads(f.read_text()) for f in files]
    return [m for m in out if not m.get("is_sample")] if settings().get("hide_samples") and not include_hidden else out


# ---------- MeriCircle: the people an author shares "family" memories with ----------
CIRCLES = DATA / "circles.json"     # {author_id: [member_id, ...]}; git-ignored. Only the author edits their own.


def circles() -> dict:
    try:
        return json.loads(CIRCLES.read_text())
    except (OSError, ValueError):
        return {}


def in_circle(author: str, viewer: str) -> bool:
    return bool(viewer) and viewer in circles().get(author, [])


# ---------- family links: a read-only key to one author's family memories ----------
def _links() -> dict:
    try:
        return json.loads(LINKS.read_text())
    except (OSError, ValueError):
        return {}


def family_owner(token: str | None) -> str | None:
    return _links().get(token) if token else None


def family_link_for(author: str, create=True) -> str | None:
    with MEM_LOCK:
        links = _links()
        tok = next((t for t, a in links.items() if a == author), None)
        if tok or not create:
            return tok
        tok = secrets.token_urlsafe(12)
        links[tok] = author
        LINKS.write_text(json.dumps(links, indent=1))
        return tok


def revoke_family_link(author: str):
    with MEM_LOCK:
        LINKS.write_text(json.dumps({t: a for t, a in _links().items() if a != author}, indent=1))


def who(q: dict) -> tuple[str, str | None]:
    """(viewer, family_owner). A family link is read-only: it never carries an author identity."""
    if q.get("family"):
        return "", family_owner(q["family"]) or "\0no-such-link"
    return q.get("viewer", ""), None


def can_see(meta, viewer, fam_owner=None):
    if viewer and meta["author_id"] == viewer:
        return True
    if meta["visibility"] == "community":
        return True
    if meta["visibility"] != "family":
        return False
    if fam_owner is not None:
        return fam_owner == meta["author_id"]
    return in_circle(meta["author_id"], viewer)


# ---------- after save: transcript, cover image, description (one at a time, never blocks the upload) ----------
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="process")


def cover_file(m) -> Path | None:
    """The hat camera frame if one was sent, else the first-person scan render at that spot and heading
    (interior), else the top-down drone scan around the spot (the yard has no first-person renders)."""
    cam = MEM / f"{m['id']}_camera.jpg"
    if cam.exists():
        return cam
    out = MEM / f"{m['id']}_cover.jpg"
    if out.exists():
        return out
    bid = m["building_id"]
    if bid not in desc.BUILDINGS:
        return None
    plan = desc.load_plan(bid)
    x, y = m["position"]["x"], m["position"]["y"]
    if plan["kind"] == "interior":
        sweep = m.get("sweep")
        if sweep is None:
            sweep = desc.nearest_sweep(plan, x, y)
        data = view_png(bid, sweep, float(m.get("heading_deg") or 0))
    else:
        data = desc.splat_crop(plan, x, y)
    out.write_bytes(data)
    return out


def process(mid: str):
    """Fills what is missing. Every step is optional: a failure is recorded on the memory and nothing else changes."""
    from connect.backend import transcribe as tr
    m = load(mid)
    now = lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")  # noqa: E731
    if not (m.get("transcript") or {}).get("status") == "done":
        try:
            t = tr.transcribe(MEM / m["audio_file"], m["audio_type"])
            res = dict(status="done", at=now(), **t)
        except Exception as e:  # noqa: BLE001 - quota, network, ffmpeg: keep the memory, note the failure
            res = dict(status="failed", at=now(), error=f"{type(e).__name__}: {e}"[:200])
        update(mid, lambda m: m.__setitem__("transcript", res))
        print(f"  transcript {mid[:8]}: {res['status']}", flush=True)
    try:
        cover_file(m)
    except Exception as e:  # noqa: BLE001
        print(f"  cover {mid[:8]}: failed {e!r}"[:200], flush=True)
    if AUTO_DESCRIBE and not load(mid).get("description"):
        try:
            d = desc.describe(m, MEM)
            update(mid, lambda m: m.__setitem__("description", d))
        except Exception as e:  # noqa: BLE001
            print(f"  describe {mid[:8]}: failed {e!r}"[:200], flush=True)


def enqueue(mid: str):
    if AUTO_PROCESS:
        WORKER.submit(process, mid)


COMPASS_RE = re.compile(r"\b(north|south|east|west)(ern|ward|-?east|-?west)?\b", re.I)


def is_public_space(bid: str) -> bool:
    """Community sharing exists only in public spaces (museums, campuses, parks).
    The flag lives in the building record; a missing flag means NOT public."""
    try:
        return desc.load_plan(bid)["building"].get("public") is True
    except (KeyError, OSError, ValueError):
        return False


def apply_choice(m: dict, choice: str, reason: str) -> tuple[int, str | None]:
    """The only place that changes who may hear a memory. Private unless explicitly shared AND reviewed.
    Returns (http status, error)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if choice == "private":
        m.update(visibility="private", draft=False)
        if m.get("share_request"):
            m["share_request"]["status"] = "withdrawn"
    elif choice == "family":
        m.update(visibility="family", draft=False)
        if m.get("share_request"):
            m["share_request"]["status"] = "withdrawn"
    elif choice == "later":
        m.update(visibility="private", draft=True)
    elif choice == "share":
        if not is_public_space(m["building_id"]):
            return 403, "community sharing is only available in public spaces"
        from connect.backend import moderation
        review = moderation.review_share_request(MEM / m["audio_file"], m)
        # visibility stays private until a reviewer approves; the stub never approves
        m.update(draft=False, share_request={"status": review["status"], "requested_at": now, "review": review})
        if review["status"] == "approved":
            m["visibility"] = "community"
    else:
        return 400, "choice must be private, family, share or later"
    m.setdefault("decisions", []).append({"choice": choice, "reason": reason, "at": now})
    return 200, None


def public(meta, viewer=None):
    out = {k: v for k, v in meta.items() if k not in ("audio_file", "decisions")}
    if out.get("share_request") and viewer != meta.get("author_id"):
        out["share_request"] = {"status": out["share_request"]["status"]}   # review details are the author's business
    out["reports"] = len(meta.get("reports") or []) if viewer != meta.get("author_id") else meta.get("reports") or []
    d = out.get("description")
    if d and COMPASS_RE.search(d.get("text", "")):
        out["description"] = None   # written before descriptions became body-relative: ask again rather than say "south"
    out["audio"] = f"/api/memories/{meta['id']}/audio"
    out["cover"] = f"/api/memories/{meta['id']}/cover"
    out["cover_source"] = "camera" if (MEM / f"{meta['id']}_camera.jpg").exists() else "scan"
    t = meta.get("transcript") or {}
    out["transcript"] = {k: t[k] for k in ("status", "text", "no_speech") if k in t} or None
    return out


# ---------- hat buttons -> open gallery pages (listen mode). Only the button name travels; nothing is stored. ----------
import queue  # noqa: E402
HAT_BUTTONS = {"left", "right", "up", "down", "play", "describe", "there", "help", "listen"}
HAT_CLIENTS: list[queue.Queue] = []
HAT_LOCK = threading.Lock()


def hat_broadcast(button: str) -> int:
    with HAT_LOCK:
        for c in HAT_CLIENTS:
            c.put(button)
        return len(HAT_CLIENTS)


# ---------- /live: relay the hat's obstacle list (ZeroMQ, port 5556) to the realtime page ----------
# Only the LATEST frame is held in memory, for display; nothing is written anywhere.
LIVE = {"warnings": [], "t": 0.0, "source": "none"}
LIVE_COND = threading.Condition()
HAT_ENDPOINT = os.environ.get("MERIDIES_HAT_ENDPOINT", "tcp://127.0.0.1:5556")


def start_hat_relay():
    try:
        import zmq
    except ImportError:
        print("  /live: pyzmq not installed, no obstacle relay", flush=True)
        return

    def run():
        import time as _t
        s = zmq.Context.instance().socket(zmq.SUB)
        s.setsockopt(zmq.CONFLATE, 1)
        s.setsockopt_string(zmq.SUBSCRIBE, "")
        s.setsockopt(zmq.RCVTIMEO, 1000)
        s.connect(HAT_ENDPOINT)
        while True:
            try:
                w = json.loads(s.recv())
            except zmq.Again:
                continue
            except ValueError:
                continue
            with LIVE_COND:
                LIVE.update(warnings=w if isinstance(w, list) else [], t=_t.time(), source="hat")
                LIVE_COND.notify_all()
    threading.Thread(target=run, daemon=True, name="hat-relay").start()
    print(f"  /live: listening for the hat on {HAT_ENDPOINT}", flush=True)


# ---------- "what's around me" (instant: maths + cache) and "tell me more" (the one live AI call) ----------
PEOPLE = {"user-a": "User A", "user-b": "User B", "maya": "Maya", "theo": "Theo"}   # display names (demo)
name_of = lambda a: PEOPLE.get(a, a)  # noqa: E731
TELL_ME_MORE_PROMPT = ("A blind person is standing here. These are their own notes left at this spot. Combine what you "
                       "see with what they recorded into three sentences. Do not state directions or distances — those "
                       "are supplied separately. Do not mention colours.")
SPOKEN_STYLE = ("Speak like a friend who has been here before, telling them what matters. Put the most important thing "
                "in the first four words. Never say 'I can see', 'it appears', 'there seems to be', 'in this image' or "
                "'based on the scan'. Never apologise or mention being an AI. Short, plain sentences.")


def audible(viewer: str, fam: str | None, bid: str) -> list[dict]:
    return [m for m in all_memories() if m["building_id"] == bid and can_see(m, viewer, fam)]


def around_here(bid: str, x: float, y: float, heading, viewer: str, fam=None, floor: int = 0, kind: str = "around",
                prev: str = "") -> dict:
    from connect.backend import spatial
    plan, mems = desc.load_plan(bid), audible(viewer, fam, bid)
    if kind == "arrival":
        return spatial.arrival(plan, x, y, heading, mems, viewer, name_of, floor, prev=prev)
    return spatial.around(plan, x, y, heading, mems, viewer, name_of, floor)


def tell_me_more(bid: str, x: float, y: float, heading, sweep, viewer: str, with_bearings: bool = True) -> dict:
    """Context: the first-person render here, the transcripts of memories within 5 m, the resolved bearings.
    One Gemini call. Directions and distances come from the resolver, never from the model."""
    import time as _t
    from connect.backend import gemini, spatial
    plan = desc.load_plan(bid)
    res = spatial.around(plan, x, y, heading, audible(viewer, None, bid), viewer, name_of)
    notes = [m for m in audible(viewer, None, bid) if m["id"] in res["memories_near"] and (m.get("transcript") or {}).get("text")]
    if plan["kind"] == "interior":
        sw = int(sweep) if sweep is not None else desc.nearest_sweep(plan, x, y)
        image, what = desc.first_person(bid, sw, heading or 0.0), "what is in front of them at eye level, from a 3D scan"
    else:
        image, what = desc.splat_crop(plan, x, y), "the spot seen from above, from a drone scan"
    lines = [TELL_ME_MORE_PROMPT, SPOKEN_STYLE, f"The image shows {what}."]
    lines.append("Their notes:" if notes else "There are no notes here yet; describe only what you see.")
    for m in notes[:6]:
        who = "their own note" if m["author_id"] == viewer else f"a note a friend, {name_of(m['author_id'])}, shared with them"
        lines.append(f"- ({who}) \"{m['transcript']['text']}\"")
    lines.append(f"For your understanding only, do not repeat: {res['bearings']}")
    t0 = _t.monotonic()
    text = gemini.generate([{"text": "\n".join(lines)}, gemini.image_part(image)], timeout=20, retries=1)
    print(f"  tell-me-more: {gemini.last_model} {_t.monotonic() - t0:.1f}s, {len(notes)} notes", flush=True)
    # up to six sentences in all: at most three measured ones (hazards first), then the model's three
    # (skipped when the person just heard them at this spot: repeating is worse than silence)
    bearings = spatial.clean(" ".join(res["hazard_lines"] + [res["memory_line"]]), max_sentences=3) if with_bearings else ""
    return {"bearings": bearings, "text": spatial.clean(text, max_sentences=3), "model": gemini.last_model,
            "notes": [m["id"] for m in notes]}


def gallery(q: dict) -> dict:
    """Places with their memories, computed from what this viewer may see right now.
    Author: all of their own memories. Family link: ONLY the owner's family memories."""
    from connect.backend import places
    viewer, fam = who(q)
    if fam is not None:
        mems = [m for m in all_memories() if m["author_id"] == fam and m["visibility"] == "family"]
        mode = "family"
    elif q.get("scope") == "shared":     # "Shared with me": family memories from people whose circle includes me
        mems = [m for m in all_memories() if m["visibility"] == "family" and m["author_id"] != viewer
                and in_circle(m["author_id"], viewer)]
        mode = "shared"
    else:
        mems = [m for m in all_memories() if viewer and m["author_id"] == viewer]
        mode = "author"
    mems = [m for m in mems if m["building_id"] in desc.BUILDINGS]
    plans = {}

    def plan_for(bid):
        if bid not in plans:
            plans[bid] = desc.load_plan(bid)
        return plans[bid]
    out = places.group([public(m, viewer) for m in mems], plan_for)
    for p in out:   # sharing with everyone is offered only in public spaces
        p["building"]["public"] = plan_for(p["building"]["id"])["building"].get("public") is True
        p["building"]["address"] = plan_for(p["building"]["id"])["building"].get("address")
    return {"mode": mode, "places": out, **({"owner": fam} if mode == "family" else {})}


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

    def hat_events(self):
        """Server-sent events: one line per hat button press, to every open gallery page."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        q: queue.Queue = queue.Queue()
        with HAT_LOCK:
            HAT_CLIENTS.append(q)
        try:
            self.wfile.write(b": hello\n\n"); self.wfile.flush()
            while True:
                try:
                    b = q.get(timeout=15)
                    self.wfile.write(f"data: {json.dumps({'button': b})}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            with HAT_LOCK:
                HAT_CLIENTS.remove(q)

    def live_stream(self):
        """Server-sent events for /live: the hat's current obstacle list, on change and at least once a second."""
        import time as _t
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                with LIVE_COND:
                    LIVE_COND.wait(timeout=1.0)
                    snap = dict(LIVE)
                fresh = bool(snap["t"]) and _t.time() - snap["t"] < 2
                msg = {"warnings": snap["warnings"] if fresh else [], "connected": fresh}
                self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode())
                self.wfile.flush()
        except (ConnectionError, OSError):
            pass

    # routes
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        if not parts or (len(parts) == 2 and parts[0] == "f"):     # gallery; /f/<token> = family view
            return self.file(FRONT / "gallery.html", "text/html; charset=utf-8")
        if parts == ["walk"]:                                       # the 3D walk ("Take me there")
            return self.file(FRONT / "index.html", "text/html; charset=utf-8")
        if parts == ["live"]:                                       # the hat: realtime, works offline
            return self.file(FRONT / "live.html", "text/html; charset=utf-8")
        if parts[0] == "data" and len(parts) == 2 and parts[1] in PUBLIC_DATA:
            return self.file(DATA / parts[1])
        if parts == ["api", "floorplan"]:
            bid = q.get("building") or DEFAULT_BUILDING
            if bid not in desc.BUILDINGS:
                return self.err(404, f"unknown building {bid}")
            fp = desc.load_plan(bid)
            fp.pop("meta", None)
            return self.send(200, fp)
        if parts == ["api", "view"]:
            bid = q.get("building") or DEFAULT_BUILDING
            if desc.BUILDINGS.get(bid, ("", ""))[1] != "interior":
                return self.err(404, "no first-person views for this building")
            try:
                png = view_png(bid, int(q["sweep"]), float(q.get("heading", 0)))
            except (KeyError, ValueError) as e:
                return self.err(400, f"bad request: {e}")
            except TimeoutError as e:
                return self.err(504, str(e))
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(png)))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            return self.wfile.write(png)
        if parts == ["api", "buildings"]:
            out = []
            for bid in desc.BUILDINGS:
                fp = desc.load_plan(bid)
                out.append(dict(id=bid, name=fp["building"]["name"], kind=fp["kind"], default=bid == DEFAULT_BUILDING,
                                public=fp["building"].get("public") is True))
            return self.send(200, out)
        if parts == ["api", "memories"]:
            (viewer, fam), bid = who(q), q.get("building_id")
            out = [public(m, viewer) for m in all_memories()
                   if can_see(m, viewer, fam) and (bid is None or m["building_id"] == bid)]
            return self.send(200, out)
        if parts == ["api", "gallery"]:
            return self.send(200, gallery(q))
        if parts == ["api", "around"]:
            # "what's around me": instant, no AI. Cached description + bearings from the spatial resolver.
            try:
                bid = q.get("building") or DEFAULT_BUILDING
                x, y = float(q["x"]), float(q["y"])
                heading = float(q["heading"]) if q.get("heading", "") != "" else None
                floor = int(q.get("floor") or 0)
            except (KeyError, ValueError) as e:
                return self.err(400, f"bad request: {e!r}")
            if bid not in desc.BUILDINGS:
                return self.err(404, "unknown building")
            viewer, fam = who(q)
            return self.send(200, around_here(bid, x, y, heading, viewer, fam, floor, q.get("kind", "around"), q.get("prev", "")))
        if parts == ["api", "circle"]:
            viewer = q.get("viewer", "")
            return self.send(200, {"members": circles().get(viewer, []) if viewer else [],
                                   "in_circles_of": sorted(a for a, ms in circles().items() if viewer and viewer in ms)})
        if parts == ["api", "voice"]:
            # SYSTEM speech only (never a memory): a cached clip, or 404 at once so the page uses the browser voice
            # while the clip is made in the background. Never waits on the AI.
            from connect.backend import voice
            text, style = (q.get("text") or "").strip(), q.get("style", "calm")
            if not VOICE_ON or not text or len(text) > 600 or style not in voice.STYLE:
                return self.err(404, "no clip")
            v = settings().get("voice", voice.DEFAULT_VOICE)
            p = voice.cached(text, style, v)
            if not p and q.get("wait") == "1" and VOICE_AUTOGEN:
                # a line the person asked for (where am I, tell me more): stream the neural voice as it is made, so
                # it starts in about a second; Gemini's version is made in the background for next time
                gen = voice.stream_edge(text, style, v)
                try:
                    first = next(gen)
                except Exception as e:  # noqa: BLE001
                    print(f"  voice: couldn't make a clip now: {e!r}"[:160], flush=True)
                    return self.err(503, "no voice right now")
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(first); self.wfile.flush()
                    for c in gen:
                        self.wfile.write(c); self.wfile.flush()
                except (ConnectionError, OSError):
                    pass                                     # the person cut it off: that's fine
                if voice.gemini_available():
                    voice.queue(text, style, v)
                return
            if p:
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg" if p.suffix == ".mp3" else "audio/wav")
                body = p.read_bytes()
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                return self.wfile.write(body)
            queued = voice.queue(text, style, v) if VOICE_AUTOGEN and q.get("queue") != "0" else False
            return self.send(404, {"error": "no clip yet", "queued": queued})
        if parts == ["api", "settings"]:
            from connect.backend import voice
            s = settings()
            every = [json.loads(f.read_text()) for f in MEM.glob("*.json") if ID_RE.match(f.stem)] if MEM.exists() else []
            n = sum(1 for m in every if m.get("is_sample"))
            return self.send(200, {"hide_samples": bool(s.get("hide_samples")), "samples": n, "real": len(every) - n,
                                   "voice": s.get("voice", voice.DEFAULT_VOICE), "voices": voice.VOICES,
                                   "voice_pending": voice.pending()})
        if parts == ["api", "hat", "events"]:
            return self.hat_events()
        if parts == ["api", "live", "stream"]:
            return self.live_stream()
        if parts == ["api", "family-link"]:
            tok = family_link_for(q.get("viewer", ""), create=False) if q.get("viewer") else None
            return self.send(200, {"token": tok, "url": f"/f/{tok}" if tok else None})
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] in ("audio", "cover") and ID_RE.match(parts[2]):
            try:
                m = load(parts[2])
            except FileNotFoundError:
                return self.err(404, "no such memory")
            if not can_see(m, *who(q)):
                return self.err(403, "private memory")
            if parts[3] == "audio":
                return self.file(MEM / m["audio_file"], m["audio_type"])
            try:
                p = cover_file(m)
            except Exception as e:  # noqa: BLE001 - no render possible right now
                return self.err(503, f"no cover yet: {e!r}"[:200])
            return self.file(p, "image/jpeg") if p else self.err(404, "no cover for this building")
        if (len(parts) == 5 and parts[:2] == ["api", "memories"] and parts[3] == "image"
                and parts[4] in ("aerial", "splat", "firstperson", "topdown", "aerial2", "topdown2") and ID_RE.match(parts[2])):
            try:
                m = load(parts[2])
            except FileNotFoundError:
                return self.err(404, "no such memory")
            if not can_see(m, *who(q)):
                return self.err(403, "private memory")
            return self.file(MEM / f"{m['id']}_{parts[4]}.jpg", "image/jpeg")
        if len(parts) == 1 and (FRONT / parts[0]).is_file():
            return self.file(FRONT / parts[0])
        return self.err(404, "not found")

    def do_POST(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        parts = [p for p in u.path.split("/") if p]
        if "family" in q:
            return self.err(403, "a family link is read-only")
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
            # Private by default, always. draft=1 comes from an interrupted recording.
            meta = dict(id=mid, position=pos, building_id=bid, author_id=author,
                        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        visibility="private", draft=q.get("draft") in ("1", "true"), share_request=None,
                        reports=[],   # reserved for listener reports (not implemented yet)
                        description=None, transcript={"status": "pending"}, audio_type=ctype, audio_file=fname)
            try:  # optional: where the person faced, and which sweep they stood at (interior)
                if q.get("recorded_at"):   # saved offline on the hat, uploaded later: keep when it was said
                    ra = datetime.fromisoformat(q["recorded_at"].replace("Z", "+00:00"))
                    if ra.tzinfo and ra <= datetime.now(timezone.utc):
                        meta["created_at"] = ra.astimezone(timezone.utc).isoformat(timespec="seconds")
                if "heading_deg" in q:
                    meta["heading_deg"] = round(float(q["heading_deg"]) % 360, 1)
                if q.get("sweep", "") != "":
                    meta["sweep"] = int(q["sweep"])
            except ValueError as e:
                return self.err(400, f"bad request: {e}")
            save(meta)
            enqueue(mid)                       # transcript + cover + description, in the background
            return self.send(201, public(meta, author))
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "frame" and ID_RE.match(parts[2]):
            # the hat camera frame taken with this recording: becomes the cover image
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "image/jpeg":
                return self.err(415, "send the frame as image/jpeg")
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_FRAME:
                return self.err(400, "frame missing or larger than 3 MB")
            data = self.rfile.read(n)
            try:
                m = load(parts[2])
            except FileNotFoundError:
                return self.err(404, "no such memory")
            if q.get("viewer") != m["author_id"]:
                return self.err(403, "only the author can add a photo")
            if data[:2] != b"\xff\xd8":
                return self.err(400, "not a JPEG")
            (MEM / f"{m['id']}_camera.jpg").write_bytes(data)
            return self.send(200, public(m, m["author_id"]))
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "process" and ID_RE.match(parts[2]):
            try:
                m, req = load(parts[2]), json.loads(self.body() or b"{}")
            except (FileNotFoundError, ValueError):
                return self.err(404, "no such memory")
            if req.get("viewer") != m["author_id"]:
                return self.err(403, "only the author")
            if (m.get("transcript") or {}).get("status") != "done":
                update(m["id"], lambda m: m.__setitem__("transcript", {"status": "pending"}))
            WORKER.submit(process, m["id"])
            return self.send(202, {"queued": m["id"]})
        if parts == ["api", "tell-me-more"]:
            # the ONLY live AI call, and only on an explicit request
            try:
                req = json.loads(self.body() or b"{}")
                bid = req["building"]
                x, y = float(req["x"]), float(req["y"])
                heading = None if req.get("heading") is None else float(req["heading"])
            except (KeyError, ValueError, TypeError) as e:
                return self.err(400, f"bad request: {e!r}")
            if bid not in desc.BUILDINGS:
                return self.err(404, "unknown building")
            try:
                return self.send(200, tell_me_more(bid, x, y, heading, req.get("sweep"), req.get("viewer") or "",
                                                   with_bearings=req.get("bearings", True) is not False))
            except LookupError as e:
                return self.err(503, str(e))
            except urllib.error.HTTPError as e:
                return self.err(429 if e.code == 429 else 502,
                                "The Gemini quota is used up for today." if e.code == 429 else f"Gemini answered {e.code}")
            except (urllib.error.URLError, TimeoutError, KeyError, IndexError) as e:
                return self.err(502, f"couldn't reach Gemini: {e!r}"[:200])
        if parts == ["api", "circle"]:
            # the author sets who is in their circle; members see the author's family memories under "Shared with me"
            req = json.loads(self.body() or b"{}")
            viewer, members = req.get("viewer") or "", req.get("members")
            if not viewer or not isinstance(members, list) or not all(isinstance(x, str) and x for x in members):
                return self.err(400, "viewer and a list of members required")
            with MEM_LOCK:
                c = circles()
                c[viewer] = sorted({x for x in members if x != viewer})
                CIRCLES.write_text(json.dumps(c, indent=1))
            return self.send(200, {"members": c[viewer]})
        if parts == ["api", "settings"]:
            req = json.loads(self.body() or b"{}")
            if not req.get("viewer"):
                return self.err(403, "only an author can change this")
            from connect.backend import voice
            s = settings()
            if "hide_samples" in req:
                s["hide_samples"] = bool(req.get("hide_samples"))
            if "voice" in req:
                if req["voice"] not in voice.VOICES:
                    return self.err(400, f"voice must be one of {voice.VOICES}")
                s["voice"] = req["voice"]
            SETTINGS.write_text(json.dumps(s))
            return self.send(200, {"hide_samples": bool(s.get("hide_samples")), "voice": s.get("voice", voice.DEFAULT_VOICE)})
        if parts == ["api", "hat", "button"]:
            req = json.loads(self.body() or b"{}")
            if req.get("button") not in HAT_BUTTONS:
                return self.err(400, f"button must be one of {sorted(HAT_BUTTONS)}")
            return self.send(200, {"delivered_to": hat_broadcast(req["button"])})
        if parts == ["api", "family-link"]:
            req = json.loads(self.body() or b"{}")
            viewer = req.get("viewer") or ""
            if not viewer:
                return self.err(400, "viewer required")
            if req.get("revoke"):
                revoke_family_link(viewer)
                return self.send(200, {"token": None, "url": None})
            tok = family_link_for(viewer)
            return self.send(200, {"token": tok, "url": f"/f/{tok}"})
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "decision" and ID_RE.match(parts[2]):
            try:
                req = json.loads(self.body() or b"{}")
                load(parts[2])
            except (FileNotFoundError, ValueError):
                return self.err(404, "no such memory")

            def decide(m):
                if req.get("viewer") != m["author_id"]:
                    return 403, "only the author decides who hears a memory"
                return apply_choice(m, req.get("choice"), str(req.get("reason", "chosen"))[:40])
            m, code, error = update(parts[2], decide)
            if error:
                return self.err(code, error)
            return self.send(200, public(m, m["author_id"]))
        if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "describe" and ID_RE.match(parts[2]):
            try:
                m, req = load(parts[2]), json.loads(self.body() or b"{}")
            except (FileNotFoundError, ValueError):
                return self.err(404, "no such memory")
            if not can_see(m, req.get("viewer", "")):
                return self.err(403, "private memory")
            try:
                d = desc.describe(m, MEM)
            except LookupError as e:
                return self.err(503, str(e))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    return self.err(429, "The free Gemini quota is used up for today on every model. It resets tomorrow.")
                if e.code == 503:
                    return self.err(503, "Gemini is overloaded right now. Try again in a minute.")
                return self.err(502, f"{e.url.split('?')[0]} answered {e.code}: {e.read()[:200].decode(errors='replace')}")
            except (urllib.error.URLError, KeyError, IndexError, TimeoutError) as e:
                return self.err(502, f"Couldn't describe this place: {e!r}"[:200])
            m, _, _ = update(m["id"], lambda m: m.__setitem__("description", d))
            return self.send(200, public(m, req.get("viewer")))
        return self.err(404, "not found")

    def do_PATCH(self):
        u = urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        if "family" in parse_qs(u.query):
            return self.err(403, "a family link is read-only")
        if len(parts) == 3 and parts[:2] == ["api", "memories"] and ID_RE.match(parts[2]):
            try:
                req = json.loads(self.body() or b"{}")
                load(parts[2])
            except (FileNotFoundError, ValueError):
                return self.err(404, "no such memory")

            def change(m):
                if req.get("viewer") != m["author_id"]:
                    return 403, "only the author can change visibility"
                if req.get("visibility") not in VISIBILITY:
                    return 400, "visibility must be private, family or community"
                # "community" is only ever a REQUEST (pending_review); private and family always work, at any time.
                return apply_choice(m, {"community": "share"}.get(req["visibility"], req["visibility"]), "toggle")
            m, code, error = update(parts[2], change)
            if error:
                return self.err(code, error)
            return self.send(200, public(m, m["author_id"]))
        return self.err(404, "not found")


if __name__ == "__main__":
    print(f"memory layer on http://localhost:{PORT}   gallery /   hat /live   walk /walk")
    start_hat_relay()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()

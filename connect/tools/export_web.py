"""Build web/: the Meridies site as plain website files plus one Vercel Python function for the live AI calls.

    python connect/tools/export_web.py              # writes HackPrinceton_2026/web (replaces it)
    python connect/tools/export_web.py --out DIR

What it does
  - pages:  frontend/*.html -> index.html (Meri home), gallery.html, walk.html, live.html; each page's <style>
            moves to css/<page>.css and every script to js/
  - data:   every read-only API answer the pages ask for (per demo viewer) is saved as JSON under data/api/;
            js/static-api.js answers those requests from the files, so the pages run unchanged
  - media:  memory audio, covers and description images, the first-person scan views, and the cached
            Gemini voice clips (as MP3) under media/
  - api/:   a Vercel Python function (api/index.py) running this repo's own server code for the calls that
            need a live AI or a computation: Meri chat, /api/hear, /api/voice (new lines), /api/around,
            /api/tell-me-more. Everything that would change data answers 403: the web copy is read-only.

Memories are read the way server.py reads them: MongoDB when MONGODB_URI is set, else connect/data/memories.
No .env or key is ever copied; the function reads GEMINI_API_KEY from the host's environment.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import io
import json
import math
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[2]          # HackPrinceton_2026
sys.path.insert(0, str(REPO))

from connect import server as srv                     # noqa: E402
from connect.backend import describe as desc          # noqa: E402
from connect.backend import voice                     # noqa: E402

FRONT, DATA, MEM = srv.FRONT, srv.DATA, srv.MEM
VIEWERS = ("user-a", "user-b")                        # the demo accounts the pages switch between
PAGES = {  # source page -> (output page, stylesheet)
    "home.html": ("index.html", "home.css"),
    "gallery.html": ("gallery.html", "gallery.css"),
    "index.html": ("walk.html", "walk.css"),
    "live.html": ("live.html", "live.css"),
}
IMAGE_KINDS = ("aerial", "splat", "firstperson", "topdown", "aerial2", "topdown2")
STATIC_GET = ("buildings", "floorplan", "memories", "gallery", "circle", "family-link", "settings")
DYNAMIC = ("POST /api/meri/chat", "POST /api/hear", "POST /api/tell-me-more", "POST /api/client-log",
           "GET /api/around", "GET /api/voice")
STATIC_VIEW_PX = 1280                                  # the renders' own width; re-encoded to keep the upload small
FUNCTION_VIEW_PX = 640                                 # tell-me-more sends the model at most 960 px anyway
SKIP_PARAMS = {"_"}
DEMO_ASSETS: dict = {}


def is_public_memory(memory: dict) -> bool:
    """Fail closed: family, private, drafts and unapproved sharing never enter an export."""
    return (memory.get("visibility") == "community" and not memory.get("draft", False)
            and (memory.get("share_request") or {}).get("status") in (None, "approved"))


def public_memories(memories: list[dict]) -> list[dict]:
    result = []
    for memory in memories:
        if not is_public_memory(memory):
            continue
        item = {k: v for k, v in memory.items()
                if k not in {"share_request", "reports", "decisions", "audioId", "_id"}}
        result.append(item)
    return result


@contextmanager
def public_snapshot(memories: list[dict]):
    """Every static API response must see the same sanitized, public-only dataset."""
    names = ("all_memories", "circles", "_links", "family_link_for", "settings")
    previous = {name: getattr(srv, name) for name in names}
    srv.all_memories = lambda include_hidden=False: memories
    srv.circles = lambda: {}
    srv._links = lambda: {}
    srv.family_link_for = lambda *args, **kwargs: None
    srv.settings = lambda: {"hide_samples": False, "voice": voice.DEFAULT_VOICE}
    try:
        yield
    finally:
        for name, value in previous.items():
            setattr(srv, name, value)


def public_voice_keys(memories: list[dict]) -> set[str]:
    """Only allow known public text, not the full cache of personalized speech."""
    texts = {"Hi, I'm Meri.", "Opening your digital gallery.", "Playing your most recent memory."}
    samples = json.loads((FRONT / "scene-samples.json").read_text(encoding="utf-8"))
    for building in samples.get("buildings", {}).values():
        for position in building.get("positions", {}).values():
            for entry in position.values():
                texts.update(value for value in entry.values() if isinstance(value, str))
    for memory in memories:
        texts.add((memory.get("description") or {}).get("text", ""))
        transcript = memory.get("transcript") or {}
        texts.add(transcript.get("text", "") if isinstance(transcript, dict) else transcript)
    texts = {text for text in texts if isinstance(text, str)}
    texts.update(sentence for text in list(texts) for sentence in re.split(r"(?<=[.!?])\s+", text) if sentence)
    return {voice.key(text, "calm", voice.DEFAULT_VOICE) for text in texts if text}


def snap_key(path: str, params: dict) -> str:
    """File name for one API answer. js/static-api.js computes the same name from a request URL."""
    name = path.strip("/")[len("api/"):].replace("/", "_")
    items = sorted((k, str(v)) for k, v in params.items() if k not in SKIP_PARAMS)
    if items:
        name += "__" + "__".join(f"{k}-{re.sub(r'[^A-Za-z0-9_.-]', '_', v)}" for k, v in items)
    return name + ".json"


def log(msg: str):
    print(msg, flush=True)


# ---------- pages, styles, scripts ----------
def build_pages(out: Path):
    (out / "css").mkdir(parents=True)
    (out / "js").mkdir()
    (out / "assets").mkdir()
    scripts = sorted(p.name for p in FRONT.glob("*.js") if p.name != "sw.js")
    for name in scripts:
        shutil.copy2(FRONT / name, out / "js" / name)
    for p in FRONT.glob("*.css"):
        shutil.copy2(p, out / "css" / p.name)
    for p in FRONT.iterdir():
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico"):
            shutil.copy2(p, out / "assets" / p.name)
    # the offline shell for /live: same files, new paths
    sw = (FRONT / "sw.js").read_text(encoding="utf-8")
    sw = sw.replace('"/live.js"', '"/js/live.js"').replace('"/speech.js"', '"/js/speech.js"')
    (out / "sw.js").write_text(sw, encoding="utf-8")

    script_re = re.compile(r'(<script\b[^>]*\bsrc=")/?([\w.-]+\.js)(")')
    css_re = re.compile(r'(<link\b[^>]*\bhref=")/?([\w.-]+\.css)(")')
    asset_re = re.compile(r'((?:src|href)=")/?([\w.-]+\.(?:png|jpe?g|svg|webp|ico))(")')
    for src, (dst, css_name) in PAGES.items():
        html = (FRONT / src).read_text(encoding="utf-8")
        styles = re.findall(r"<style>(.*?)</style>", html, flags=re.S)
        if len(styles) != 1:
            raise SystemExit(f"{src}: expected one <style> block, found {len(styles)}")
        (out / "css" / css_name).write_text(styles[0].strip() + "\n", encoding="utf-8")
        html = re.sub(r"<style>.*?</style>", f'<link rel="stylesheet" href="/css/{css_name}">', html, count=1, flags=re.S)
        for name in re.findall(r'<script\b[^>]*\bsrc="/?([\w.-]+\.js)"', html):
            if name not in scripts:
                raise SystemExit(f"{src} loads {name}, which is not in frontend/")
        html = script_re.sub(r"\1/js/\2\3", html)
        html = css_re.sub(r"\1/css/\2\3", html)
        html = asset_re.sub(r"\1/assets/\2\3", html)
        if "<head>" not in html:
            raise SystemExit(f"{src}: no <head> to put the static API shim in")
        # first script on the page, so it is in place before any page script asks the server for something
        html = html.replace("<head>", '<head><script src="/js/static-api.js"></script>', 1)
        if re.search(r"<script>(?!\s*</script>)", html):
            raise SystemExit(f"{src}: has an inline <script>; move it to a .js file first")
        (out / dst).write_text(html, encoding="utf-8")
        log(f"  page {src} -> {dst} (+ css/{css_name})")


# ---------- read-only API answers ----------
def snapshot_api(out: Path) -> list[str]:
    api = out / "data" / "api"
    api.mkdir(parents=True)
    srv.Handler.log_message = lambda *a: None
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    reqs: list[tuple[str, dict]] = [("/api/buildings", {}), ("/api/settings", {}), ("/api/floorplan", {})]
    reqs += [("/api/floorplan", {"building": b}) for b in desc.BUILDINGS]
    for v in VIEWERS:
        reqs += [("/api/memories", {"viewer": v}), ("/api/gallery", {"viewer": v}),
                 ("/api/gallery", {"viewer": v, "scope": "shared"}), ("/api/circle", {"viewer": v}),
                 ("/api/family-link", {"viewer": v})]
        reqs += [("/api/memories", {"viewer": v, "building_id": b}) for b in desc.BUILDINGS]
    names = []
    try:
        for path, params in reqs:
            url = base + path + ("?" + urlencode(params) if params else "")
            with urllib.request.urlopen(url, timeout=60) as r:
                body = json.loads(r.read())
            if path == "/api/settings":
                records = srv.all_memories(include_hidden=True)
                body = {"hide_samples": False, "samples": sum(bool(m.get("is_sample")) for m in records),
                        "real": sum(not m.get("is_sample") for m in records), "voice": voice.DEFAULT_VOICE,
                        "voices": [voice.DEFAULT_VOICE], "voice_pending": 0}
            if path == "/api/gallery" and params.get("scope") == "shared":
                from connect.backend import places
                demos = [m for m in srv.all_memories() if m.get("is_public_demo") and m.get("demo_shared")
                         and m["author_id"] != params.get("viewer")]
                if demos:
                    groups = places.group([srv.public(m, params.get("viewer")) for m in demos], desc.load_plan)
                    body = {"mode": "shared", "places": groups, "public_demo": True}
            name = snap_key(path, params)
            (api / name).write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
            names.append(name)
    finally:
        httpd.shutdown()
    log(f"  {len(names)} API answers saved")
    return names


# ---------- media ----------
def view_file(bid: str, sweep: int, k: int) -> bytes | None:
    """The cached first-person render the server would send for /api/view (JPEG, else the PNG master)."""
    from PIL import Image
    yaw = f"{0.0:.3f},{k * math.pi / 2:.3f}"           # interior_render.sr_string: pitch first
    stem = f"{bid}_ss{sweep + 1}_sr{yaw}"                 # ss = sweep + SS_BASE (1); the name itself has dots
    jpg, png = (DATA / "cache" / "interior" / f"{stem}.{ext}" for ext in ("jpg", "png"))
    if jpg.exists():
        return jpg.read_bytes()
    if png.exists():
        buf = io.BytesIO()
        Image.open(png).convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue()
    return None


def copy_media(out: Path, memories: list[dict]) -> dict:
    if any(not is_public_memory(m) for m in memories):
        raise ValueError("refusing to copy non-public memory media")
    from PIL import Image
    audio_ext = {}
    for m in memories:
        d = out / "media" / "memories" / m["id"]
        d.mkdir(parents=True, exist_ok=True)
        asset = DEMO_ASSETS.get(m["id"])
        a = asset["audio"] if asset else MEM / m.get("audio_file", "")
        if a.is_file():
            ext = a.suffix.lstrip(".")
            shutil.copy2(a, d / f"audio.{ext}")
            audio_ext[m["id"]] = ext
        try:
            if asset:
                k = round(((-asset["heading"]) % 360) / 90) % 4
                view = view_file(asset["building"], asset["sweep"], k)
                cover = None
                if view:
                    (d / "cover.jpg").write_bytes(view)
            else:
                cover = srv.cover_file(m)            # already made for every saved memory; renders only if missing
        except Exception as e:  # noqa: BLE001
            cover = None
            log(f"  ! no cover for {m['id'][:8]}: {e!r}"[:120])
        if cover:
            shutil.copy2(cover, d / "cover.jpg")
        for kind in IMAGE_KINDS:
            p = MEM / f"{m['id']}_{kind}.jpg"
            if p.exists():
                shutil.copy2(p, d / f"{kind}.jpg")
    log(f"  media for {len(memories)} memories ({len(audio_ext)} recordings)")

    n = missing = 0
    for bid, (_, kind) in desc.BUILDINGS.items():
        if kind != "interior":
            continue
        for s in desc.load_plan(bid)["sweeps"]:
            for k in range(4):
                jpg = view_file(bid, s["index"], k)
                if jpg is None:
                    missing += 1
                    continue
                for root, px, quality in ((out / "media" / "views", STATIC_VIEW_PX, 74),
                                          (out / "api" / "_app" / "views", FUNCTION_VIEW_PX, 78)):
                    p = root / bid / f"{s['index']}_{k}.jpg"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    img = Image.open(io.BytesIO(jpg)).convert("RGB")
                    img.thumbnail((px, px))
                    img.save(p, "JPEG", quality=quality, optimize=True, progressive=True)
                n += 1
    log(f"  {n} scan views ({missing} never rendered: those spots show no photo)")
    return audio_ext


def convert_voice(out: Path, allowed_keys: set[str]) -> list[str]:
    """Cached Gemini clips (WAV) -> MP3, named by voice.key(), so prepared lines play without the function."""
    src = voice.CACHE / voice.DEFAULT_VOICE
    dst = out / "media" / "voice"
    dst.mkdir(parents=True, exist_ok=True)
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not src.exists():
        log("  ! ffmpeg or the voice cache is missing: no prepared voice clips (new lines still come from the function)")
        return []
    def convert(wav):
        mp3 = dst / f"{wav.stem}.mp3"
        r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(wav), "-threads", "1", "-ac", "1", "-b:a", "48k", str(mp3)],
                           capture_output=True)
        if r.returncode == 0 and mp3.exists():
            return wav.stem
        else:
            mp3.unlink(missing_ok=True)
            return None
    sources = [wav for wav in sorted(src.glob("*.wav")) if wav.stem in allowed_keys]
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = [key for key in pool.map(convert, sources) if key]
    log(f"  {len(keys)} voice clips as MP3")
    return keys


# ---------- the Vercel function's copy of the server ----------
def build_function(out: Path, memories: list[dict]) -> Path:
    if any(not is_public_memory(m) for m in memories):
        raise ValueError("refusing to package non-public memory records")
    app = out / "api" / "_app" / "connect"
    (app / "backend").mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy2(REPO / "connect" / "server.py", app / "server.py")
    for p in (REPO / "connect" / "backend").glob("*.py"):
        shutil.copy2(p, app / "backend" / p.name)
    fn_data = app / "data"
    (fn_data / "memories").mkdir(parents=True)
    for fname, _ in desc.BUILDINGS.values():
        shutil.copy2(DATA / fname, fn_data / fname)
        plan = json.loads((DATA / fname).read_text())
        if plan.get("backdrop", {}).get("image"):
            shutil.copy2(DATA / plan["backdrop"]["image"], fn_data / plan["backdrop"]["image"])
    (fn_data / "circles.json").write_text("{}", encoding="utf-8")
    (fn_data / "settings.json").write_text(json.dumps({"hide_samples": False, "voice": voice.DEFAULT_VOICE}), encoding="utf-8")
    for m in memories:   # the records only; audio and images are static files
        (fn_data / "memories" / f"{m['id']}.json").write_text(json.dumps(m, indent=1, default=str), encoding="utf-8")
    for leaked in out.rglob(".env*"):
        raise SystemExit(f"refusing to build: {leaked} would be deployed")
    log(f"  function: server code + {len(memories)} memory records")
    return fn_data


def write_shim(out: Path, audio_ext: dict, voice_keys: list[str]):
    tpl = (Path(__file__).resolve().parent / "web_template" / "static-api.js").read_text(encoding="utf-8")
    config = {
        "staticGet": list(STATIC_GET), "dynamic": list(DYNAMIC), "skipParams": sorted(SKIP_PARAMS),
        "audio": audio_ext, "voiceKeys": voice_keys, "voiceName": voice.DEFAULT_VOICE,
        "promptVersion": voice.PROMPT_VERSION,
    }
    (out / "js" / "static-api.js").write_text(
        tpl.replace("/*__CONFIG__*/null", json.dumps(config, separators=(",", ":"))), encoding="utf-8")


def copy_template(out: Path):
    tpl = Path(__file__).resolve().parent / "web_template"
    for p in tpl.rglob("*"):
        if p.is_file() and p.name != "static-api.js":
            dest = out / p.relative_to(tpl)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO / "web")
    ap.add_argument("--no-voice", action="store_true", help="skip converting the voice cache (faster)")
    ap.add_argument("--demo-samples", action="store_true", help="add independent public copies of verified scripted samples; never modifies Mongo")
    args = ap.parse_args()
    out = args.out.resolve()
    if out == REPO or REPO.is_relative_to(out) or out == REPO / "connect":
        raise SystemExit(f"refusing to replace {out}")
    if out.is_relative_to(REPO / "connect") or out.is_relative_to(REPO / ".git"):
        raise SystemExit(f"refusing to replace source or Git paths: {out}")
    if out.exists() and any(out.iterdir()):
        if not (out / "vercel.json").exists():
            raise SystemExit(f"{out} exists and is not an earlier export (no vercel.json); pick another --out")
        for child in out.iterdir():          # empty it rather than remove it: Windows keeps an open folder
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)

    from connect.backend.memory_repository import MemoryRepository
    srv.REPOSITORY = MemoryRepository(MEM)                # read-only use: no migrate()
    log(f"memories from {'MongoDB' if srv.REPOSITORY.db is not None else 'local files'}")
    all_records = srv.all_memories(include_hidden=True)
    memories = public_memories(all_records)
    log(f"PUBLIC ONLY: {len(memories)} included; {len(all_records)-len(memories)} private/family/draft records excluded")
    if args.demo_samples:
        from connect.tools.demo_export import build_demo_samples
        additions, assets = build_demo_samples(all_records, memories, MEM, desc.load_plan)
        DEMO_ASSETS.update(assets)
        memories.extend(additions)
        log(f"PUBLIC DEMO: {len(additions)} new scripted copies added; source visibility and Mongo unchanged")

    log("pages")
    build_pages(out)
    copy_template(out)
    log("data")
    with public_snapshot(memories):
        snapshot_api(out)
    (out / "data").mkdir(exist_ok=True)
    shutil.copy2(FRONT / "scene-samples.json", out / "data" / "scene-samples.json")
    for name in srv.PUBLIC_DATA:
        shutil.copy2(DATA / name, out / "data" / name)
    log("function")
    build_function(out, memories)
    log("media")
    audio_ext = copy_media(out, memories)
    voice_keys = [] if args.no_voice else convert_voice(out, public_voice_keys(memories))
    write_shim(out, audio_ext, voice_keys)
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    count = sum(1 for p in out.rglob("*") if p.is_file())
    log(f"done: {out}  ({count} files, {size / 2**20:.1f} MB)")


if __name__ == "__main__":
    main()

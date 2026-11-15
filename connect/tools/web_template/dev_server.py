"""Run the web copy locally the way Vercel serves it: static files with clean URLs, and /api/* -> api/index.py.

    python dev_server.py                 # http://localhost:8080
    python dev_server.py --port 9000

The AI calls need GEMINI_API_KEY in the environment. Without it, the .env of the folder above the repo is used
(the same file the local server reads), when this web copy sits inside the repo.
Stdlib only, plus Pillow for "tell me more" in the yard.
"""
from __future__ import annotations

import argparse
import mimetypes
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "api"))
import index  # noqa: E402  the Vercel function

KIT = HERE.parents[1]                       # web/ -> repo -> the folder holding .env
if (KIT / ".env").exists():
    index.gemini.KIT = KIT                  # gemini.secret() reads the key from there when it is not in the env
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/webm", ".webm")
mimetypes.add_type("audio/mpeg", ".mp3")


class Dev(index.handler):
    def log_message(self, fmt, *args):
        print(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}", flush=True)

    def do_GET(self):
        if urlparse(self.path).path.startswith("/api/"):
            return super().do_GET()
        return self.static()

    def static(self):
        rel = unquote(urlparse(self.path).path).lstrip("/")
        if rel.startswith("api/") or rel.startswith(".") or "/." in rel:
            return self.err(404, "not found")
        p = (HERE / rel).resolve()
        if p.is_dir():
            p = p / "index.html"
        elif not p.exists() and (HERE / f"{rel}.html").is_file():
            p = (HERE / f"{rel}.html").resolve()       # clean URLs: /gallery -> gallery.html
        if not p.is_relative_to(HERE) or not p.is_file() or p.suffix == ".py":
            return self.err(404, "not found")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        self.send(200, p.read_bytes(), ctype)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    port = ap.parse_args().port
    print(f"Meridies web copy on http://localhost:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Dev).serve_forever()

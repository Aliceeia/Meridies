"""Vercel Python function: the live part of the Meridies web copy.

It runs the repo's own server code (copied to api/_app by connect/tools/export_web.py) for the calls that need
a live AI or a computation, and refuses everything that would change data (the web copy is read-only).

    POST /api/meri/chat      Meri's answer (Gemini, over the memories and places saved at export)
    POST /api/hear           a short spoken command -> text (Gemini)
    POST /api/tell-me-more   what is here, from the scan view and nearby notes (Gemini)
    GET  /api/around         what's around this spot (no AI)
    GET  /api/voice          a spoken line not among the prepared clips (Gemini TTS)

Environment: GEMINI_API_KEY (required for the AI calls), GEMINI_MODEL (optional).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse

APP = Path(__file__).resolve().parent / "_app"
sys.path.insert(0, str(APP))
os.environ.setdefault("MERIDIES_AUTO_PROCESS", "0")      # nothing is saved here, so nothing to process

from connect import server as srv                          # noqa: E402
from connect.backend import describe as desc               # noqa: E402
from connect.backend import gemini, voice                  # noqa: E402

voice.CACHE = Path(tempfile.gettempdir()) / "meridies-voice"   # the bundle is read-only; /tmp is not
VIEWS = APP / "views"


def first_person(bid: str, sweep: int, heading_deg: float) -> bytes:
    """The saved eye-level scan view nearest the heading (the server picks among the same four)."""
    k = round(((-heading_deg) % 360) / 90) % 4
    p = VIEWS / bid / f"{int(sweep)}_{k}.jpg"
    if not p.exists():
        raise LookupError("No scan view was saved for this spot.")
    return p.read_bytes()


desc.first_person = first_person
srv.view_png = first_person

GET_OK = {("api", "around"), ("api", "voice")}
POST_OK = {("api", "meri", "chat"), ("api", "hear"), ("api", "tell-me-more"), ("api", "client-log")}
READ_ONLY = "This is the read-only web copy of Meridies. Recording, sharing and settings need the local server."


class handler(srv.Handler):
    def _route(self) -> tuple:
        """vercel.json sends /api/<path> here as /api/index?__path=<path>; put the original path back."""
        u = urlparse(self.path)
        q = parse_qsl(u.query, keep_blank_values=True)
        orig = next((v for k, v in q if k == "__path"), None)
        if orig is not None:
            rest = [(k, v) for k, v in q if k != "__path"]
            self.path = "/api/" + orig.lstrip("/") + ("?" + urlencode(rest) if rest else "")
        return tuple(p for p in urlparse(self.path).path.split("/") if p)

    def do_GET(self):
        if self._route() in GET_OK:
            return super().do_GET()
        return self.err(404, "Not part of this web copy.")

    def do_POST(self):
        if self._route() in POST_OK:
            return super().do_POST()
        return self.err(403, READ_ONLY)

    def do_PATCH(self):
        return self.err(403, READ_ONLY)

    do_PUT = do_DELETE = do_PATCH

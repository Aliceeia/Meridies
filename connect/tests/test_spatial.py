"""Spatial resolver and the one live AI call.   pytest connect/tests/test_spatial.py

- a memory anchored within 1 m of a hazard is linked to it (geometry only)
- "what's around me" never calls Gemini
- "tell me more" is one call with: the first-person render, transcripts within 5 m (only ones the viewer may hear),
  the resolved bearings; the model is told not to state directions or distances
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import connect.server as srv  # noqa: E402
from connect.backend import gemini, spatial  # noqa: E402

BID = "zvKHR9DHNjJ"
PLAN = srv.desc.load_plan(BID)
CHANDELIER = next(h for h in PLAN["hazards"] if h["kind"] == "chandelier")


def test_words():
    assert spatial.height_words(1.6) == "one metre six"
    assert spatial.height_words(2.0) == "two metres"
    assert spatial.where_words(1.4, -40) == "two steps ahead on your left"
    assert spatial.where_words(1.4, 90) == "two steps to your right"
    assert spatial.where_words(1.4, None) == "two steps away"
    assert spatial.where_words(0.2, 10) == "right here"


def test_hazard_links_only_memories_within_one_metre():
    hx, hy = CHANDELIER["position"][:2]
    near = dict(id="a", author_id="me", position=dict(x=hx + 0.6, y=hy), created_at="2026-10-10T10:00:00+00:00",
                transcript=dict(text="Careful with this one. It swings."))
    far = dict(id="b", author_id="me", position=dict(x=hx + 1.3, y=hy), created_at="2026-10-10T10:00:00+00:00",
               transcript=dict(text="Something else."))
    assert [m["id"] for m in spatial.hazard_notes(CHANDELIER, [far, near])] == ["a"]
    # standing 1.4 m from it, facing it: "Chandelier, one metre six, two steps ahead. You left a note here ...: '...'."
    line = spatial.hazard_line(CHANDELIER, hx, hy - 1.4, 0.0, [near, far], "me", lambda a: a)
    assert line.startswith("Chandelier, one metre six, two steps ahead.")
    assert "You left a note here" in line and "'Careful with this one'" in line and "Something else" not in line


@pytest.fixture()
def api(tmp_path, monkeypatch):
    for k, v in dict(MEM=tmp_path, LINKS=tmp_path / "l.json", CIRCLES=tmp_path / "c.json", SETTINGS=tmp_path / "s.json",
                     AUTO_PROCESS=False).items():
        monkeypatch.setattr(srv, k, v)
    calls = []

    def fake_generate(parts, **k):
        calls.append(parts)
        return "Three sentences."
    monkeypatch.setattr(gemini, "generate", fake_generate)
    monkeypatch.setattr(srv.desc, "first_person", lambda bid, sweep, heading: b"\xff\xd8render")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def record(x, y, text, author="user-a", vis=None):
        m = call("POST", f"/api/memories?x={x}&y={y}&z=1.5&building_id={BID}&author_id={author}", b"\x1aE\xdf\xa3x", "audio/webm")[1]
        srv.update(m["id"], lambda r: r.update(transcript=dict(status="done", text=text), description=dict(text=f"Cached: {text}"),
                                               **({"visibility": vis} if vis else {})))
        return m
    yield call, record, calls
    httpd.shutdown()


def test_around_is_instant_and_never_calls_gemini(api):
    call, record, calls = api
    hx, hy = CHANDELIER["position"][:2]
    record(hx + 0.5, hy, "Careful, the chandelier here hangs low.")
    code, r = call("GET", f"/api/around?viewer=user-a&building={BID}&x={hx}&y={hy - 1.4}&heading=0")
    assert code == 200 and calls == []
    print(r["bearings"])
    assert "Chandelier, one metre six" in r["bearings"] and "You left a note here" in r["bearings"]
    assert r["description"] == "Cached: Careful, the chandelier here hangs low."
    # someone else can't hear it, so it isn't linked or counted for them
    other = call("GET", f"/api/around?viewer=user-b&building={BID}&x={hx}&y={hy - 1.4}&heading=0")[1]
    assert "left a note" not in other["bearings"] and other["description"] is None


def test_tell_me_more_is_one_call_with_render_notes_and_bearings(api):
    call, record, calls = api
    hx, hy = CHANDELIER["position"][:2]
    record(hx + 0.5, hy, "Careful, the chandelier here hangs low.")
    record(hx + 9, hy + 9, "Far away note.")                                # more than 5 m away
    record(hx - 0.5, hy, "Someone else's private note.", author="user-b")    # not audible to user-a
    code, r = call("POST", "/api/tell-me-more", {"viewer": "user-a", "building": BID, "x": hx, "y": hy - 1.4, "heading": 0, "sweep": 6})
    assert code == 200 and len(calls) == 1 and r["text"] == "Three sentences."
    text = calls[0][0]["text"]
    assert srv.TELL_ME_MORE_PROMPT in text and "Do not state directions or distances" in text
    assert "Careful, the chandelier" in text and "Far away" not in text and "private note" not in text
    assert calls[0][1]["inline_data"]["mime_type"] == "image/jpeg"
    assert "Chandelier, one metre six" in r["bearings"]
    # a family link can't ask
    assert call("POST", "/api/tell-me-more?family=x", {"building": BID, "x": 0, "y": 0})[0] == 403

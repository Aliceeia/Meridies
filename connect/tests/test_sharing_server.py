"""Server rules for the after-recording flow.   pytest connect/tests/test_sharing_server.py

- every new memory is private; an interrupted take is a private draft
- sharing is refused outside public spaces (missing flag = not public)
- sharing in a public space only creates a pending_review request: nobody else can hear it
- the author can always make it private again; only the author decides
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

BID = "test-building"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "MEM", tmp_path)
    monkeypatch.setattr(srv, "AUTO_PROCESS", False)   # no Gemini calls from tests
    flags = {"public": False}
    monkeypatch.setattr(srv.desc, "load_plan", lambda bid: {"building": {"id": bid, **({"public": True} if flags["public"] else {})}})
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

    def record(draft=False, author="user-a"):
        q = f"x=1&y=2&z=1.5&building_id={BID}&author_id={author}" + ("&draft=1" if draft else "")
        return call("POST", f"/api/memories?{q}", b"\x1aE\xdf\xa3fake-webm", "audio/webm")[1]

    yield call, record, flags
    httpd.shutdown()


def test_new_memory_is_private_and_reserves_reports(api):
    call, record, _ = api
    m = record()
    assert m["visibility"] == "private" and m["draft"] is False and m["share_request"] is None and m["reports"] == []


def test_interrupted_recording_is_a_private_draft(api):
    m = api[1](draft=True)
    assert m["visibility"] == "private" and m["draft"] is True


def test_share_refused_in_non_public_space(api):
    call, record, flags = api
    m = record()
    code, body = call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-a", "choice": "share"})
    assert code == 403 and "public spaces" in body["error"]
    code, _ = call("PATCH", f"/api/memories/{m['id']}", {"viewer": "user-a", "visibility": "community"})
    assert code == 403
    assert call("GET", f"/api/memories?viewer=user-a&building_id={BID}")[1][0]["visibility"] == "private"


def test_share_in_public_space_is_pending_review_not_public(api):
    call, record, flags = api
    flags["public"] = True
    m = record()
    code, body = call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-a", "choice": "share"})
    assert code == 200
    assert body["share_request"]["status"] == "pending_review"
    assert body["visibility"] == "private"
    assert body["share_request"]["review"]["reviewer"] == "stub"
    # nobody else can see or hear it before review
    assert call("GET", f"/api/memories?viewer=user-b&building_id={BID}")[1] == []
    assert call("GET", f"/api/memories/{m['id']}/audio?viewer=user-b")[0] == 403
    # the old toggle can't bypass review either
    m2 = record()
    code, body = call("PATCH", f"/api/memories/{m2['id']}", {"viewer": "user-a", "visibility": "community"})
    assert code == 200 and body["visibility"] == "private" and body["share_request"]["status"] == "pending_review"


def test_author_can_always_make_private_again(api):
    call, record, flags = api
    flags["public"] = True
    m = record()
    call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-a", "choice": "share"})
    code, body = call("PATCH", f"/api/memories/{m['id']}", {"viewer": "user-a", "visibility": "private"})
    assert code == 200 and body["visibility"] == "private" and body["share_request"]["status"] == "withdrawn"


def test_decide_later_and_keep_private(api):
    call, record, _ = api
    m = record()
    assert call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-a", "choice": "later"})[1]["draft"] is True
    body = call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-a", "choice": "private"})[1]
    assert body["draft"] is False and body["visibility"] == "private"


def test_only_the_author_decides(api):
    call, record, flags = api
    flags["public"] = True
    m = record()
    assert call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-b", "choice": "share"})[0] == 403
    assert call("POST", f"/api/memories/{m['id']}/decision", {"viewer": "user-a", "choice": "everyone"})[0] == 400

"""Gallery rules.   pytest connect/tests/test_gallery_server.py

- visibility is private (default) | family | community; only the author changes it
- a family link sees ONLY the owner's family memories, and can never write
- private audio, covers and images answer 403 to everyone but the author
- places exist only where a visible memory was saved; nothing about movement is stored
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
from connect.backend import places  # noqa: E402

BID = "zvKHR9DHNjJ"   # the real interior plan: places come from its rooms
JPEG = b"\xff\xd8\xff\xe0fake-jpeg"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "MEM", tmp_path)
    monkeypatch.setattr(srv, "LINKS", tmp_path / "links.json")
    monkeypatch.setattr(srv, "CIRCLES", tmp_path / "circles.json")
    monkeypatch.setattr(srv, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(srv, "AUTO_PROCESS", False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def call(method, path, body=None, ctype="application/json", raw=False):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                b = r.read()
                return r.status, (b if raw else json.loads(b))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    plan = srv.desc.load_plan(BID)

    def record(sweep=4, author="user-a"):
        s = next(s for s in plan["sweeps"] if s["index"] == sweep)
        q = (f"x={s['position']['x']}&y={s['position']['y']}&z=1.5&sweep={sweep}&heading_deg=0"
             f"&building_id={BID}&author_id={author}")
        return call("POST", f"/api/memories?{q}", b"\x1aE\xdf\xa3fake-webm", "audio/webm")[1]

    def vis(m, v, viewer="user-a"):
        return call("PATCH", f"/api/memories/{m['id']}", {"viewer": viewer, "visibility": v})

    yield call, record, vis
    httpd.shutdown()


def test_three_levels_and_only_the_author_changes_them(api):
    call, record, vis = api
    m = record()
    assert m["visibility"] == "private" and m["transcript"] == {"status": "pending"}
    assert vis(m, "family")[1]["visibility"] == "family"
    assert vis(m, "family", viewer="user-b")[0] == 403
    assert vis(m, "everyone")[0] == 400
    assert vis(m, "private")[1]["visibility"] == "private"


def test_family_link_sees_only_family(api):
    call, record, vis = api
    priv, fam, other = record(), record(), record(author="user-b")
    vis(fam, "family")
    vis(other, "family", viewer="user-b")
    tok = call("POST", "/api/family-link", {"viewer": "user-a"})[1]["token"]
    g = call("GET", f"/api/gallery?family={tok}")[1]
    ids = [m["id"] for p in g["places"] for m in p["memories"]]
    assert g["mode"] == "family" and ids == [fam["id"]]
    assert call("GET", f"/api/memories/{fam['id']}/audio?family={tok}", raw=True)[0] == 200
    for path in ("audio", "cover", "image/firstperson"):
        assert call("GET", f"/api/memories/{priv['id']}/{path}?family={tok}")[0] == 403
        assert call("GET", f"/api/memories/{priv['id']}/{path}?viewer=user-b")[0] == 403
    assert call("GET", f"/api/memories/{other['id']}/audio?family={tok}")[0] == 403   # someone else's family
    assert call("GET", "/api/gallery?family=not-a-token")[1]["places"] == []


def test_family_link_is_read_only(api):
    call, record, vis = api
    m = record()
    vis(m, "family")
    tok = call("POST", "/api/family-link", {"viewer": "user-a"})[1]["token"]
    assert call("PATCH", f"/api/memories/{m['id']}?family={tok}", {"viewer": "user-a", "visibility": "private"})[0] == 403
    assert call("POST", f"/api/memories/{m['id']}/decision?family={tok}", {"viewer": "user-a", "choice": "private"})[0] == 403
    assert call("POST", f"/api/memories?family={tok}&x=0&y=0&z=0&building_id={BID}&author_id=user-a",
                b"fake", "audio/webm")[0] == 403
    # revoking the link closes it
    call("POST", "/api/family-link", {"viewer": "user-a", "revoke": True})
    assert call("GET", f"/api/gallery?family={tok}")[1]["places"] == []


def test_camera_frame_becomes_the_cover_and_only_the_author_sends_it(api):
    call, record, _ = api
    m = record()
    assert call("POST", f"/api/memories/{m['id']}/frame?viewer=user-b", JPEG, "image/jpeg")[0] == 403
    code, body = call("POST", f"/api/memories/{m['id']}/frame?viewer=user-a", JPEG, "image/jpeg")
    assert code == 200 and body["cover_source"] == "camera"
    assert call("GET", f"/api/memories/{m['id']}/cover?viewer=user-a", raw=True)[1] == JPEG


def test_places_come_only_from_visible_memories(api):
    call, record, vis = api
    a, b, c = record(sweep=4), record(sweep=4), record(sweep=0)
    g = call("GET", "/api/gallery?viewer=user-a")[1]
    assert sum(p["memory_count"] for p in g["places"]) == 3
    assert all(p["memory_count"] == len(p["memories"]) and p["latest_at"] for p in g["places"])
    same = [p for p in g["places"] if a["id"] in {m["id"] for m in p["memories"]}][0]
    assert b["id"] in {m["id"] for m in same["memories"]}
    # another viewer sees no places at all: a place never reveals that a private memory exists
    assert call("GET", "/api/gallery?viewer=user-b")[1]["places"] == []
    # nothing stores a place or a path: the only files are the memories themselves
    assert {p.suffix for p in srv.MEM.iterdir()} <= {".json", ".webm"}


def test_circle_members_see_only_family_under_shared_with_me(api):
    call, record, vis = api
    priv, fam = record(), record()
    vis(fam, "family")
    shared = lambda who: [m["id"] for p in call("GET", f"/api/gallery?viewer={who}&scope=shared")[1]["places"]  # noqa: E731
                          for m in p["memories"]]
    assert shared("user-b") == []                                    # not in the circle yet
    assert call("GET", f"/api/memories/{fam['id']}/audio?viewer=user-b")[0] == 403
    assert call("POST", "/api/circle", {"viewer": "user-a", "members": ["user-b"]})[1]["members"] == ["user-b"]
    assert shared("user-b") == [fam["id"]]
    assert call("GET", f"/api/memories/{fam['id']}/audio?viewer=user-b", raw=True)[0] == 200
    assert call("GET", f"/api/memories/{priv['id']}/audio?viewer=user-b")[0] == 403    # private stays private
    assert call("GET", "/api/circle?viewer=user-b")[1]["in_circles_of"] == ["user-a"]
    assert call("PATCH", f"/api/memories/{fam['id']}", {"viewer": "user-b", "visibility": "private"})[0] == 403
    call("POST", "/api/circle", {"viewer": "user-a", "members": []})   # removed: access ends at once
    assert shared("user-b") == []


def test_place_of_is_pure_and_uses_rooms():
    plan = srv.desc.load_plan(BID)
    s = next(s for s in plan["sweeps"] if s["index"] == 4)
    m = dict(building_id=BID, position=dict(x=s["position"]["x"], y=s["position"]["y"]), sweep=4)
    pid, name = places.place_of(m, plan)
    assert pid.startswith(BID + ".") and name
    yard = srv.desc.load_plan("1PVeAYmPNJz")
    z = yard["zones"][1]
    pid, name = places.place_of(dict(building_id="1PVeAYmPNJz", position=dict(x=z["center"][0], y=z["center"][1])), yard)
    assert pid == "1PVeAYmPNJz.zone1" and name == z["name"]

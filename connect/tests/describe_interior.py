"""Part 4 check: create an interior memory via the API (reusing a recorded test clip) and describe it.

    python connect/tests/describe_interior.py [sweep] [heading_deg] [out_dir]
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL = "http://localhost:8765"
SWEEP = int(sys.argv[1]) if len(sys.argv) > 1 else 6
HEADING = float(sys.argv[2]) if len(sys.argv) > 2 else 270.0
OUT = Path(sys.argv[3]) if len(sys.argv) > 3 else Path(".")
BID = "zvKHR9DHNjJ"

fp = json.loads(urllib.request.urlopen(f"{URL}/api/floorplan?building={BID}").read())
s = next(s for s in fp["sweeps"] if s["index"] == SWEEP)
clip = next((Path(__file__).resolve().parents[1] / "data" / "memories").glob("*.webm")).read_bytes()
q = f"x={s['position']['x']}&y={s['position']['y']}&z=1.5&building_id={BID}&author_id=user-a&sweep={SWEEP}&heading_deg={HEADING}"
m = json.loads(urllib.request.urlopen(urllib.request.Request(f"{URL}/api/memories?{q}", data=clip, method="POST",
                                                               headers={"Content-Type": "audio/webm"})).read())
print("memory", m["id"], "sweep", m.get("sweep"), "heading", m.get("heading_deg"), "room", s.get("room"))
req = urllib.request.Request(f"{URL}/api/memories/{m['id']}/describe", data=json.dumps({"viewer": "user-a"}).encode(),
                             method="POST", headers={"Content-Type": "application/json"})
try:
    d = json.loads(urllib.request.urlopen(req, timeout=300).read())["description"]
    print("DESCRIPTION:", d["text"])
    print("images sent:", [i["label"] for i in d["images"]])
except urllib.error.HTTPError as e:
    print("describe ->", e.code, json.loads(e.read())["error"][:300])
for kind in ("firstperson", "aerial2", "topdown2"):
    try:
        b = urllib.request.urlopen(f"{URL}/api/memories/{m['id']}/image/{kind}?viewer=user-a").read()
        (OUT / f"p4_{kind}.jpg").write_bytes(b)
        print(f"  {kind}: {len(b)} bytes")
    except urllib.error.HTTPError as e:
        print(f"  {kind}: {e.code}")

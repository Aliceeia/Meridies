"""End-to-end check of the memory layer in the installed Chrome (fake microphone).

    python connect/server.py &      # in another terminal
    python connect/tests/e2e_memory.py [step] [screenshot_dir]

Uses Chrome's fake media device (a beep) so MediaRecorder produces real audio.
Writes real memories into connect/data/memories/.
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://localhost:8765/"
STEP = sys.argv[1] if len(sys.argv) > 1 else "all"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(".")

errors = []


def walk_to(page, x, y):
    """Click the map at map coords (x, y) and wait until the walker arrives."""
    page.evaluate("""([x, y]) => {
        const m = document.getElementById('map'), p = m.createSVGPoint(); p.x = x; p.y = -y;
        const s = p.matrixTransform(m.getScreenCTM());
        m.dispatchEvent(new MouseEvent('click', {clientX: s.x, clientY: s.y, bubbles: true}));
    }""", [x, y])
    page.wait_for_function("() => state.target === null", timeout=60_000)


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=[
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1500, "height": 950})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.goto(URL + ("?building=zvKHR9DHNjJ" if STEP == "6" else "?building=1PVeAYmPNJz"))
    page.wait_for_function("() => state.ready === true")
    page.evaluate("document.getElementById('debug').open = true")   # demo switches (hat, user) live in the debug drawer
    n0 = page.evaluate("state.memories.length")
    print("memories visible at start:", n0)

    if STEP in ("2", "all"):
        walk_to(page, 8.3, 0.5)
        page.click("#rec")
        page.wait_for_selector("body.recording", timeout=5000)
        time.sleep(3)
        page.screenshot(path=str(OUT / "step2_recording.png"))
        # clicks on the map are refused while recording
        pos = page.evaluate("[state.walker.x, state.walker.y]")
        page.wait_for_function(f"() => state.memories.length === {n0 + 1}", timeout=20_000)
        m = page.evaluate("state.memories[state.memories.length-1]")
        print("saved:", {k: m[k] for k in ("position", "building_id", "author_id", "created_at", "visibility")})
        r = page.request.get(URL.rstrip("/") + m["audio"] + "?viewer=user-a")
        print("audio:", r.status, r.headers.get("content-type"), len(r.body()), "bytes")
        assert m["visibility"] == "private" and abs(m["position"]["x"] - pos[0]) < 0.01
        page.screenshot(path=str(OUT / "step2_saved.png"))

    if STEP in ("3", "all"):
        page.click("h2")  # a user gesture -> AudioContext exists
        m = page.evaluate("state.memories[0]")
        mx, my = m["position"]["x"], m["position"]["y"]
        walk_to(page, 17.3, 5.7)                     # Yard A, > 6 m away: re-arm
        assert page.evaluate("document.getElementById('nearby').hidden"), "prompt showed while far away"
        walk_to(page, mx + 3.0, my)                  # 3 m to the right of the memory, arriving heading -X (270)
        near = page.evaluate("play.near")
        print("near after approach:", near == m["id"], "|", page.inner_text("#nearWhere"))
        assert near == m["id"] and not page.evaluate("document.getElementById('nearby').hidden")
        assert page.evaluate("play.playingId") is None, "voice auto-played"
        page.screenshot(path=str(OUT / "step3_nearby.png"))
        page.click("#listen")
        page.wait_for_function("() => play.playingId !== null", timeout=10_000)
        # face -X (heading 270): the memory, 3 m to our -X side, is straight ahead -> panner z < 0, x ~ 0
        page.evaluate("state.walker.heading = 270; renderWalker()")
        time.sleep(0.3)
        ahead = page.evaluate("[play.panner.positionX.value, play.panner.positionZ.value]")
        page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight")  # +90 deg -> heading 0
        time.sleep(0.3)
        left = page.evaluate("[play.panner.positionX.value, play.panner.positionZ.value]")
        print(f"panner facing it: x={ahead[0]:+.2f} z={ahead[1]:+.2f} | after turning right 90: x={left[0]:+.2f} z={left[1]:+.2f}")
        assert ahead[1] < -2.5 and abs(ahead[0]) < 0.5, "should be ahead"
        assert left[0] < -2.5 and abs(left[1]) < 0.5, "should be on the left"
        page.screenshot(path=str(OUT / "step3_playing.png"))
        page.check("#hat")
        assert page.evaluate("play.playingId") is None, "hat warning did not stop memory audio"
        page.click("#listen")
        assert page.evaluate("play.playingId") is None, "played while hat warning"
        page.uncheck("#hat")
        print("hat gate: ok")

    if STEP in ("4", "all"):
        m = page.evaluate("state.memories[0]")
        print("compass:", page.inner_text("#compass"))
        r = page.request.post(f"{URL}api/memories/{m['id']}/describe", data={"viewer": "user-a"}, timeout=180_000)
        body = r.json()
        print("describe:", r.status, body.get("error") or body["description"]["text"])
        for kind in ("aerial", "splat"):
            ir = page.request.get(f"{URL}api/memories/{m['id']}/image/{kind}?viewer=user-a")
            print(f"  {kind} crop:", ir.status, ir.headers.get("content-type"), len(ir.body()), "bytes")
            if ir.ok:
                (OUT / f"step4_{kind}.jpg").write_bytes(ir.body())
        if r.ok:
            page.reload(); page.wait_for_function("() => state.memories.length > 0")
            page.screenshot(path=str(OUT / "step4_described.png"))

    if STEP in ("5", "all"):
        api = URL + "api/memories"
        mine = [m for m in page.evaluate("state.memories") if m["author_id"] == "user-a"]
        m = mine[0]
        # start from private
        page.request.patch(f"{api}/{m['id']}", data={"viewer": "user-a", "visibility": "private"})
        page.select_option("#user", "user-a"); page.wait_for_timeout(300)
        page.select_option("#user", "user-b"); page.wait_for_timeout(500)
        seen_b = page.evaluate("state.memories.map(x => x.id)")
        print("user-b sees user-a's private memory:", m["id"] in seen_b)
        assert m["id"] not in seen_b
        assert page.request.get(f"{api}/{m['id']}/audio?viewer=user-b").status == 403
        assert page.request.patch(f"{api}/{m['id']}", data={"viewer": "user-b", "visibility": "community"}).status == 403
        # author shares it
        page.select_option("#user", "user-a"); page.wait_for_timeout(500)
        page.click(f"#mem-{m['id']} >> text=Share with community")
        page.wait_for_function(f"() => state.memories.find(x => x.id === '{m['id']}').visibility === 'community'")
        page.select_option("#user", "user-b"); page.wait_for_timeout(500)
        print("after sharing, user-b sees it:", m["id"] in page.evaluate("state.memories.map(x => x.id)"))
        page.click("h2")
        walk_to(page, 17.3, 5.7)
        walk_to(page, m["position"]["x"] + 2.0, m["position"]["y"])
        assert page.evaluate("play.near") == m["id"], "user-b did not get the chime/prompt"
        assert page.evaluate("play.playingId") is None
        page.click("#listen")
        page.wait_for_function("() => play.playingId !== null", timeout=10_000)
        print("user-b hears it after confirming:", page.inner_text("#nearWhere"))
        assert page.query_selector(f"#mem-{m['id']} >> text=Make private") is None, "non-author got a visibility toggle"
        page.screenshot(path=str(OUT / "step5_userb.png"))

    if STEP == "6":   # interior: sweep-to-sweep walking + first-person view + memory at a sweep
        page.wait_for_function("() => state.plan.kind === 'interior'")
        print("start sweep:", page.evaluate("state.walker.sweep"), "|", page.inner_text("#zone"))
        page.wait_for_function("() => document.getElementById('viewImg').complete && !document.getElementById('viewImg').classList.contains('loading')", timeout=60_000)
        src0 = page.evaluate("document.getElementById('viewImg').src")
        page.screenshot(path=str(OUT / "step6_start.png"))
        # turn to face -X (heading 270: toward the dining room), then step forward
        page.evaluate("state.walker.heading = 270; renderWalker()")
        page.click("h2")
        page.keyboard.press("ArrowUp")
        page.wait_for_function("() => state.target === null && state.walker.sweep !== 4", timeout=20_000)
        s1 = page.evaluate("state.walker.sweep")
        nb = page.evaluate("sweepAt(4).neighbours")
        print("ArrowUp moved to sweep", s1, "| a neighbour of 4:", s1 in nb, "|", page.inner_text("#zone"))
        assert s1 in nb
        page.wait_for_function(f"() => document.getElementById('viewImg').src !== {src0!r} && !document.getElementById('viewImg').classList.contains('loading')", timeout=60_000)
        print("view updated:", page.inner_text("#viewCap"))
        # a non-neighbour click is refused
        far = page.evaluate("state.plan.sweeps.find(s => s.index !== state.walker.sweep && !sweepAt(state.walker.sweep).neighbours.includes(s.index)).index")
        page.evaluate(f"goToSweep({far})")
        assert page.evaluate("state.target") is None, "jumped to a non-neighbour"
        # record a memory here
        page.click("#rec")
        page.wait_for_selector("body.recording", timeout=5000)
        page.wait_for_function("() => !document.body.classList.contains('recording')", timeout=20_000)
        page.wait_for_selector(".memdot.landed", state="attached", timeout=20_000)   # the new memory's light pulses
        m = page.evaluate("memById(document.querySelector('.memdot.landed').dataset.id)")
        print("memory:", {k: m.get(k) for k in ("building_id", "sweep", "heading_deg", "position", "visibility")})
        try:
            page.wait_for_selector(f".memdot.landed[data-id='{m['id']}']", state="attached", timeout=5000)
        except Exception:
            print("DEBUG want", m["id"][:8], "walker", page.evaluate("[state.walker.x, state.walker.y, state.walker.sweep]"),
                  "dots", page.evaluate("[...document.querySelectorAll('#markers .memdot')].map(d => d.dataset.id.slice(0,8) + ' ' + d.className)"),
                  "newest", page.evaluate("state.memories.map(m => m.created_at + ' ' + m.id.slice(0,8)).sort().slice(-3)"))
            raise
        print("landed pulse: yes | plays back:", page.wait_for_function(f"() => play.playingId === {m['id']!r}", timeout=15_000) is not None)
        page.screenshot(path=str(OUT / "step7_landed.png"))
        page.click("#whereami")
        print("where am I:", page.inner_text("#whereText"))
        print("list:", page.inner_text("#memList").replace("\n", " | ")[:300], "|", page.inner_text("#more"))
        # walk away two steps and come back: chime prompt, panner direction
        page.evaluate("state.walker.heading = 90; renderWalker()")
        for _ in range(3):
            page.keyboard.press("ArrowUp")
            page.wait_for_function("() => state.target === null", timeout=20_000)
        d_away = page.evaluate(f"relative(memById({m['id']!r})).dist")
        print(f"walked to sweep {page.evaluate('state.walker.sweep')}, {d_away:.1f} m from the memory")
        page.wait_for_function("() => !document.getElementById('viewImg').classList.contains('loading')", timeout=60_000)
        print("view at the end:", page.inner_text("#viewCap"), "| src:", page.evaluate("document.getElementById('viewImg').src").split("?")[1])
        page.screenshot(path=str(OUT / "step6_interior.png"))

    b.close()

print("page errors:", errors or "none")
sys.exit(1 if errors else 0)

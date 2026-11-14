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
    page.goto(URL)
    page.wait_for_function("() => state.plan && document.getElementById('memCount')")
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
        page.mouse.click(5, 5)  # a user gesture -> AudioContext exists
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

    b.close()

print("page errors:", errors or "none")
sys.exit(1 if errors else 0)

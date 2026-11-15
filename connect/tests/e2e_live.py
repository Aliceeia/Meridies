"""/live end to end in the installed Chrome (fake microphone + camera), on a scratch memory folder.

    python connect/tests/e2e_live.py

- a 10 s recording is saved on the device first; with the server unreachable it waits in the queue, and the
  page keeps working; when the server answers it uploads, with the camera frame as its cover
- the status board shows the hat's obstacle stream (a real ZeroMQ PUB stands in for integration/)
- a warning stops a recording at once and keeps it as a private draft
- camera context on demand answers in words (Gemini stubbed)
- the page loads with the network off (service worker)
"""
import json
import shutil
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

import zmq
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import connect.server as srv  # noqa: E402
from connect.backend import gemini  # noqa: E402

OUT = ROOT / "connect" / "tests" / "out"
OUT.mkdir(exist_ok=True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="meridies-live-"))
    srv.MEM, srv.LINKS, srv.AUTO_PROCESS = tmp, tmp / "links.json", False
    srv.VOICE_ON, srv.VOICE_AUTOGEN = False, False          # spoken lines captured from the browser voice
    ai_calls = []
    gemini.generate = lambda parts, **k: ai_calls.append(parts) or "There's a doorway and a chair close by."
    pub = zmq.Context.instance().socket(zmq.PUB)
    port = pub.bind_to_random_port("tcp://127.0.0.1")
    srv.HAT_ENDPOINT = f"tcp://127.0.0.1:{port}"
    srv.start_hat_relay()
    warnings = {"now": []}
    stop = threading.Event()

    def hat():                                          # 10 Hz, like integration/ would
        while not stop.is_set():
            pub.send_string(json.dumps(warnings["now"]))
            time.sleep(0.1)
    threading.Thread(target=hat, daemon=True).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://localhost:{httpd.server_address[1]}"     # localhost: a secure context, so the service worker runs
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=[
            "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1100, "height": 900}, permissions=["microphone", "camera"])
        ctx.add_init_script("window.__said = []; speechSynthesis.speak = (u) => window.__said.push(u.text);")
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/live")
        page.wait_for_function("document.getElementById('spot').options.length > 0")
        page.select_option("#building", "zvKHR9DHNjJ")
        page.wait_for_timeout(500)
        print("spots:", page.locator("#spot option").all_inner_texts()[:6], "…")

        # ---- the hat stream drives the status board
        page.wait_for_function("document.getElementById('chipHat').textContent === 'Hat connected'", timeout=5000)
        warnings["now"] = [{"id": "S3", "az": -45, "dist": 0.62, "level": "warn"}]
        page.wait_for_function("document.getElementById('state').textContent === 'Careful'", timeout=5000)
        print("board:", page.inner_text("#state"), "|", page.inner_text("#stateLine"))
        assert "ahead-left" in page.inner_text("#stateLine")
        page.wait_for_timeout(300)
        spoken = page.evaluate("window.__said.slice(-1)[0] || ''")
        print("warning spoken:", spoken)
        assert spoken in ("Careful, ahead left.", "Something ahead left.") and len(spoken.split()) <= 4   # one clause, < 2 s
        n_said = page.evaluate("window.__said.length")
        page.wait_for_timeout(600)
        assert page.evaluate("window.__said.length") == n_said, "a warning must be said once, not every frame"
        page.screenshot(path=str(OUT / "live_warning.png"))
        warnings["now"] = []
        page.wait_for_function("document.getElementById('state').textContent === 'Clear'", timeout=5000)

        # ---- record 10 s with the server unreachable: saved on the device, walking unaffected
        page.check("#camOn")
        page.wait_for_function("document.getElementById('cam').videoWidth > 0", timeout=10000)
        page.route("**/api/memories**", lambda r: r.abort())
        page.keyboard.press("r")
        page.wait_for_function("document.getElementById('rec').classList.contains('recording')", timeout=5000)
        page.wait_for_function("!document.getElementById('rec').classList.contains('recording')", timeout=15000)
        page.wait_for_function("document.getElementById('chipQueue').textContent.startsWith('1 waiting')", timeout=5000)
        print("offline record:", page.inner_text("#recLine"), "|", page.inner_text("#chipQueue"), "|", page.inner_text("#queue"))
        assert "with photo" in page.inner_text("#queue")
        assert not list(tmp.glob("*.json"))
        page.screenshot(path=str(OUT / "live_queued.png"))

        # ---- connection back: it uploads by itself (here: nudge the retry instead of waiting 15 s)
        page.unroute("**/api/memories**")
        page.evaluate("live.flush()")
        page.wait_for_function("document.getElementById('chipQueue').textContent === 'Nothing waiting to upload'", timeout=10000)
        mems = [json.loads(f.read_text()) for f in tmp.glob("*.json") if srv.ID_RE.match(f.stem)]
        assert len(mems) == 1
        m = mems[0]
        cover = tmp / f"{m['id']}_camera.jpg"
        print(f"uploaded: visibility={m['visibility']} sweep={m.get('sweep')} created_at={m['created_at']} "
              f"camera cover={cover.exists()} audio={(tmp / m['audio_file']).stat().st_size} bytes")
        assert m["visibility"] == "private" and cover.exists() and m["sweep"] is not None

        # ---- a warning mid-recording stops it at once, as a private draft
        page.keyboard.press("r")
        page.wait_for_function("document.getElementById('rec').classList.contains('recording')", timeout=5000)
        page.wait_for_timeout(2500)
        warnings["now"] = [{"id": "S1", "az": 5, "dist": 0.4, "level": "urgent"}]
        page.wait_for_function("!document.getElementById('rec').classList.contains('recording')", timeout=3000)
        warnings["now"] = []
        # wait for the draft itself (the queue label already read "nothing waiting" before the take was saved)
        drafts, t_end = [], time.time() + 15
        while not drafts and time.time() < t_end:
            page.wait_for_timeout(300)
            drafts = [d for d in (json.loads(f.read_text()) for f in tmp.glob("*.json") if srv.ID_RE.match(f.stem)) if d.get("draft")]
        print("warning mid-recording:", page.inner_text("#recLine"), "| drafts:", len(drafts))
        assert len(drafts) == 1 and drafts[0]["visibility"] == "private"

        # ---- C "what's around me": instant, no AI. M "tell me more": exactly one AI call
        page.wait_for_function("document.getElementById('state').textContent === 'Clear'", timeout=5000)
        page.keyboard.press("c")
        page.wait_for_function("(t => t && t !== '…')(document.getElementById('ctxLine').textContent)", timeout=5000)
        print("C (no AI):", page.inner_text("#ctxLine")[:110], "| AI calls:", len(ai_calls))
        assert ai_calls == []
        page.keyboard.press("m")
        page.wait_for_function("document.getElementById('ctxLine').textContent.includes('doorway')", timeout=20000)
        print("M (one AI call):", page.inner_text("#ctxLine")[:110], "| AI calls:", len(ai_calls))
        assert len(ai_calls) == 1

        # ---- the page itself loads with no network
        page.wait_for_function("navigator.serviceWorker.controller !== null || true")
        page.reload()
        page.wait_for_timeout(800)
        ctx.set_offline(True)
        page.reload()
        page.wait_for_selector("#rec", timeout=5000)
        page.wait_for_function("document.getElementById('spot').options.length > 0", timeout=5000)
        print("offline reload: page up,", page.locator("#spot option").count(), "spots from the device cache |", page.inner_text("#chipNet"))
        page.screenshot(path=str(OUT / "live_offline.png"))
        ctx.set_offline(False)

        assert not errors, errors
        b.close()
    stop.set()
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("live e2e: ok")


if __name__ == "__main__":
    main()

"""After-recording flow in the real page (fake microphone).   python connect/tests/e2e_after_record.py [out_dir]

1. record -> stop with R -> automatic playback -> Enter skips -> question (private home: 2 options) -> Decide later = Draft
2. interrupt during playback  -> no question, private draft
3. interrupt while recording  -> mic stops, private draft, no playback
"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
errors = []


def newest(page):
    return page.evaluate("state.memories.slice().sort((a, b) => a.created_at < b.created_at ? -1 : 1).pop()")


def record(page, secs=2.5):
    page.click("#rec")
    page.wait_for_selector("body.recording", timeout=8000)
    time.sleep(secs)


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=[
        "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1500, "height": 950})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("window.__spoken = []; const _s = speechSynthesis.speak.bind(speechSynthesis);"
                         "speechSynthesis.speak = (u) => { window.__spoken.push(u.text); return _s(u); };")
    page.goto("http://localhost:8765/?building=zvKHR9DHNjJ")
    page.wait_for_function("() => state.ready === true")

    # --- 1. normal flow
    record(page)
    print("Stop button where Record was:", page.is_visible("#recStop"), "| Record hidden:", not page.is_visible("#rec"),
          "| counter:", page.inner_text("#recState"))
    page.keyboard.press("r")
    page.wait_for_function("() => afterRecordState.phase === 'reviewing'", timeout=15000)
    print("reviewing:", page.inner_text("#recState"))
    page.keyboard.press("Enter")
    page.wait_for_function("() => afterRecordState.phase === 'deciding'", timeout=5000)
    opts = page.evaluate("[...document.querySelectorAll('#decide .opt:not([hidden])')].map(b => b.dataset.choice)")
    print("question:", page.inner_text("#decideTitle"), "| options:", opts,
          "| focus on:", page.evaluate("document.activeElement.dataset.choice"),
          "| aria:", page.evaluate("document.activeElement.getAttribute('aria-label')"))
    assert opts == ["private", "later"], "a private home must not offer community sharing"
    page.screenshot(path=str(OUT / "ar_question.png"))
    page.click("#decide .opt[data-choice='later']")
    page.wait_for_function("() => afterRecordState.phase === 'saved'")
    time.sleep(1)
    m = newest(page)
    print("Decide later ->", {k: m.get(k) for k in ("visibility", "draft")}, "|", page.inner_text(f"#mem-{m['id']} .who"))
    assert m["draft"] is True and m["visibility"] == "private"

    # --- 2. interrupt during playback
    record(page)
    page.keyboard.press("Enter")
    page.wait_for_function("() => afterRecordState.phase === 'reviewing'", timeout=15000)
    page.evaluate("meridies.interrupt('test-obstacle')")
    time.sleep(1.5)
    m = newest(page)
    print("interrupt in playback ->", page.evaluate("afterRecordState.result"), {k: m.get(k) for k in ("visibility", "draft")},
          "| question shown:", page.is_visible("#decide"))
    assert m["draft"] is True and not page.is_visible("#decide")

    # --- 3. interrupt while recording
    record(page)
    page.evaluate("meridies.interrupt('test-obstacle')")
    page.wait_for_function("() => afterRecordState.phase === 'saved' && !rec.mr", timeout=10000)
    time.sleep(2)
    m = newest(page)
    print("interrupt in recording ->", page.evaluate("afterRecordState.result"), {k: m.get(k) for k in ("visibility", "draft")},
          "| playing:", page.evaluate("play.playingId"), "|", page.inner_text("#recState"))
    assert m["draft"] is True and page.evaluate("play.playingId") is None

    print("spoken:", page.evaluate("window.__spoken"))
    b.close()
print("page errors:", errors or "none")

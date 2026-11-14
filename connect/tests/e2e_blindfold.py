"""Blindfold + audio-first check (interior).  python connect/tests/e2e_blindfold.py [out_dir]"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
errors = []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1500, "height": 950})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("""window.__spoken = []; window.__tones = 0;
      const S = window.speechSynthesis; const orig = S.speak.bind(S);
      S.speak = (u) => { window.__spoken.push(u.text); return orig(u); };""")
    page.goto("http://localhost:8765/walk?building=zvKHR9DHNjJ")
    page.wait_for_function("() => state.ready === true")
    page.evaluate("() => { const t = audio.tone.bind(audio); audio.tone = (...a) => { window.__tones++; return t(...a); }; }")
    vis = page.evaluate("[...document.querySelectorAll('#memList .mem')].filter(c => getComputedStyle(c).display !== 'none').length")
    print("cards expanded:", vis, "| more:", page.inner_text("#more"))
    page.click("h2")
    page.keyboard.press("Space")
    page.wait_for_selector("#blind", state="visible")
    print("blind overlay text:", page.inner_text("#blind"))
    page.evaluate("state.walker.heading = 270; renderWalker()")
    for k in ["ArrowUp", "ArrowUp", "ArrowLeft", "ArrowUp"]:
        page.keyboard.press(k)
        page.wait_for_function("() => state.target === null", timeout=20_000)
        time.sleep(0.3)
    page.keyboard.press("Enter")
    time.sleep(1.5)
    page.keyboard.press("w")
    time.sleep(0.5)
    page.keyboard.press("Space")
    page.wait_for_selector("#report", state="visible")
    time.sleep(1)
    print("report:", page.inner_text("#reportLine"))
    print("path points:", page.evaluate("blind.path.length"), "| tones played:", page.evaluate("window.__tones"))
    print("spoken:", [s[:70] for s in page.evaluate("window.__spoken")])
    print("voice:", page.evaluate("voice && voice.name"))
    page.screenshot(path=str(OUT / "blind_report.png"))
    b.close()
print("page errors:", errors or "none")

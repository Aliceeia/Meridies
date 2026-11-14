"""Listen button check (interior): stand near a memory, press Listen, confirm audio starts.
    python connect/tests/e2e_listen.py"""
import time
from playwright.sync_api import sync_playwright

errors, logs = [], []
with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
    page = b.new_page(viewport={"width": 1500, "height": 950})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
    page.goto("http://localhost:8765/?building=zvKHR9DHNjJ")
    page.wait_for_function("() => state.ready === true")
    page.click("h2")
    # walk next to the nearest memory: the start sweep is 4; the test memories sit at sweep 0 (a neighbour)
    page.evaluate("goToSweep(0)")
    page.wait_for_function("() => state.target === null && state.walker.sweep === 0", timeout=20_000)
    time.sleep(0.5)
    print("near:", page.evaluate("play.near"), "| prompt visible:", page.is_visible("#nearby"), "|", page.inner_text("#nearby")[:80].replace("\n", " "))
    print("listen visible:", page.is_visible("#listen"))
    if page.is_visible("#listen"):
        page.click("#listen")
        time.sleep(3)
        print("playingId after click:", page.evaluate("play.playingId"), "| ctx state:", page.evaluate("audio._ctx && audio._ctx.state"))
    print("card buttons:", page.evaluate("[...document.querySelectorAll('#memList .mem:not(.far-hidden) button')].map(b => b.textContent)"))
    b.close()
print("errors:", errors or "none")
print("console:", [l for l in logs if not l.startswith('warning: THREE')][:10])

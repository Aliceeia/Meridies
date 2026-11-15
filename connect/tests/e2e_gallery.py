"""MeriVerse gallery end to end in the installed Chrome, on a scratch copy of the sample memories
(your data is not touched).

    python connect/tests/e2e_gallery.py          # timeline, detail, sharing, circle, family link, take me there
    python connect/tests/e2e_gallery.py listen   # + listen mode and a hat button
"""
import json
import shutil
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import connect.server as srv  # noqa: E402

OUT = ROOT / "connect" / "tests" / "out"
OUT.mkdir(exist_ok=True)
REAL = srv.MEM
LISTEN = "listen" in sys.argv


def seed(tmp: Path):
    """4 sample memories in 3 places: 2 in the La Mirada dining room (one is the chandelier note), the Princeton
    foyer, the Princeton classroom. All given to user-a here."""
    from connect.tools.seed_samples import sample_id
    picked = [json.loads((REAL / f"{sample_id(k)}.json").read_text()) for k in ("l-movies", "l-chandelier", "p-alone", "f-maya-talk")]
    assert all(m.get("description") for m in picked), "run connect/tools/seed_samples.py first"
    for m, v in zip(picked, ["family", "private", "family", "family"]):
        for f in REAL.glob(f"{m['id']}*"):
            shutil.copy(f, tmp / f.name)
        m = json.loads((tmp / f"{m['id']}.json").read_text())
        m.update(visibility=v, draft=False, share_request=None, author_id="user-a")
        (tmp / f"{m['id']}.json").write_text(json.dumps(m))


def no_sideways_scroll(page, what):
    sw, cw = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw, f"{what}: page scrolls sideways ({sw} > {cw})"


def main():
    tmp = Path(tempfile.mkdtemp(prefix="meridies-e2e-"))
    srv.MEM, srv.LINKS, srv.CIRCLES, srv.SETTINGS, srv.AUTO_PROCESS = tmp, tmp / "l.json", tmp / "c.json", tmp / "s.json", False
    srv.desc.describe = lambda m, d: dict(text="A test description of the spot.", model="test", images=[])   # no Gemini quota
    from connect.backend import gemini
    global AI_CALLS
    AI_CALLS = []
    gemini.generate = lambda parts, **k: AI_CALLS.append(parts) or "Three sentences from the model."
    seed(tmp)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        ctx = b.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- timeline: grouped by day, filterable by place
        page.goto(base + "/")
        page.wait_for_selector(".entry")
        print("title:", page.inner_text("#pageTitle"), "|", page.inner_text("#lede"))
        assert page.locator(".entry").count() == 4 and page.locator(".chip").count() == 4   # All + 3 places
        assert page.locator(".sample-tag").count() == 0                                   # no badge clutter
        page.locator(".chip", has_text="Dining Room").click()
        page.wait_for_function("document.querySelectorAll('.entry').length === 2")
        page.locator(".chip.all").click()
        page.wait_for_function("document.querySelectorAll('.entry').length === 4")
        no_sideways_scroll(page, "desktop")

        # ---- play from the timeline: one at a time, progress appears only while playing
        first = page.locator(".entry").first
        assert not first.locator(".progress").is_visible()
        first.locator(".play").click()
        page.wait_for_function("gallery.G.playingId !== null")
        page.wait_for_timeout(700)
        assert first.locator(".progress").is_visible() and "Pause" in first.locator(".play").get_attribute("aria-label")
        print("playing:", page.inner_text("#announcer"), "|", first.locator(".time").inner_text())
        first.locator(".play").click()
        assert page.evaluate("gallery.G.playingId") is None

        # ---- keyboard only: Tab reaches a play button, Enter plays it
        page.evaluate("document.querySelector('.entry .thumb-btn').focus()")
        page.keyboard.press("Tab"); page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.classList.contains('play')"), page.evaluate("document.activeElement.outerHTML")[:120]
        page.keyboard.press("Enter")
        page.wait_for_function("gallery.G.playingId !== null")
        page.keyboard.press("Enter")

        # ---- the memory, revisited: dialog, focus in, sharing, focus back out
        opener = page.locator(".entry .excerpt").nth(1)
        opener.click()
        page.wait_for_selector("#detail[open]")
        assert page.evaluate("document.activeElement.id") == "dTitle"
        print("detail:", page.inner_text("#dTitle"), "|", page.inner_text("#dWhere"))
        assert page.inner_text("#dSaid") and page.inner_text("#dAbout")
        before = page.evaluate("document.querySelector('#detail input[name=vis]:checked').value")
        other = "family" if before == "private" else "private"
        page.locator(f"#detail input[value={other}]").check()
        page.wait_for_function(f"document.getElementById('dShareNote').textContent.includes('{'shared with family' if other == 'family' else 'only you'}')")
        print("sharing:", page.inner_text("#dShareNote"))
        page.locator(f"#detail input[value={before}]").check()
        page.wait_for_timeout(400)
        page.screenshot(path=str(OUT / "gallery_detail.png"))
        page.keyboard.press("Escape")
        page.wait_for_selector("#detail:not([open])", state="attached")
        page.wait_for_timeout(300)
        assert page.evaluate("document.activeElement.classList.contains('excerpt')"), \
            f"focus did not return to the memory: {page.evaluate('document.activeElement.outerHTML.slice(0, 120)')}"

        # ---- MeriCircle: put User B in the circle -> "Shared with me" for User B shows only family
        page.click("#profileBtn")
        page.click("#circleOpen")
        page.wait_for_selector("#circle[open]")
        page.check("#mem-user-b")
        page.wait_for_function("document.getElementById('announcer').textContent.includes('in your circle')")
        page.click("#famMake")
        page.wait_for_function("document.getElementById('famUrl').value.includes('/f/')")
        url = page.input_value("#famUrl")
        page.screenshot(path=str(OUT / "gallery_circle.png"))
        page.keyboard.press("Escape")
        b_page = ctx.new_page()
        b_page.add_init_script("localStorage.setItem('meridies.user', 'user-b')")
        b_page.goto(base + "/")
        b_page.wait_for_selector(".empty")                                  # User B has no memories of their own
        b_page.locator(".tab", has_text="Shared with me").click()
        b_page.wait_for_selector(".entry")
        n = b_page.locator(".entry").count()
        print("User B, shared with me:", n, "|", b_page.inner_text("#lede"))
        assert n == 3 and b_page.locator(".entry", has_text="from User A").count() == 3
        b_page.locator(".entry .excerpt").first.click()
        assert b_page.locator("#dShare").is_hidden() and "User A" in b_page.inner_text("#dFrom")
        b_page.close()

        # ---- family link: read-only, family memories only
        fam = ctx.new_page()
        fam.goto(url)
        fam.wait_for_selector(".entry")
        assert fam.locator(".entry").count() == 3
        assert fam.locator("#tabs").is_hidden() and fam.locator("#profile").is_hidden()
        assert fam.locator(".entry a", has_text="Take me there").count() == 0
        fam.locator(".entry .excerpt").first.click()
        assert fam.locator("#dShare").is_hidden() and fam.locator("#dActions a").count() == 0
        fam.screenshot(path=str(OUT / "gallery_family.png"))
        fam.keyboard.press("Escape")
        page.click("#profileBtn"); page.click("#circleOpen"); page.click("#famOff")
        page.wait_for_function("document.getElementById('famUrl').value === ''")
        page.keyboard.press("Escape")
        fam.goto(url)
        fam.wait_for_selector(".empty")
        print("family link after turning it off:", fam.inner_text(".empty").splitlines()[0])
        fam.close()

        # ---- phone width
        phone = ctx.new_page()
        phone.set_viewport_size({"width": 390, "height": 844})
        phone.add_init_script("localStorage.setItem('meridies.user', 'user-a')")   # the context still remembers User B
        phone.goto(base + "/")
        phone.wait_for_selector(".entry")
        no_sideways_scroll(phone, "phone")
        phone.screenshot(path=str(OUT / "gallery_phone.png"))
        phone.close()

        # ---- take me there: the 3D space, a few steps away, facing it; first key chimes it
        page.evaluate("localStorage.setItem('meridies.user', 'user-a')")
        page.goto(base + "/")
        page.wait_for_selector(".entry")
        mid = page.locator(".entry").first.get_attribute("id")[2:]
        page.locator(".entry").first.locator("a", has_text="Take me there").click()
        page.wait_for_url("**/walk?**")
        page.wait_for_selector("#arrive", timeout=30000)
        page.keyboard.press("Shift")
        page.wait_for_timeout(400)
        near, dist, fwd = page.evaluate(f"[play.near, relative(memById('{mid}')).dist, relative(memById('{mid}')).fwd]")
        print(f"take me there: near={near == mid} dist={dist:.2f} m ahead={fwd:.2f} m")
        assert near == mid and dist <= 4 and fwd > 0

        # ---- the hazard and the note left at it line up (geometry, no AI): W next to the chandelier
        from connect.tools.seed_samples import sample_id
        page.goto(f"{base}/walk?building=zvKHR9DHNjJ&memory={sample_id('l-chandelier')}&viewer=user-a")
        page.wait_for_selector("#arrive", timeout=30000)
        page.keyboard.press("Shift"); page.wait_for_timeout(300)
        before = len(AI_CALLS)
        page.keyboard.press("w")
        page.wait_for_function("document.getElementById('whereText').textContent.toLowerCase().includes('chandelier')", timeout=5000)
        said = page.inner_text("#whereText")
        print("W at the chandelier:", said[:200])
        low = said.lower()
        assert "chandelier" in low and "one metre six" in low and "careful, the chandelier here hangs low" in low
        assert len(AI_CALLS) == before
        page.keyboard.press("m")
        page.wait_for_function("document.getElementById('tellText').textContent.includes('Three sentences')", timeout=20000)
        assert len(AI_CALLS) == before + 1 and "Careful, the chandelier" in AI_CALLS[-1][0]["text"]
        print("M (one live call):", page.inner_text("#tellText")[:160])
        page.evaluate("for (let i = 0; i < 4; i++) { state.walker.heading = (state.walker.heading + 15) % 360; renderWalker(); }")
        page.wait_for_timeout(300)
        assert len(AI_CALLS) == before + 1, "movement must never call the AI"

        if LISTEN:
            listen(page, base)
        assert not errors, errors
        b.close()
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("gallery e2e: ok")


def listen(page, base):
    page.add_init_script("""window.__said = []; window.__cancels = 0;
      speechSynthesis.speak = (u) => { window.__said.push(u.text); };
      const c = speechSynthesis.cancel.bind(speechSynthesis);
      speechSynthesis.cancel = () => { window.__cancels++; c(); };""")
    page.goto(base + "/")
    page.wait_for_selector(".entry")
    said = lambda: page.evaluate("window.__said.slice(-1)[0] || ''")  # noqa: E731
    page.keyboard.press("l"); page.wait_for_timeout(300)
    assert page.locator("#listen").is_visible() and page.get_attribute("#listenBtn", "aria-pressed") == "true"
    print("  L    ->", said()[:100])
    assert "1 of 3" in said()
    first4 = " ".join(said().split()[:4])
    assert any(p in first4 for p in page.evaluate("gallery.G.places.map(p => p.name.split(' ')[0])")), "place name not front-loaded"
    seen = {}
    for _ in range(3):
        n = said().split(". ")[1] if said().startswith("Place") else ""
        seen[n] = page.evaluate("gallery.G.places[gallery.G.placeIdx].memory_count")
        page.keyboard.press("ArrowRight"); page.wait_for_timeout(200)
    print("  → ×3 ->", said()[:60])
    assert "last" in said().lower() and len(said().split()) <= 4         # an edge: a few words, not the line again
    while page.evaluate("gallery.G.places[gallery.G.placeIdx].memory_count") < 2:
        page.keyboard.press("ArrowLeft"); page.wait_for_timeout(150)
    page.keyboard.press("ArrowDown"); page.wait_for_timeout(200)
    print("  ↓    ->", said()[:90])
    assert "“" in " ".join(said().split()[:4]) or "“" in said()[:40], "what was said should come first"
    # INTERRUPTIBLE: any key cuts speech off at once
    page.keyboard.press("h"); page.wait_for_timeout(100)
    n = page.evaluate("window.__cancels")
    page.keyboard.press("Shift"); page.wait_for_timeout(50)
    assert page.evaluate("window.__cancels") > n, "a key did not cancel speech"
    print("  key during help -> speech cancelled")
    page.keyboard.press("Space"); page.wait_for_timeout(900)
    assert page.evaluate("!!gallery.G.audio")
    n0 = len(AI_CALLS)
    page.keyboard.press("d"); page.wait_for_timeout(200)
    print("  D    ->", said()[:80])
    assert len(AI_CALLS) == n0, "D must use the cached description"
    page.keyboard.press("m")
    page.wait_for_function("(window.__said.slice(-1)[0] || '').includes('Three sentences')", timeout=20000)
    print("  M    ->", said()[:100])
    assert len(AI_CALLS) == n0 + 1
    req = urllib.request.Request(base + "/api/hat/button", data=json.dumps({"button": "right"}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req)
    page.wait_for_timeout(500)
    print("  hat → ->", said()[:60])
    assert "of 3" in said() or "last" in said().lower()
    page.keyboard.press("x"); page.wait_for_timeout(150)
    assert said() == "H for help."
    for s in page.evaluate("window.__said"):
        for bad in ["sorry", "loading", "looking closer", "this takes", "i can see", "it appears"]:
            assert bad not in s.lower(), (bad, s)
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    assert page.locator("#listen").is_hidden() and page.get_attribute("#listenBtn", "aria-pressed") == "false"
    page.keyboard.press("l"); page.wait_for_timeout(200)
    page.keyboard.press("t")
    page.wait_for_url("**/walk?**", timeout=10000)
    print("  T    -> 3D space opened")


if __name__ == "__main__":
    main()

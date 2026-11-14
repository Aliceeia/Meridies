"""Gallery end to end in the installed Chrome, on a scratch copy of real memories (your data is not touched).

    python connect/tests/e2e_gallery.py            # home, place page, family link, take me there
    python connect/tests/e2e_gallery.py listen     # + listen mode (step 3)

Starts its own server on a free port with MEM pointed at a temp folder.
"""
import json
import shutil
import sys
import tempfile
import threading
import time
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
    """Copy 4 real memories (2 places in the house, 1 in the yard); give them visibilities and transcripts."""
    mems = sorted((json.loads(f.read_text()) for f in REAL.glob("*.json") if srv.ID_RE.match(f.stem)), key=lambda m: m["created_at"])
    house = [m for m in mems if m["building_id"] == "zvKHR9DHNjJ"]
    yard = [m for m in mems if m["building_id"] == "1PVeAYmPNJz"]
    by_place = {}
    plan = srv.desc.load_plan("zvKHR9DHNjJ")
    from connect.backend import places
    for m in house:
        by_place.setdefault(places.place_of(m, plan)[0], []).append(m)
    big = max(by_place.values(), key=len)
    other = next(v for k, v in by_place.items() if v is not big)
    picked = [big[-1], big[-2], other[-1], yard[-1]]
    vis = ["family", "private", "family", "family"]
    words = ["This is where Grandpa read the paper every morning.", "Just for me.", "The kitchen smelled of oranges.", "The swing hung from this tree."]
    for m, v, w in zip(picked, vis, words):
        for f in REAL.glob(f"{m['id']}*"):
            shutil.copy(f, tmp / f.name)
        m = json.loads((tmp / f"{m['id']}.json").read_text())
        m.update(visibility=v, draft=False, transcript={"status": "done", "text": w}, author_id="user-a")
        (tmp / f"{m['id']}.json").write_text(json.dumps(m))
    return picked


def main():
    tmp = Path(tempfile.mkdtemp(prefix="meridies-e2e-"))
    srv.MEM, srv.LINKS, srv.AUTO_PROCESS = tmp, tmp / "links.json", False
    srv.desc.describe = lambda m, d: dict(text="A test description of the spot.", model="test", images=[])   # no Gemini quota
    picked = seed(tmp)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = b.new_page(viewport={"width": 1280, "height": 860})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---- home: a grid of places
        page.goto(base + "/")
        page.wait_for_selector(".place")
        n = page.locator(".place").count()
        print("home places:", n, "|", page.inner_text("#homeTitle"))
        assert n == 3, n
        assert "2 memories" in page.inner_text(".place >> nth=0") or "2 memories" in page.inner_text("#places")
        page.wait_for_function("[...document.querySelectorAll('.place img')].every(i => i.complete && i.naturalWidth > 0)", timeout=60000)
        page.screenshot(path=str(OUT / "gallery_home.png"))

        # ---- place page: timeline of memory cards
        page.locator(".place", has_text="2 memories").click()
        page.wait_for_selector(".mem")
        cards = page.locator(".mem")
        assert cards.count() == 2
        first = cards.nth(0)
        assert first.locator(".play").count() == 1 and first.locator(".badge").count() == 1
        assert not first.locator(".text").is_visible()
        first.locator(".showtext").click()
        assert first.locator(".text").is_visible()
        badges = page.locator(".mem .badge").all_inner_texts()
        print("place cards:", cards.count(), "| badges:", badges, "| line:", first.locator(".line").inner_text())
        assert any("Private" in x for x in badges) and any("Family" in x for x in badges)
        page.wait_for_function("[...document.querySelectorAll('.mem img')].every(i => i.complete && i.naturalWidth > 0)", timeout=60000)
        page.screenshot(path=str(OUT / "gallery_place.png"), full_page=True)

        # visibility change by the author, through the page
        sel = page.locator(".mem select.vis").nth(1)
        before = sel.input_value()
        sel.select_option("family" if before == "private" else "private")
        page.wait_for_timeout(600)
        page.locator(".mem select.vis").nth(1).select_option(before)   # put it back
        page.wait_for_timeout(600)

        # ---- family link: only family memories, no controls
        page.click("#familyBtn")
        page.click("#famMake")
        page.wait_for_function("document.getElementById('famUrl').value.includes('/f/')")
        url = page.input_value("#famUrl")
        print("family link made in the page:", url.split("/f/")[0] + "/f/…", "|", page.inner_text("#famNote")[-40:])
        page.screenshot(path=str(OUT / "gallery_family_dialog.png"))
        page.click("#famClose")
        fam = b.new_page(viewport={"width": 1280, "height": 860})
        fam.goto(url)
        fam.wait_for_selector(".place")
        total = sum(int(t.split()[0].replace("One", "1")) for t in fam.locator(".place .meta").all_inner_texts())
        print("family places:", fam.locator(".place").count(), "memories:", total)
        assert total == 3, "family must see exactly the 3 family memories"
        fam.locator(".place").first.click()
        fam.wait_for_selector(".mem")
        assert fam.locator("select.vis").count() == 0 and fam.locator(".mem a").count() == 0
        assert fam.locator("#authorControls").is_hidden()
        assert all("Family" in x for x in fam.locator(".badge").all_inner_texts())
        fam.screenshot(path=str(OUT / "gallery_family.png"))
        # family listen mode works, minus Take me there
        fam.keyboard.press("l"); fam.wait_for_timeout(200)
        assert fam.locator("#listen").is_visible()
        fam.keyboard.press("Escape")
        # the author turns the link off: the same URL now shows nothing
        page.click("#familyBtn"); page.click("#famOff")
        page.wait_for_function("document.getElementById('famUrl').value === ''")
        page.click("#famClose")
        fam.goto(url)
        fam.wait_for_selector(".empty")
        print("after turning it off:", fam.inner_text(".empty"))
        fam.close()

        # ---- take me there: the 3D walk, a few steps away, facing it; first key chimes it
        page.goto(base + "/")
        page.wait_for_selector(".place")
        page.locator(".place", has_text="2 memories").click()
        page.wait_for_selector(".mem")
        mid = page.locator(".mem").first.get_attribute("id")[2:]
        page.locator(".mem").first.locator("a", has_text="Take me there").click()
        page.wait_for_url("**/walk?**")
        page.wait_for_selector("#arrive", timeout=30000)
        page.keyboard.press("Shift")
        page.wait_for_timeout(400)
        near, dist, fwd = page.evaluate(f"[play.near, relative(memById('{mid}')).dist, relative(memById('{mid}')).fwd]")
        print(f"take me there: near={near == mid} dist={dist:.2f} m ahead={fwd:.2f} m")
        assert near == mid and dist <= 4 and fwd > 0
        assert page.locator("#arrive").count() == 0

        if LISTEN:
            listen(page, base)

        assert not errors, errors
        b.close()
    httpd.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    print("gallery e2e: ok")


def listen(page, base):
    """Listen mode: every key answers in speech; the screen can be dark."""
    page.add_init_script("""
      window.__said = [];
      const _speak = speechSynthesis.speak.bind(speechSynthesis);
      speechSynthesis.speak = (u) => { window.__said.push(u.text); };
    """)
    page.goto(base + "/")
    page.wait_for_selector(".place")
    page.keyboard.press("l")
    page.wait_for_selector("#listen:not([hidden])")
    said = lambda: page.evaluate("window.__said.slice(-1)[0] || ''")  # noqa: E731
    page.wait_for_timeout(300)
    print("  L    ->", said())
    assert "Place 1 of 3" in said()
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(200)
    print("  →    ->", said())
    assert said().startswith("Place 2 of 3")
    page.keyboard.press("ArrowDown"); page.wait_for_timeout(200)       # place 2 holds two memories
    print("  ↓    ->", said())
    assert "Memory 2 of 2" in said()
    page.keyboard.press("Space"); page.wait_for_timeout(1200)
    playing = page.evaluate("!!gallery.G.audio")
    print("  Space-> playing:", playing)
    assert playing
    page.keyboard.press("d"); page.wait_for_timeout(200)
    print("  D    ->", said()[:90])
    assert said()
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(200)
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(200)
    page.keyboard.press("ArrowRight"); page.wait_for_timeout(200)
    print("  → at end ->", said())
    assert "last place" in said().lower()
    # a hat button does what the key does
    req = urllib.request.Request(base + "/api/hat/button", data=json.dumps({"button": "left"}).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    print("  hat 'left' delivered to", json.loads(urllib.request.urlopen(req).read())["delivered_to"], "page(s)")
    page.wait_for_timeout(500)
    print("  hat ← ->", said())
    assert said().startswith("Place 2 of 3")
    page.keyboard.press("x"); page.wait_for_timeout(150)
    assert said() == "Press H for help."
    page.keyboard.press("Escape"); page.wait_for_timeout(200)
    assert page.locator("#listen").is_hidden()
    page.keyboard.press("l"); page.wait_for_timeout(200)
    page.keyboard.press("t")
    page.wait_for_url("**/walk?**", timeout=10000)
    print("  T    -> walk opened")


if __name__ == "__main__":
    main()

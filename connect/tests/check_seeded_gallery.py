"""Check the REAL gallery on the running server (http://localhost:8765): every memory complete. Read-only.

    python connect/tests/check_seeded_gallery.py [out_dir]
"""
import sys
from collections import Counter
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)


def settle(page):
    for _ in range(12):
        page.mouse.wheel(0, 500); page.wait_for_timeout(100)        # lazy images below the fold
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_function("[...document.querySelectorAll('.entry img')].every(i => i.complete && i.naturalWidth > 0)", timeout=60000)
    page.wait_for_timeout(900)                                        # the fade-in


with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    page = b.new_page(viewport={"width": 1366, "height": 900})
    page.add_init_script("localStorage.setItem('meridies.user', 'user-a')")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector(".entry")
    settle(page)
    print("page:", page.inner_text("#pageTitle"), "|", page.inner_text("#lede"))
    print("places:", [c.replace("\n", " ") for c in page.locator(".chip").all_inner_texts()])
    page.screenshot(path=str(OUT / "seeded_home.png"))
    page.screenshot(path=str(OUT / "seeded_full.png"), full_page=True)

    report = page.evaluate("""async () => {
      const g = await (await fetch('/api/gallery?' + gallery.auth())).json();
      const out = [];
      for (const p of g.places) for (const m of p.memories) {
        const img = await fetch(gallery.withAuth(m.cover)), au = await fetch(gallery.withAuth(m.audio));
        let secs = 0;
        try { secs = (await new OfflineAudioContext(1, 1, 48000).decodeAudioData(await au.arrayBuffer())).duration; } catch (e) {}
        out.push({ place: p.name, vis: gallery.visOf(m).label, cover: img.ok ? (await img.blob()).size : 0, audio_s: +secs.toFixed(1),
                   transcript: (m.transcript || {}).text || '', desc: (m.description || {}).text || '' });
      }
      return { places: g.places.length, cards: out };
    }""")
    for c in report["cards"]:
        print(f"  {c['place'][:28]:28} {c['vis']:20} cover={c['cover']:>7}B audio={c['audio_s']:>4}s "
              f"transcript={'yes' if c['transcript'] else 'NO'} desc={'yes' if c['desc'] else 'NO'}")
    cards = report["cards"]
    print("places:", report["places"], "| memories:", len(cards), "|", dict(Counter(c["vis"] for c in cards)))
    assert not [c for c in cards if not (c["cover"] and c["audio_s"] > 1 and c["transcript"] and c["desc"])]
    assert page.locator(".entry").count() == len(cards)

    page.locator(".entry .excerpt").first.click()
    page.wait_for_selector("#detail[open]")
    page.wait_for_timeout(700)
    page.screenshot(path=str(OUT / "seeded_detail.png"))
    page.keyboard.press("Escape")

    phone = b.new_page(viewport={"width": 390, "height": 844})
    phone.add_init_script("localStorage.setItem('meridies.user', 'user-a')")
    phone.goto(BASE + "/")
    phone.wait_for_selector(".entry")
    settle(phone)
    sw, cw = phone.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert sw <= cw, (sw, cw)
    phone.screenshot(path=str(OUT / "seeded_phone.png"))
    assert not errors, errors
    b.close()
print("seeded gallery: ok")

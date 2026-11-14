"""Check the REAL gallery on the running server (http://localhost:8765): every card complete. Read-only, except
that it flips the Samples switch off and back on, restoring what it found.

    python connect/tests/check_seeded_gallery.py [out_dir]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8765"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True)
    page = b.new_page(viewport={"width": 1280, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(BASE + "/")
    page.wait_for_selector(".place")
    page.wait_for_function("[...document.querySelectorAll('.place img')].every(i => i.complete && i.naturalWidth > 0)", timeout=60000)
    print("home:", page.inner_text("#homeTitle"))
    print("places:", [t.replace("\n", " | ") for t in page.locator(".place .body").all_inner_texts()])
    page.screenshot(path=str(OUT / "seeded_home.png"))
    assert page.locator("#exploreLink").get_attribute("href") == "/walk"

    # every card: cover, playable audio, transcript, description
    report = page.evaluate("""async () => {
      const g = await (await fetch('/api/gallery?' + gallery.auth())).json();
      const out = [];
      for (const p of g.places) for (const m of p.memories) {
        const img = await fetch(gallery.withAuth(m.cover));
        const au = await fetch(gallery.withAuth(m.audio));
        let secs = 0;
        try { const ctx = new OfflineAudioContext(1, 1, 48000); secs = (await ctx.decodeAudioData(await au.arrayBuffer())).duration; } catch (e) {}
        out.push({ place: p.name, vis: m.visibility, sample: !!m.is_sample, cover: img.ok && (await img.blob()).size,
                   audio_s: +secs.toFixed(1), transcript: (m.transcript || {}).text || '', desc: (m.description || {}).text || '' });
      }
      return { places: g.places.length, cards: out };
    }""")
    for c in report["cards"]:
        print(f"  {c['place'][:28]:28} {c['vis']:9} cover={c['cover']:>7}B audio={c['audio_s']:>4}s "
              f"transcript={'yes' if c['transcript'] else 'NO'} desc={'yes' if c['desc'] else 'NO'}  “{c['transcript'][:48]}…”")
    cards = report["cards"]
    from collections import Counter
    print("places:", report["places"], "| cards:", len(cards), "| visibility:", dict(Counter(c["vis"] for c in cards)))
    bad = [c for c in cards if not (c["cover"] and c["audio_s"] > 1 and c["transcript"] and c["desc"])]
    assert not bad, bad

    # each place page, screenshotted with the text of the first card open
    for i in range(report["places"]):
        page.goto(BASE + "/")
        page.wait_for_selector(".place")
        page.locator(".place").nth(i).click()
        page.wait_for_selector(".mem")
        page.wait_for_function("[...document.querySelectorAll('.mem img')].every(i => i.complete && i.naturalWidth > 0)", timeout=60000)
        page.locator(".mem .showtext").first.click()
        sw, cw = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
        assert sw <= cw, f"page scrolls sideways: {sw} > {cw}"
        page.screenshot(path=str(OUT / f"seeded_place{i + 1}.png"), full_page=True)

    # one click hides every sample, one click brings them back
    page.goto(BASE + "/")
    page.wait_for_selector("#samplesBtn:not([hidden])")
    label = page.inner_text("#samplesBtn")
    real = page.evaluate("fetch('/api/settings').then(r => r.json()).then(s => s.real)")
    if label.startswith("Hide") and not real:
        said = []
        page.once("dialog", lambda d: (said.append(d.message), d.dismiss()))
        page.click("#samplesBtn")
        page.wait_for_timeout(500)
        print("hide with no real recordings: refused ->", said[0].splitlines()[0])
        assert page.inner_text("#samplesBtn").startswith("Hide") and page.locator(".place").count() == 3
    elif label.startswith("Hide"):
        page.click("#samplesBtn")
        page.wait_for_function("document.getElementById('samplesBtn').textContent.startsWith('Show')")
        shown = page.locator(".place").count()
        print(f"after '{label}': {shown} place cards (only real recordings remain)")
        page.screenshot(path=str(OUT / "seeded_hidden.png"))
        page.click("#samplesBtn")
        page.wait_for_function("document.getElementById('samplesBtn').textContent.startsWith('Hide')")
        page.wait_for_selector(".place")
    assert not errors, errors
    b.close()
print("seeded gallery: ok")

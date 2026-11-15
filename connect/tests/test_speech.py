"""Acceptance criteria for SPOKEN output.   pytest -s connect/tests/test_speech.py   (-s prints the lines)

LENGTH       arrival <= 4 sentences and < 20 s; tell me more <= 6 sentences; nothing over 30 s unasked
FRONT-LOAD   the first four words carry the point (a hazard, a room, a memory), never "In terms of", "There is"
NO FILLER    no "I can see", "it appears", "there seems to be", "in this image", "based on the scan", apologies, AI talk
NUMBERS      steps not metres (a hazard's height is the one exception), "about" past three steps, no decimals, no degrees
VARY         ten consecutive arrivals: no opening stem twice in a row, and several different openings overall
SILENCE      nothing changed -> same signature, so the caller stays quiet; nothing nearby -> one short line
"""
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import connect.server as srv  # noqa: E402
from connect.backend import spatial  # noqa: E402

PLAN = srv.desc.load_plan("zvKHR9DHNjJ")
SW = {s["index"]: s for s in PLAN["sweeps"]}
BANNED = ["i can see", "it appears", "there seems to be", "in this image", "based on the scan", "let me describe",
          "sorry", "as an ai", "loading", "analysing", "analyzing", "fetching", "degree", "in terms of"]
WEAK_OPENINGS = ("there is", "there are", "in terms", "this location", "it contains", "you are currently")


def mem(mid, x, y, text, days_ago_iso="2026-11-05T10:00:00+00:00", author="me"):
    return dict(id=mid, author_id=author, position=dict(x=x, y=y), created_at=days_ago_iso,
                transcript=dict(text=text), description=None)


CH = next(h for h in PLAN["hazards"] if h["kind"] == "chandelier")
MEMS = [mem("chand", CH["position"][0], CH["position"][1] + 0.6, "Careful, the chandelier here hangs low."),
        mem("movies", SW[8]["position"]["x"], SW[8]["position"]["y"], "Movie night, everyone piled in."),
        mem("kettle", SW[17]["position"]["x"], SW[17]["position"]["y"], "Kettle's right of the sink.", author="theo"),
        mem("bed", SW[30]["position"]["x"], SW[30]["position"]["y"], "Spare key in the blue box.")]
NAME = lambda a: {"theo": "Theo"}.get(a, a)  # noqa: E731


def route():
    """A walk neighbour to neighbour through every room, twice round, facing the way of travel."""
    path, cur, seen = [], 4, set()
    targets = [6, 8, 10, 17, 19, 21, 28, 30, 33, 35, 0] * 2
    for t in targets:
        seen.clear()
        while cur != t and len(path) < 40:   # greedy hop toward the target through visible neighbours
            nb = min(SW[cur]["neighbours"], key=lambda i: math.dist((SW[i]["position"]["x"], SW[i]["position"]["y"]),
                                                                    (SW[t]["position"]["x"], SW[t]["position"]["y"])))
            if nb in seen and nb != t:
                break
            seen.add(nb)
            prev, cur = cur, nb
            p, q = SW[prev]["position"], SW[cur]["position"]
            path.append((cur, math.degrees(math.atan2(q["x"] - p["x"], q["y"] - p["y"])) % 360))
        cur = t
    return path


def check_line(text, max_sentences, max_s):
    low = text.lower()
    sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
    assert len(sents) <= max_sentences, (len(sents), text)
    assert spatial.seconds(text) < max_s, (spatial.seconds(text), text)
    for b in BANNED:
        assert b not in low, (b, text)
    assert not re.search(r"\d\.\d", text), text                     # no decimals
    assert not re.search(r"\d+ (?:steps|metres|meters)", text), text  # numbers are words
    for s in sents:
        if re.search(r"\bmetres?\b", s):                               # metres only for a hazard's height
            assert any(h["kind"] in s.lower() for h in PLAN["hazards"]), s
    assert not low.startswith(WEAK_OPENINGS), text


def test_ten_consecutive_arrivals():
    lines, prev, silent, steps = [], "", 0, 0
    for sweep, heading in route():
        p = SW[sweep]["position"]
        r = spatial.arrival(PLAN, p["x"], p["y"], heading, MEMS, "me", NAME, prev=prev)
        prev, steps = r["sig"], steps + 1
        if not r["text"]:
            silent += 1                                               # nothing changed: silence
            continue
        lines.append(r["text"])
        if len(lines) == 10:
            break
    print(f"\n--- ten consecutive arrivals ({steps} steps walked, {silent} silent) ---")
    for i, t in enumerate(lines, 1):
        print(f"{i:2}. ({spatial.seconds(t):4.1f}s) {t}")
    assert len(lines) == 10 and silent > 0
    for t in lines:
        check_line(t, max_sentences=4, max_s=20)
    stems = [" ".join(t.split()[:2]).lower().strip(".,:;") for t in lines]
    for a, b in zip(stems, stems[1:]):
        assert a != b, f"same opening twice in a row: {a!r}"
    assert len(set(stems)) >= 6, stems


def test_hazard_is_front_loaded_and_carries_the_note():
    hx, hy = CH["position"][:2]
    for _ in range(6):   # every variant
        r = spatial.arrival(PLAN, hx, hy - 1.4, 0.0, MEMS, "me", NAME)
        first4 = " ".join(r["text"].split()[:4]).lower()
        assert "chandelier" in first4, r["text"]
        assert "careful, the chandelier here hangs low" in r["text"].lower()
        check_line(r["text"], 4, 20)


def test_nothing_nearby_is_one_short_line():
    far = SW[28]["position"]
    r = spatial.arrival(PLAN, far["x"], far["y"], 0.0, [], "me", NAME)
    print("\nnothing nearby:", r["text"])
    assert spatial.seconds(r["text"]) < 6 and r["text"].count(".") <= 2


def test_nothing_changed_is_silence():
    p = SW[8]["position"]
    a = spatial.arrival(PLAN, p["x"], p["y"], 0.0, MEMS, "me", NAME)
    b = spatial.arrival(PLAN, p["x"], p["y"], 0.0, MEMS, "me", NAME, prev=a["sig"])
    assert a["text"] and b["text"] == ""


def test_around_is_capped_and_clean():
    p = SW[6]["position"]
    r = spatial.around(PLAN, p["x"], p["y"], 0.0, MEMS, "me", NAME)
    text = " ".join([r["bearings"], r["description"] or ""])
    print("\naround:", text)
    check_line(text, max_sentences=6, max_s=30)


def test_clean_strips_filler_and_metres():
    t = spatial.clean("I can see a table about 2 meters ahead. It appears that there is a door. In this image, a lamp. "
                      "Sorry, I can't tell. There seems to be a step at 1.5 m. Fifth sentence. Sixth. Seventh.", max_sentences=6)
    print("\nclean:", t)
    assert not any(b in t.lower() for b in ["i can see", "it appears", "in this image", "sorry", "seems"])
    assert "about three steps" in t or "three steps" in t
    assert "A door" in t and "one metre five" in t                    # front-loaded; a height stays a height
    assert len(re.split(r"(?<=[.!?])\s+", t)) <= 6
    assert t.startswith("A table")

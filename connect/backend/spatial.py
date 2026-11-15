"""The spatial resolver: what is around a spot, said the way a friend who knows the room would say it. No AI.

Intent decides what plays; AI only generates content, and most of it is pre-computed:
  arrive somewhere    -> arrival(): one short line, or silence when nothing changed   (instant)
  "what's around me"  -> around(): bearings + the cached description                 (instant)
  "play that memory"  -> the original recording                                      (a file)
  "tell me more"      -> ONE live Gemini call, explicit request only                  (server.tell_me_more)

Spoken-output rules (acceptance criteria, tested in tests/test_speech.py):
  - the most important thing in the first four words; hazards before anything else
  - steps, never metres, except a hazard's height (a tier-1 anchor); "about" past three steps; never degrees
  - no filler ("I can see", "it appears", "there seems to be", "in this image", "based on the scan"), no apologies
  - openings rotate: the same sentence stem is never used twice in a row
  - arrival: at most 4 sentences, under 20 seconds; nothing nearby = one short line
Heading is degrees clockwise from map +Y; None = unknown (the hat page): then distances only, no left/right.
"""
from __future__ import annotations

import math
import random
import re
from datetime import datetime, timezone

STEP_M = 0.7          # one walking step
HAZARD_LINK_M = 1.0   # a memory anchored this close to a hazard is about the same thing
HAZARD_SAY_M = 3.5    # hazards worth mentioning
NEAR_M = 5.0          # memories considered "here"
WORDS_PER_S = 2.5     # speech rate used to estimate length (rate 0.95 voices)
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def say_num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


# ---------- variety: never the same stem twice in a row ----------
_last: dict[str, int] = {}


def vary(key: str, options: list[str]) -> str:
    """Pick one of several sentence forms, never the one used last time for this key."""
    idx = [i for i in range(len(options)) if i != _last.get(key)] or [0]
    i = random.choice(idx)
    _last[key] = i
    return options[i]


# ---------- numbers and directions ----------
def height_words(h: float) -> str:
    """1.6 -> 'one metre six', 2.0 -> 'two metres'."""
    whole, tenth = divmod(round(h * 10), 10)
    if not tenth:
        return f"{say_num(whole)} metre{'s' if whole != 1 else ''}"
    return f"{say_num(whole)} metre{'s' if whole > 1 else ''} {say_num(tenth)}"


def steps_words(d: float) -> str:
    steps = max(1, round(d / STEP_M))
    if steps > 12:
        return "a good way"
    return f"{'about ' if steps > 3 else ''}{say_num(steps)} step{'s' if steps != 1 else ''}"


def relative(x, y, heading, tx, ty) -> tuple[float, float | None]:
    """(distance m, body angle deg: 0 ahead, + right) of target (tx, ty) seen from (x, y) facing heading."""
    dx, dy = tx - x, ty - y
    d = math.hypot(dx, dy)
    if heading is None:
        return d, None
    return d, (math.degrees(math.atan2(dx, dy)) - heading + 540) % 360 - 180


def side_words(a: float | None) -> str:
    if a is None:
        return ""
    side, b = ("right" if a > 0 else "left"), abs(a)
    return ("ahead" if b < 20 else f"ahead on your {side}" if b < 65 else f"to your {side}" if b < 115
            else f"behind you on your {side}" if b < 160 else "behind you")


def where_words(d: float, a: float | None) -> str:
    """'two steps ahead on your left', 'right here', 'about six steps away'."""
    if d < 0.5:
        return "right here"
    s = side_words(a)
    return f"{steps_words(d)} {s or 'away'}"


def when_words(iso: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    days = (now.date() - datetime.fromisoformat(iso).astimezone(timezone.utc).date()).days
    return ("earlier today" if days <= 0 else "yesterday" if days == 1 else "a few days ago" if days < 7
            else "last week" if days < 14 else "a few weeks ago" if days < 31 else "last month" if days < 62
            else "a while back")


def quote(text: str, max_words: int = 14) -> str:
    first = text.strip().split(". ")[0].rstrip(".")
    words = first.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


# ---------- model text: same rules as the templates ----------
FILLER = [
    (r"\b(?:I can see|I see|we can see|you can see)\s+(?:that\s+)?", ""),
    (r"\bit (?:appears|seems|looks like)(?: that)?\s+", ""),
    (r"\bthere (?:seems|appears) to be\b", "there's"),
    (r"\b(?:in|from) (?:this|the) (?:first |second |third )?(?:image|photo|picture|scan|render|view|overhead view|aerial(?: photo)?)s?,?\s*", ""),
    (r"\bbased on (?:the|this) [^,.]*,?\s*", ""),
    (r"\blet me (?:describe|explain)[^.]*\.\s*", ""),
    (r"\b(?:I'm|I am) (?:sorry|afraid)[^.]*\.\s*", ""),
    (r"\bsorry[^.]*\.\s*", ""),
    (r"\bas an AI[^,.]*[,.]\s*", ""),
    (r"\bat a bearing of [\d.]+ degrees\b", ""),
    (r"\b[\d.]+ ?degrees\b", ""),
]
_METRES = re.compile(r"(\bat\s+|\b)(\d+(?:\.\d+)?)\s*(?:m|metres|meters)\b(\s*(?:high|tall|up|above|from the floor|off the floor))?", re.I)


def _metres(m) -> str:
    """A distance becomes steps; a height ('at 1.5 m', '1.6 m high') stays a height, said in words."""
    at, v, high = m.group(1), float(m.group(2)), m.group(3)
    if at.strip() or high:
        return f"{at}{height_words(v)}{high or ''}"
    return steps_words(v)


def clean(text: str, max_sentences: int | None = None) -> str:
    """Strip filler, apologies and AI talk; distances in steps; optionally keep only the first N sentences."""
    t = " " + (text or "").strip() + " "
    for pat, rep in FILLER:
        t = re.sub(pat, rep, t, flags=re.I)
    t = _METRES.sub(_metres, t)
    t = re.sub(r"\s+([,.;])", r"\1", re.sub(r"\s{2,}", " ", t)).strip()
    # front-load: "There is a door ..." -> "A door ..."
    sents = [cap(re.sub(r"^(?:there(?:'s| is| are))\s+(?=(?:an?|some|two|three|several|the)\b)", "", s.strip(), flags=re.I))
             for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    if max_sentences:
        sents = sents[:max_sentences]
    return " ".join(sents)


def seconds(text: str) -> float:
    return len(text.split()) / WORDS_PER_S


# ---------- hazards and the notes left at them ----------
def note_words(m: dict, viewer: str, name_of) -> str:
    t = (m.get("transcript") or {}).get("text")
    said, when = (f"'{quote(t)}'" if t else ""), when_words(m["created_at"])
    if m["author_id"] == viewer:
        return vary("note.me", [f"You left a note here {when}: {said}." if said else f"You left a note here {when}.",
                                f"Your note from {when}: {said}." if said else f"You left a note here {when}.",
                                f"{cap(when)} you said here: {said}." if said else f"You left a note here {when}."])
    who = name_of(m["author_id"])
    return vary("note.friend", [f"{who} left a note here {when}: {said}." if said else f"{who} left a note here.",
                                f"{who}'s note: {said}." if said else f"{who} left a note here."])


def hazard_notes(hazard: dict, memories: list[dict]) -> list[dict]:
    """Memories anchored within 1 m of the hazard: they refer to the same thing (geometry only)."""
    hx, hy = hazard["position"][:2]
    near = [(math.hypot(m["position"]["x"] - hx, m["position"]["y"] - hy), m) for m in memories]
    return [m for d, m in sorted(near, key=lambda t: t[0]) if d <= HAZARD_LINK_M]


def hazard_line(h: dict, x, y, heading, memories, viewer, name_of) -> str:
    d, a = relative(x, y, heading, *h["position"][:2])
    kind, ht, where = h["kind"], height_words(h["height_m"]), where_words(d, a)
    line = vary("hazard", [f"{cap(kind)}, {ht}, {where}.",
                           f"Mind the {kind}: {ht}, {where}.",
                           f"Low {kind}, {where}, at {ht}."])
    for m in hazard_notes(h, memories)[:1]:
        line += " " + note_words(m, viewer, name_of)
    return line


# ---------- rooms and doorways ----------
def room_at(plan, x, y, floor=0):
    from .places import _inside
    for r in plan.get("rooms", []):
        if r.get("floor", 0) == floor and _inside(x, y, r["polygon"]):
            return r
    return None


def doorway(plan, x, y, heading, floor=0):
    """The nearest doorway within 3 m and the room on its far side."""
    here = room_at(plan, x, y, floor)
    best = None
    for o in plan.get("openings", []):
        if o.get("type") != "doorway":
            continue
        d, a = relative(x, y, heading, *o["center"][:2])
        if d > 3 or (best and d >= best[0]):
            continue
        to = None
        for k in range(8):
            r = room_at(plan, o["center"][0] + 0.6 * math.cos(k * math.pi / 4), o["center"][1] + 0.6 * math.sin(k * math.pi / 4), floor)
            if r and (not here or r["id"] != here["id"]):
                to = r["label"].lower()
                break
        if here and to == here["label"].lower():
            continue                      # "Bathroom. Door to the bathroom." says nothing useful
        best = (d, a, to)
    return best


def _memories_near(x, y, heading, memories):
    near = sorted(((relative(x, y, heading, m["position"]["x"], m["position"]["y"]), m) for m in memories), key=lambda t: t[0][0])
    return [(r, m) for r, m in near if r[0] <= NEAR_M]


def _memory_sentence(near, skip_ids) -> str:
    rest = [(r, m) for r, m in near if m["id"] not in skip_ids]
    if not rest:
        return ""
    (d, a), _ = rest[0]
    n = len(near)
    count = "one memory" if n == 1 else f"{say_num(n) if n <= 12 else n} memories"
    w = where_words(d, a)
    if n == 1:
        return vary("mem1", [f"One memory, {w}.", f"A memory's {w}.", f"There's a memory {w}."])
    return vary("memN", [f"{cap(count)} here; the closest is {w}.", f"{cap(count)} nearby, the nearest {w}.",
                         f"Closest of {count}: {w}."])


def _signature(room, hazard_ids, near) -> str:
    return "|".join([room["id"] if room else "-", ",".join(sorted(hazard_ids)), ",".join(sorted(m["id"] for _, m in near))])


def _parse(sig: str):
    room, hz, mems = (sig.split("|") + ["", "", ""])[:3] if sig else ("", "", "")
    return room, set(filter(None, hz.split(","))), set(filter(None, mems.split(",")))


def arrival(plan: dict, x, y, heading, memories, viewer, name_of, floor=0, prev: str = "") -> dict:
    """Arriving somewhere: say only what changed since the last arrival (prev = its 'sig'), most important first:
    a head-height hazard newly ahead, then a new room, then a memory newly in range. Nothing changed -> ''.
    At most 4 sentences, under 20 seconds."""
    prev_room, prev_hz, prev_mem = _parse(prev)
    room = room_at(plan, x, y, floor) if plan.get("kind") == "interior" else None
    in_range = []                       # hazards that matter on arrival: close, ahead (or heading unknown)
    for i, h in enumerate(plan.get("hazards", [])):
        d, a = relative(x, y, heading, *h["position"][:2])
        if d <= 2.2 and (a is None or abs(a) < 70):
            in_range.append((d, f"h{i}", h))
    in_range.sort(key=lambda t: t[0])
    near = _memories_near(x, y, heading, memories)
    parts = []
    new_hz = [(d, hid, h) for d, hid, h in in_range if hid not in prev_hz][:1]
    parts += [hazard_line(h, x, y, heading, memories, viewer, name_of) for _, _, h in new_hz]
    linked = {m["id"] for _, _, h in new_hz for m in hazard_notes(h, memories)}
    new_mems = [(r, m) for r, m in near if m["id"] not in prev_mem and m["id"] not in linked]
    name = room["label"].lower() if room else None
    if room and room["id"] != prev_room:
        if not near and not parts:                                           # nothing nearby: one short line
            parts.append(vary("room.empty", [f"{cap(name)}, nothing left here yet.", f"{cap(name)}, quiet.",
                                             f"Into the {name}. No memories here."]))
        else:
            parts.append(vary("room", [f"{cap(name)}.", f"Into the {name}.", f"{cap(name)} now."]))
            if new_mems:
                parts.append(_memory_sentence(near, linked))
    elif new_mems:
        (d, a), m = new_mems[0]
        parts.append(vary("mem.new", [f"A memory, {where_words(d, a)}.", f"New memory {where_words(d, a)}.",
                                      f"{name_of(m['author_id'])} left something {where_words(d, a)}." if m["author_id"] != viewer
                                      else f"One of yours, {where_words(d, a)}."]))
    elif not room and not prev and not near:
        parts.append(vary("empty", ["Nothing here yet.", "Quiet here.", "No memories here."]))
    text = " ".join(p for p in parts[:4] if p)
    sig = _signature(room, [hid for _, hid, _ in in_range], near)
    return {"text": text, "sig": sig, "seconds": round(seconds(text), 1)}


def around(plan: dict, x: float, y: float, heading: float | None, memories: list[dict], viewer: str, name_of,
           floor: int = 0) -> dict:
    """'What's around me' (asked for): hazards first, then the room and its door, memories, and the first sentence
    of the cached description. Instant. At most 5 sentences, about 30 seconds."""
    room = room_at(plan, x, y, floor) if plan.get("kind") == "interior" else None
    hz = sorted(((relative(x, y, heading, *h["position"][:2])[0], h) for h in plan.get("hazards", [])), key=lambda t: t[0])
    hz = [(d, h) for d, h in hz if d <= HAZARD_SAY_M][:2]
    hazards = [hazard_line(h, x, y, heading, memories, viewer, name_of) for _, h in hz]
    near = _memories_near(x, y, heading, memories)
    linked = {m["id"] for _, h in hz for m in hazard_notes(h, memories)}
    room_line = ""
    if room:
        dw = doorway(plan, x, y, heading, floor)
        door = f" Door {'to the ' + dw[2] + ', ' if dw[2] else ''}{where_words(dw[0], dw[1])}." if dw else ""
        room_line = vary("around.room", [f"{cap(room['label'].lower())}.{door}", f"You're in the {room['label'].lower()}.{door}"])
    memory_line = _memory_sentence(near, linked) or ("" if linked else "No memories right here.")
    cached = next((m["description"]["text"] for _, m in near if m.get("description")), None)
    description = clean(cached, max_sentences=1) if cached else None
    parts = hazards + ([room_line] if room_line else []) + ([memory_line] if memory_line else [])
    return {"bearings": " ".join(parts), "room_line": room_line, "hazard_lines": hazards, "memory_line": memory_line,
            "description": description, "memories_near": [m["id"] for _, m in near],
            "hazards": [h["kind"] for _, h in hz]}

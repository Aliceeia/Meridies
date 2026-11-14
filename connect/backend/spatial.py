"""The spatial resolver: what is around a spot, said the way a person would say it. Maths only, no AI.

Intent decides what plays; AI only generates content, and most of it is pre-computed:
  arrive somewhere    -> memory count + chimes                 (walk page, local)
  "what's around me"  -> around(): cached description + bearings (this module: instant)
  "play that memory"  -> the original recording                  (a file)
  "tell me more"      -> ONE live Gemini call, explicit request only (server.tell_me_more)

Bearings are body-relative (ahead / left / right / behind), never compass points. Heading is degrees clockwise
from map +Y; None means unknown (the hat page doesn't know which way you face): then only distances are given.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

STEP_M = 0.7          # one walking step
HAZARD_LINK_M = 1.0   # a memory anchored this close to a hazard is about the same thing
NEAR_M = 5.0          # memories considered "here" for tell-me-more
WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]


def say_num(n: int) -> str:
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def height_words(h: float) -> str:
    """1.6 -> 'one metre six', 2.0 -> 'two metres'."""
    whole, tenth = divmod(round(h * 10), 10)
    if not tenth:
        return f"{say_num(whole)} metre{'s' if whole != 1 else ''}"
    return f"{say_num(whole)} metre{'s' if whole > 1 else ''} {say_num(tenth)}"


def relative(x, y, heading, tx, ty) -> tuple[float, float | None]:
    """(distance m, body angle deg: 0 ahead, + right) of target (tx, ty) seen from (x, y) facing heading."""
    dx, dy = tx - x, ty - y
    d = math.hypot(dx, dy)
    if heading is None:
        return d, None
    a = (math.degrees(math.atan2(dx, dy)) - heading + 540) % 360 - 180
    return d, a


def where_words(d: float, a: float | None) -> str:
    """'two steps ahead on your left', 'right here', 'about six metres away'."""
    if d < 0.5:
        return "right here"
    steps = max(1, round(d / STEP_M))
    far = steps > 12
    amount = f"about {round(d)} metres" if far else f"{say_num(steps)} step{'s' if steps != 1 else ''}"
    if a is None:
        return f"{amount} away"
    side, b = ("right" if a > 0 else "left"), abs(a)
    if b < 20:
        return f"{amount} ahead"
    if b < 65:
        return f"{amount} ahead on your {side}"
    if b < 115:
        return f"{amount} to your {side}"
    if b < 160:
        return f"{amount} behind you on your {side}"
    return f"{amount} behind you"


def when_words(iso: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    days = (now.date() - datetime.fromisoformat(iso).astimezone(timezone.utc).date()).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return "a few days ago"
    if days < 14:
        return "last week"
    if days < 31:
        return "a few weeks ago"
    if days < 62:
        return "last month"
    return "a while ago"


def quote(text: str, max_words: int = 18) -> str:
    """The note as the person said it: the first sentence, or the first words if it runs long."""
    first = text.strip().split(". ")[0].rstrip(".")
    words = first.split()
    return " ".join(words[:max_words]) + ("…" if len(words) > max_words else "")


def note_words(m: dict, viewer: str, name_of) -> str:
    who = "You" if m["author_id"] == viewer else name_of(m["author_id"])
    t = (m.get("transcript") or {}).get("text")
    said = f": '{quote(t)}'" if t else ""
    return f"{who} left a note here {when_words(m['created_at'])}{said}."


def hazard_notes(hazard: dict, memories: list[dict]) -> list[dict]:
    """Memories anchored within 1 m of the hazard: they refer to the same thing (geometry only)."""
    hx, hy = hazard["position"][:2]
    near = [(math.hypot(m["position"]["x"] - hx, m["position"]["y"] - hy), m) for m in memories]
    return [m for d, m in sorted(near, key=lambda t: t[0]) if d <= HAZARD_LINK_M]


def hazard_line(h: dict, x, y, heading, memories, viewer, name_of) -> str:
    d, a = relative(x, y, heading, *h["position"][:2])
    line = f"{h['kind'][:1].upper()}{h['kind'][1:]}, {height_words(h['height_m'])}, {where_words(d, a)}."
    for m in hazard_notes(h, memories)[:1]:
        line += " " + note_words(m, viewer, name_of)
    return line


def room_at(plan, x, y, floor=0):
    from .places import _inside
    for r in plan.get("rooms", []):
        if r.get("floor", 0) == floor and _inside(x, y, r["polygon"]):
            return r["label"]
    return None


def around(plan: dict, x: float, y: float, heading: float | None, memories: list[dict], viewer: str, name_of,
           floor: int = 0) -> dict:
    """'What's around me': instant. memories = the ones this viewer may hear, in this building."""
    here = room_at(plan, x, y, floor) if plan.get("kind") == "interior" else None
    room_line = f"You're in the {here.lower()}." if here else ""
    parts = [room_line] if room_line else []
    hz = sorted(((relative(x, y, heading, *h["position"][:2])[0], h) for h in plan.get("hazards", [])), key=lambda t: t[0])
    hazards = [hazard_line(h, x, y, heading, memories, viewer, name_of) for d, h in hz if d <= 3.5][:2]
    parts += hazards
    n_before = len(parts)
    near = sorted(((relative(x, y, heading, m["position"]["x"], m["position"]["y"]), m) for m in memories),
                  key=lambda t: t[0][0])
    near = [(r, m) for r, m in near if r[0] <= NEAR_M]
    linked = {m["id"] for _, h in hz if _ <= 3.5 for m in hazard_notes(h, memories)}
    rest = [(r, m) for r, m in near if m["id"] not in linked]
    if rest:
        (d, a), m = rest[0]
        n = len(near)
        parts.append(f"{say_num(n).capitalize() if n <= 12 else n} {'memory is' if n == 1 else 'memories are'} close by; "
                     f"the nearest is {where_words(d, a)}.")
    elif not near:
        parts.append("No memories right here.")
    cached = next((m["description"]["text"] for _, m in near if m.get("description")), None)
    return {"bearings": " ".join(parts), "room_line": room_line, "hazard_lines": hazards,
            "memory_line": " ".join(parts[n_before:]), "description": cached,
            "memories_near": [m["id"] for _, m in near], "hazards": [h["kind"] for d, h in hz if d <= 3.5][:2]}

"""Bridge to the memory layer (connect/server.py on the memory-layer branch, default http://localhost:8765).

Reads, never writes: GET /api/memories?viewer=..  and  GET /api/memories/<id>/audio?viewer=..
Respects the memory layer's privacy rules because it asks the server AS the viewer: other people's
private memories are never returned to us.

The memory layer stores the AUDIO and an AI description of the PLACE. We add a transcript of what was
said (Gemini, cached in cache/transcripts/), so the agent can answer "what did I say about the kitchen?".
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from datetime import datetime

from google.genai import types

import config
import vision

TRANSCRIPTS = config.CACHE / "transcripts"


class Memories:
    def __init__(self, base=config.MEMORY_URL, viewer=config.MEMORY_VIEWER):
        self.base, self.viewer = base.rstrip("/"), viewer
        self.items: list[dict] = []
        self.ok = False

    def _get(self, path, **q):
        q["viewer"] = self.viewer
        with urllib.request.urlopen(f"{self.base}{path}?{urllib.parse.urlencode(q)}", timeout=5) as r:
            return r.read(), r.headers.get("Content-Type", "")

    def refresh(self) -> list[dict]:
        try:
            body, _ = self._get("/api/memories")
            self.items, self.ok = json.loads(body), True
        except Exception as e:
            print(f"[memory] memory layer not reachable at {self.base}: {e}")
            self.ok = False
        for m in self.items:
            f = TRANSCRIPTS / f"{m['id']}.txt"
            if f.exists():
                m["transcript"] = f.read_text()
        return self.items

    async def transcribe_missing(self, limit=20):
        """Transcribe memories we haven't heard yet (background; each one is one Gemini call)."""
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        todo = [m for m in self.items if "transcript" not in m][:limit]
        for m in todo:
            try:
                audio, ctype = await asyncio.to_thread(self._get, f"/api/memories/{m['id']}/audio")
                txt = await vision._generate(
                    [types.Part.from_bytes(data=audio, mime_type=ctype.split(";")[0] or "audio/webm"),
                     "Transcribe this voice memo verbatim. If it is silent or only noise, answer '(no speech)'."],
                    types.GenerateContentConfig(temperature=0), timeout=60)
                (TRANSCRIPTS / f"{m['id']}.txt").write_text(txt)
                m["transcript"] = txt
            except Exception as e:
                print(f"[memory] could not transcribe {m['id'][:8]}: {e}")

    def as_context(self, query: str = "", limit: int = 25) -> str:
        if not self.ok and not self.items:
            return "The memory layer is not reachable right now, so no past memories are available."
        if not self.items:
            return "This person has no saved memories yet."
        q = {w for w in query.lower().split() if len(w) > 2}

        def score(m):
            text = (m.get("transcript", "") + " " + ((m.get("description") or {}).get("text") or "")).lower()
            return sum(w in text for w in q), m.get("created_at", "")

        rows = []
        for m in sorted(self.items, key=score, reverse=True)[:limit]:
            when = m.get("created_at", "")
            try:
                when = datetime.fromisoformat(when).strftime("%b %d %Y, %H:%M")
            except ValueError:
                pass
            mine = "theirs" if m.get("author_id") == self.viewer else f"shared by {m.get('author_id')}"
            rows.append(" | ".join(filter(None, [
                f"memory {m['id'][:8]} ({mine}, {when}, building {m.get('building_id')})",
                f"said: \"{m['transcript'][:400]}\"" if m.get("transcript") else "said: (not transcribed yet)",
                f"place: {m['description']['text'][:300]}" if (m.get("description") or {}).get("text") else ""])))
        return "\n".join(rows)

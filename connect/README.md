# connect/ — the memory layer

Two halves, connected by one thing only: a saved recording.

| Page | What |
|---|---|
| `/live` | The hat. Status board (the obstacle list from `integration/` on ZeroMQ 5556; tones come from `audio/engine.py`), 10 s recording, camera context on demand, demo warning switch. Works with no network: the page is cached by `sw.js`, recordings go to IndexedDB first and upload whenever the server answers. You choose where you are; nothing tracks you. |
| `/` | MeriVerse, the gallery. A timeline of memories grouped by day, filterable by place (places are derived from saved memories). Each entry: cover, excerpt, Play, Take me there; opening one shows the full memory, the description of the spot, and "Who can hear this". Tabs: **Memories** / **Shared with me** (family memories from people whose circle you're in). The profile menu holds the account switch, **Your circle** (members + family link), the hat's live view and demo data. **L** = listen mode (screen dark; ← → places, ↑ ↓ memories, Space play, D describe, T take me there, H help). Design tokens are one `:root` block in `gallery.html`. |
| `/f/<token>` | Family view: only the owner's `family` memories, read-only. Made and turned off from the gallery's "Family link". |
| `/walk` | The 3D walk ("Take me there": `/walk?building=&memory=` stands you a few steps from the memory, facing it; first key chimes it). |

### How AI is used: intent decides what plays; AI only generates content, mostly ahead of time

| Request | What answers | Speed |
|---|---|---|
| arrive somewhere | memory count + chimes (walk page, local) | instant |
| "what's around me" (W in the walk, C on /live) | `/api/around`: cached description + bearings from `backend/spatial.py` (maths) | instant, no AI |
| "play that memory" (Space / Play) | the original recording | instant |
| "tell me more" (M, or the button) | `/api/tell-me-more`: ONE Gemini call with the first-person render here, the transcripts within 5 m the viewer may hear, and the resolved bearings. The model is told not to state directions or distances; those are spoken from the resolver. | 2–3 s, only on request |

Pre-computed once, after a memory is saved: the transcript and the description of the spot. Nothing calls Gemini on
movement. **Hazards and memories are linked by geometry:** a memory anchored within 1 m of a hazard is about the same
thing, so the hazard line carries the note: *"Chandelier, one metre six, three steps ahead. You left a note here last
week: 'Careful, the chandelier here hangs low'."* Tests: `pytest connect/tests/test_spatial.py`.

Sample memories (`tools/seed_samples.py`, `is_sample: true`): your own in the Princeton foyer and the La Mirada dining
room, and ones shared with you by two blind friends, Maya and Theo (you're in their circles). Voices are Microsoft
neural voices via `edge-tts` (seed tool only; `uv pip install edge-tts`), one voice per person.

### Gallery rules (enforced by the server, tested in `tests/test_gallery_server.py`)

- `visibility`: `private` (default) | `family` | `community`. Only the author changes it. `community` is only ever a
  pending-review request in a public space (no browsing UI yet). Private audio, covers and images answer 403 to
  everyone else.
- A place exists only because a visible memory was saved there: places are computed per request (building + room,
  or yard zone) from what that viewer may see. No visits, paths or dwell times are stored anywhere.
- After each save one background worker fills in: Gemini transcript (ffmpeg → mp3), cover image (hat camera frame,
  else the first-person scan render at that sweep and heading, else the drone-scan crop for the yard), description.
- Hat buttons: `POST /api/hat/button {"button": "left|right|up|down|play|describe|there|help|listen"}` drives listen
  mode in any open gallery page (SSE `/api/hat/events`). Only the button name travels.

Tests: `pytest connect/tests/test_gallery_server.py connect/tests/test_sharing_server.py`,
`python connect/tests/e2e_gallery.py listen`, `python connect/tests/e2e_live.py` (both run their own server on a
scratch copy; your memories are not touched).

## The walk (original memory layer)

Arrive at a place → record (until you stop) → hear it back → keep it private (default) → come back later → hear it
from that direction → optionally an AI description of the place from aerial + drone imagery.

The only link to the hardware half: while the hat is warning, memory audio stays silent
(`window.setHatWarning(true|false)` in the page; a demo checkbox stands in for it).

## Run

```
# from the repo root, with the kit's venv (one folder up) active
python connect/server.py          # http://localhost:8765
```

`models_index.csv` and `.env` (VEXCEL_API_KEY, GEMINI_API_KEY) are read from the folder
*above* the repo, so no secrets or sponsor data live in git.

## Pieces

| File | What |
|---|---|
| `spike/build_floorplan.py` | Houston splat (4606 Kinglet St, `1PVeAYmPNJz`) → levelled ground plane (vertical axis found by RANSAC, not assumed), walkable polygon, zones, top-down backdrop. |
| `spike/georef.py` | Registers the splat frame to a Vexcel ortho (rotation × translation, edge NCC) → `georef` in floorplan.json: lat/lon of the map origin, rotation, compass. **Run after build_floorplan.py** (which rewrites the file). |
| `server.py` | Stdlib server: frontend, `/api/floorplan`, memories (`connect/data/memories/`, git-ignored), describe (Vexcel + splat crops → Gemini). |
| `frontend/` | Vanilla JS, no build. SVG map, click to walk, record, HRTF proximity chime + confirmed playback, AI description + speech, user switch + visibility. |
| `tests/e2e_memory.py` | Drives installed Chrome with a fake mic: `python connect/tests/e2e_memory.py 2|3|4|5`. |

## Indoors (default building): 5836 La Mirada Ave, Los Angeles (`zvKHR9DHNjJ`)

Public Matterport showcase, no API key. Measured, not assumed:
`ss` = sweep index **+ 1**; `sr` = **pitch,yaw** (radians); showcase yaw is counter-clockwise from +Y,
so `yaw = -map_heading`; frame is +Z up (camera 1.496 m above each floor point).

| File | What |
|---|---|
| `backend/interior_render.py` | `render_view(sweep, yaw)` → PNG via headless Chrome; waits for a stable, textured frame; crops chrome; caches in `data/cache/interior/`. |
| `spike/interior_map.py` | Sweeps + neighbours from the page's own data, rooms/walls/doors from GetRoomBounds, georef from showcase geocoordinates → `data/interior_floorplan.json`. |
| `spike/interior_hazards.py` | Gemini Vision on 4 renders per sweep; hazards kept only if seen from ≥2 sweeps. **Needs quota**: free tier is 20 requests/day/model. |
| `backend/describe.py` | Three images to Gemini: first-person (primary), Vexcel aerial, floor plan. |

Walking indoors is sweep to sweep (click a highlighted sweep, ↑/↓ to the neighbour ahead/behind);
the first-person panel shows the pre-rendered view nearest the heading (4 per sweep).

## Memory record

```json
{"id": "...", "position": {"x": 8.26, "y": 0.53, "z": 1.5}, "building_id": "1PVeAYmPNJz",
 "author_id": "user-a", "created_at": "2026-11-14T06:15:36+00:00", "visibility": "private",
 "description": {"text": "...", "model": "gemini-2.5-flash", "lat": 29.66, "lon": -95.45, ...},
 "audio": "/api/memories/<id>/audio"}
```

Map frame: metres, origin at the house, +Z up, floor z=0. Map +Y points (almost) east here;
`compass = map_heading - georef.rotation_deg`.

## Rules the code enforces

- Nothing is recorded without a press. The person stops it (Stop, R or Enter); a reminder is spoken at 3 min and
  it stops by itself at 5 min (kept private), or just before the 5 MB upload cap.
- After recording: playback -> one spoken question -> saved (`frontend/after_record.js`, a pure state machine).
  Private is the default for silence, timeout, Escape, leaving, and any interruption (`window.meridies.interrupt()`).
- Community sharing exists only where `building.public === true` (missing = not public) and only creates a
  `pending_review` request; `backend/moderation.py` is a stub that never approves. Tests: `node --test
  connect/tests/after_record.test.js`, `pytest connect/tests/test_sharing_server.py`.
- New memories are private. Only the author can change visibility. Others see only `community` ones,
  and private audio/images answer 403 to anyone else.
- Voices never auto-play: a chime (HRTF, from the anchor's direction) offers, a press plays.
- One AudioContext, created on the first gesture; 8 ms fades on every start/stop.

`author_id` is a demo switch, not authentication.

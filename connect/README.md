# connect/ — the memory layer

Arrive at a place → record 10 s → anchored to coordinates → come back later → hear it
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

- Nothing is recorded without a press; the mic closes after 10 s.
- New memories are private. Only the author can change visibility. Others see only `community` ones,
  and private audio/images answer 403 to anyone else.
- Voices never auto-play: a chime (HRTF, from the anchor's direction) offers, a press plays.
- One AudioContext, created on the first gesture; 8 ms fades on every start/stop.

`author_id` is a demo switch, not authentication.

# Guide for the audio-side agent: consuming object warnings

**Audience:** whoever owns `audio/` (Person B or their agent).
**Status:** nothing here is required for the demo. `sensing_pi/main.py` (its object stream, `objects/run.py`) already speaks the current warning-list contract, and the engine works unchanged. These are suggested upgrades so the sound can carry what the new object layer knows.
**Rule:** the sensing side does not edit `audio/`; the audio side does not need to edit `sensing_pi/`.

## 1. What arrives now
- **Transport:** ZMQ PUB at `tcp://*:5556`, bound by the object stream (`main.py` → `objects/run.py`). The engine's SUB at `tcp://localhost:5556` works as-is. There's one JSON list per frame, at about 10 Hz (the sensor frame rate; the 0.5 s stale timeout is fine).
- **The list:** at most 3 entries, **nearest first**. An empty list means silence. When the object stream exits it sends `[]`.

| Field | Type | Meaning | Engine today |
|---|---|---|---|
| `id` | str `"O<n>"` | stable **object track id**; stays the same while the object is tracked | sticky voice slot ✔ |
| `az` | float, deg | nearest point of the object, 0 = ahead, **+ right** | ✔, snapped to 15°, clamped ±90 |
| `dist` | float, m | **straight-line** distance from the head centre to the nearest point, never null. The horizontal part is `dist × cos(el)` (also sent as `hdist`). | ✔ hum, beep rate, urgent; horizontal part = beep trigger |
| `hdist` | float, m | horizontal distance to the nearest point (`dist × cos(el)`) | not read (mixer recomputes it) |
| `level` | `"warn"` \| `"urgent"` \| `"wall"` | `"wall"` = corridor wall beside the user, under 0.5 m | only `"urgent"` is special; **`"wall"` currently beeps like warn** |
| `kind` | `narrow` \| `wide` \| `wall_facing` \| `wall_oblique` \| `wall_parallel` | object type | ignored |
| `az_l`, `az_r` | float, deg | left / right edge of the object | ignored |
| `width_m` | float, m | distance between the object's end points | ignored |
| `el` | float, deg | elevation of the nearest point (+ up) | ignored |
| `height_m` | [zmin, zmax], m | vertical extent relative to the head centre | ignored |
| `closing_mps` | float | approach speed (+ = getting closer) | ignored |
| `ttc_s` | float, s, **optional** | time to contact; absent when not approaching | ignored |

**Mock without hardware:**
```
cd sensing_pi && .venv/bin/python main.py --sim corridor
```
Scenes: `wall`, `pole_sweep`, `approach`, `pole_wall`, `corridor`. This publishes realistic object lists on 5556, which is handy alongside `audio/engine.py`. Don't run `audio/mock_warnings.py` at the same time; both bind 5556.

## 2. Suggested changes, in priority order

### 2.1 Treat `level: "wall"` as a quiet, distinct cue (small)
- **Where:** `audio/mixer.py`, at the `level` check (currently `w.get("level") == "urgent"`, around line 115).
- **What:** for `"wall"`, play a soft, low, slow cue on that side, e.g. −12 dB relative to warn, a fixed slow rate of about 0.8 s, and no lowpass brightening. Don't treat it as urgent. Blind travellers use walls to orient, so it should be audible but calm. Ask the blind tester whether they want it at all; a mixer flag to mute it is cheap.

### 2.2 Per-kind timbre (medium)
- **Where:** `audio/hrtf_render.py` (render more sample sets) and `audio/mixer.py` (choose a set per entry).
- **Sounds:**
  - `narrow`: a short, sharp click or tick, 20–40 ms with a bright transient. This covers poles, door edges and people, the classic head-height hazards.
  - `wide`, `wall_facing`, `wall_oblique`: a softer, lower band-limited noise burst, 80–120 ms.
  - `wall` level: see 2.1.
- **How:**
  - Keep the 3 voice slots for "which object". The kind only chooses the waveform family, so render `click_az±NNN.wav`, `broad_az±NNN.wav` and `wall_az±NNN.wav` alongside the current `voice{1..3}`.
  - A mixed approach also works: keep the voice pitch per slot and swap only the envelope and noise content per kind.
  - Loudness-normalise across kinds, as is done today across angles.
  - Unknown or missing `kind` → current behaviour.

### 2.3 Hearing width (medium, optional)
- **What:** for a `wide`/`wall_*` entry with `az_r − az_l ≥ 20°`, play each beep as a quick sweep from `az_l` to `az_r` (3–4 pre-rendered angles about 25 ms apart), or as a pair at the two ends. It's still **one sound per object**, so the 3-voice limit holds.
- **Where:** the beep-onset scheduling in `mixer.py` (where it snaps `az` once per onset). Snap each sub-step to the nearest rendered angle.
- **Narrow objects:** keep them a single point.

### 2.4 Robustness in the audio callback (small, recommended)
Today a malformed message raises inside the sounddevice callback (`engine.py` ~55 → `mixer.py` ~149). The sensing side guarantees the schema, but defensive parsing is cheap:
- Accept only a JSON **list**; otherwise treat it as `[]`.
- Skip entries without `id`/`az`/`dist`, or with a null or non-finite value.
- `float()` the numbers, and keep using `[:3]`.

### 2.5 Rear angles (optional)
The cap covers −107°…+107°. Entries beyond ±90° are currently clamped to ±90°, which reads as "beside you". If that's confusing, render −135°, +135° and 180°, or keep the clamp. It's a deliberate choice; tell sensing if you'd rather we clamp before sending.

### 2.6 Tests and mocks to add on the audio side
- **`audio/mock_warnings.py`:** add scenarios with the new fields, e.g. a narrow object closing in, a wide wall at az −10…+40 facing, and a `level: "wall"` at az +80. Keep the old scenarios.
- **`audio/test_mixer_smoke.py`:**
  - entries with `kind`, `az_l`/`az_r` and an absent `ttc_s` render without NaN
  - `level: "wall"` is quieter than warn (RMS check)
  - a malformed entry (`dist: null`) doesn't raise, if 2.4 is done

## 3. Notes for the integration owner (Person C)
- For the VL53L5CX hardware, `sensing_pi/main.py` (object stream: `objects/run.py`) **replaces** the sector-based selector (`integration/obstacle_select.py` on `memory-layer`). That selector expects six single-zone readings on port 5555; the TTC idea and thresholds are carried over in `sensing_pi/objects/track.py` and `objects/rank.py`.
- The old frozen frame `{"t", "mm": [6]}` on port 5555 isn't produced. If something still needs it, ask sensing for a small adapter. It would publish, per sensor, the nearest valid zone in the middle rows.
- **Only one publisher on 5556.**
- Head-rotation suppression (gyro > 60°/s) waits for the MPU6050 hook (`sensing_pi/geometry/orientation.py`).

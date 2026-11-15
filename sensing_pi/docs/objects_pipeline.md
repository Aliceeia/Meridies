# Object pipeline: ToF zones → objects → audio warnings

`run_objects.py` turns six VL53L5CX 8×8 frames into **objects**: a pole, a person, a wall, a table edge. It sends the top 3 to the audio engine, one sound per object, placed at the object's nearest point. The same code runs the flat bench plate and the cap; only the rig config changes.

```
source ─► frame_to_points ─► height band ─► az-binned scan ─► local map ─► cluster ─► corner split
          (rig json)                         (nearest/bin)    (0.3–1.5 s)  (dist-similar)
     ─► describe + wall fit ─► track (ids, TTC) ─► rank ─► top-3 list ─► ZMQ :5556  → audio/engine.py
                                                                     └► TCP :5558  → tools/objects_view.py
```

## Running (from `sensing_pi/`)

| What | Command |
|---|---|
| Sensors are already streaming (`tof6_record.py --serve 5557`) | `.venv/bin/python run_objects.py --rig config/flat_mounts.json --source tcp:localhost:5557` |
| Read the sensors directly (don't run the recorder at the same time) | `.venv/bin/python run_objects.py --rig config/flat_mounts.json --source live` |
| Replay a recording | `.venv/bin/python run_objects.py --source csv:rec.csv --no-pub` (add `--fast` to skip real-time waits) |
| No hardware: simulated scene | `.venv/bin/python run_objects.py --rig config/cap_mounts.json --source sim:corridor` (scenes: `wall pole_sweep approach pole_wall corridor`) |
| Laptop debug view (top-down) | `python tools/objects_view.py --live <pi-address>:5558` (needs `numpy matplotlib`) |
| Tests | `.venv/bin/python tests/objects/test_objects.py` and `.venv/bin/python tests/geometry/test_geometry.py` |

It publishes on `tcp://*:5556`, the port `audio/engine.py` listens to, so start the audio engine as usual and it will play the objects. **Only one process may publish on 5556**: don't run `audio/mock_warnings.py` or another selector at the same time.

## Rigs
- **`config/flat_mounts.json`:** the bench plate.
  - Six boards all facing forward, 2.5 cm apart in a row, 4 cm between rows, origin at the plate centre.
  - The bottom row (S4–S6) is mounted rotated 180°, so it has `roll_deg: 180`.
  - All six views overlap; the scan fuses them by taking the nearest reading per azimuth bin.
  - The plate only sees ±22.5°, so a side wall shows up as a short "narrow" piece at the edge of the view.
- **`config/cap_mounts.json`:** the cap layout from `docs/cap_layout.md`, about 215° horizontally plus a downward sensor.
- **Sensor positions and angles for both rigs are edited in `config/sensor_layout.py`** (the `FLAT` and `CAP` tables: x_cm right, y_cm front, angle counter-clockwise with 90 = left, tilt − = down). To wear the flat plate, add the plate-to-head-centre offset to every row in `FLAT`. Check with `.venv/bin/python -m geometry.layout config/flat_mounts.json`.

### Zone orientation: set once with the hand test
`zone_flip_h` / `zone_flip_v` at the top of the rig file say how the VL53L5CX's own zone order maps onto the scene for a board mounted upright. It's the same for every board, so it's set once per rig file. The defaults are `false`/`false`. Check them:

1. Run `run_objects.py ... --source tcp:localhost:5557` together with the debug view, or use `tests/multi6/tof6_view.py`.
2. Hold a flat hand about 30 cm in front of the **top row** and move it to **your right**. The object's `az` must go **positive**. If it goes negative, set `zone_flip_h: true`.
3. Move the hand from chest height to above the plate. In the debug stream, `el` must go **up** (positive). If it goes down, set `zone_flip_v: true`.
4. Repeat in front of the bottom row. If it disagrees with the top row, the `roll_deg` of the bottom row is wrong.

**Current setting: `zone_flip_v: true` in both rig files** (live run on 2026-11-14). The desk lies 7–14 cm *below* the plate only with `true`, and the bottom row's lowest zones then hit it at about 0.30 m, as expected for a plate about 10 cm above the desk. `zone_flip_h` is still unconfirmed: do step 2 of the hand test.

## How each step works
1. **Points:** `geometry.frame_to_points` places every valid zone in the head frame (x forward, y right, z up). Points closer than `min_range_m` are dropped; that's 10 cm on the flat rig, which removes cover-glass and near-field returns. Points outside the height band `band_z_m` are dropped too. Without an IMU the head is assumed level.
2. **Scan:** azimuth bins of `bin_deg` (3°). Each bin keeps one point: the newest capture, then the nearest. Elevation and height are kept, so `height_m` tells a hanging sign from a table edge.
3. **Local map:** recent points are kept for `map_window_s` (0.3 s), or `map_window_imu_s` (1.5 s) once an IMU exists. Without an IMU this only fills flicker. Old points fill empty bins but never become an object's "nearest point".
4. **Cluster:** walk the bins by azimuth. Two neighbours join when `|Δd| < max(0.25 m, 0.2·d)` **and** `Δaz ≤ max_gap_deg` (20°, enough to bridge the seams between cap sensors). It compares distances, not straight-line gaps, so a wall isn't split just because its points are far apart.
5. **Corner split:** a continuous surface that isn't straight (like a corridor corner) is cut at its sharpest bend. It's only cut when a piece comes out as a clean wall, so a person or chair still gets one sound.
6. **Describe:** for each object:
   - the nearest point (az, el, dist)
   - the span, `az_l`…`az_r`
   - `width_m`, the distance between the end points
   - `height_m`
   - the kind: **narrow** if the width is under 0.35 m or it covers a single bin, otherwise **wide**
7. **Walls:** applies to objects with at least 5 bins that are at least 0.6 m wide. Fit a total-least-squares line; it's a wall if every point is within 10 cm. The angle to straight ahead sets the kind:
   - under 30° → `wall_parallel`
   - over 60° → `wall_facing`
   - in between → `wall_oblique`
8. **Track:** greedy matching to the previous objects. Spans must overlap, or nearest points must be within 15°, and the distance change must be under 0.4 m.
   - Each object keeps its `O<n>` id, so the audio voice stays sticky.
   - Position is smoothed with an EMA.
   - Closing speed is the least-squares slope over 0.3 s, giving `ttc_s`; this is the method from `integration/obstacle_select.py`.
9. **Rank:**
   - Levels:
     - **warn** if d < 1.5 m or TTC < 2 s; it stays on until d > 1.7 m and TTC > 2.5 s.
     - **urgent** if d < 0.5 m or TTC < 0.8 s.
     - `wall_parallel` never warns. It gets `level: "wall"` only under 0.5 m.
   - `score = front(az) × kind_weight × max(1/d, 2/TTC)`. Narrow objects ahead win.
   - The top 3 by score are sent, sorted nearest first.

Every threshold is in `config/objects.json`. A rig file can override any of them under `"objects": {…}`; the flat rig uses that to widen the height band.

## Output to the audio engine (port 5556)
```json
[{"id":"O7","az":3.0,"dist":1.773,"level":"warn",
  "kind":"narrow","el":-33.4,"az_l":3.0,"az_r":3.2,"width_m":0.007,
  "height_m":[-1.11,-0.88],"closing_mps":0.95,"ttc_s":1.86}]
```
- **Fields the engine reads now:** `id`, `az`, `dist`, `level`.
- **Extra fields:** the engine ignores them today; see `docs/audio_integration_guide.md`.
- `ttc_s` is left out when the object isn't approaching.
- `dist` is never null.

## Known limits
- **Simulator:** it casts one ray per zone, so it misses thin poles until about 2 m. Real zones measure an area, so the hardware sees them earlier.
- **Head movement:** there's no compensation until the MPU6050 is implemented. The local map then grows to 1.5 s and points are stored in room coordinates.
- **Flat plate:** with a ±22.5° view, wide objects are only partly seen.

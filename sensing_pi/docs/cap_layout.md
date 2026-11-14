# Sensor layout on the cap (6 × VL53L5CX)

## Frame and angle conventions
Head frame, origin at the **head centre** (midpoint between the ear canals):

- **x**: forward (out of the face)
- **y**: right
- **z**: up

Angles, matching `sectors.json` and `audio/hrtf_render.py`:

- **azimuth / horizontal angle (az)**: 0° straight ahead, **+ right**, − left
- **elevation / vertical angle (el)**: 0° level, **+ up**, − down

Each sensor's mount is `az, el, roll` (where it points) plus `x, y, z` in metres (where it sits). These live in [`config/cap_mounts.json`](../config/cap_mounts.json).

## Layout

Each VL53L5CX sees 45° × 45° in 8 × 8 zones (~5.6° per zone).

| Sensor | Where on the cap | az | el | Covers horizontally | Covers vertically |
|---|---|---|---|---|---|
| S1 | brim, front-left | −40° | −10° | −62.5° … −17.5° | +12.5° … −32.5° |
| S2 | brim, front-centre | 0° | −10° | −22.5° … +22.5° | +12.5° … −32.5° |
| S3 | brim, front-right | +40° | −10° | +17.5° … +62.5° | +12.5° … −32.5° |
| S4 | crown, above left ear | −85° | −5° | −107.5° … −62.5° | +17.5° … −27.5° |
| S5 | under brim, centre, tilted down | 0° | −40° | −22.5° … +22.5° | −17.5° … −62.5° |
| S6 | crown, above right ear | +85° | −5° | +62.5° … +107.5° | +17.5° … −27.5° |

```
                 front (brim)
          S1 (−40°)  S2 (0°)  S3 (+40°)      ← head-height band, el −10°
                     S5 (0°, el −40°)        ← chest / waist band, under brim
  S4 (−85°)                          S6 (+85°)  ← sides, on crown above ears
                 back: cable run → Pi + battery pouch
```

**Why this layout:**

- **Head-height band (S1–S3, S4, S6):** about 215° of horizontal coverage (−107.5° … +107.5°), with ~5° of overlap between neighbours and no gaps. That fixes the "gaps between 27° cones" risk in the work plan, which assumed VL53L1X. The −10° tilt points the band at eye-to-shoulder height (+22 cm to −64 cm at 1 m), which is what a cane misses.
- **S5, tilted down 40°:** catches chest- and waist-height hazards such as tables, open drawers and railings. Its lower zones will also see the floor about 0.9–2 m ahead. The transform code drops floor hits with a fixed floor height for now; head pitch from the MPU6050 will make this accurate later.
- **Same left/middle/right order as the 2 × 3 bench rig** (top row S1–S3 = brim, bottom row S4–S6 = left, down, right), so the wiring, `tof6_record.py` and the viewer need no changes.
- **Audio:** sounds are pre-rendered for −90° … +90°. Points beyond ±90° (only the outer half of S4/S6) are clamped to ±90°, which still reads as "beside you".

## Mounting notes
- **Brim sensors:** mount them flush with the **front edge** of the brim, so the brim itself isn't in the field of view. S5 goes on the brim's underside, set back about 2 cm from the edge.
- **Side sensors:** put them on the crown about 3 cm above the ear, so the ear and hair don't block the lower zones.
- **Angles:** set them with foam wedges or 3D-printed wedges at the angles above, then **measure the real `x, y, z`** with a ruler from the head centre (put the cap on a person or a head form) and enter them in `cap_mounts.json`. Run the calibration (below) to correct the angles.
- **Cables:** run them back along the crown to the pouch at the back. Leave a strain-relief loop at each sensor.
- **MPU6050:** put it at the back of the crown, flat and centred, with its x axis pointing forward. It will supply head pitch/roll/yaw for `geometry/orientation.py` later.

## Calibration procedure
Everything below lives in `geometry/calibrate.py`.

1. **Zone orientation, once per mounting style:** hold a hand about 30 cm in front of a sensor and move it left → right, then up → down. Watch the tile in `tof6_view.py`. If the hand moves the wrong way, set `flip_h` / `flip_v` for that sensor. If left/right and up/down are swapped, set `roll_deg` to 90 or −90.
2. **Range, per sensor, on the bench:** point the sensor square at a wall at 0.5, 1.0 and 2.0 m and record about 50 frames each. `fit_range(...)` returns the `range_scale` and `range_offset_mm` for that sensor.
3. **Mount angles, worn:** the wearer stands with head level, facing a wall at a measured distance D from the head centre. Record about 50 frames, then run `calibrate_mount(...)` for S1, S2, S3 and S5 with the wall `normal=(1,0,0), distance=D`. Then the wearer turns so the wall is on their left: run it for S4 with `normal=(0,-1,0)`. Turn so the wall is on the right: run it for S6 with `normal=(0,1,0)`. Each call returns the corrected `az, el` and the RMS error; the error should be under ~2 cm.
4. Save the results with `save_mounts(...)`.

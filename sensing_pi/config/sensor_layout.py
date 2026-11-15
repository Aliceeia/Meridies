"""EDIT HERE: where each ToF sensor sits and where it points.

This is the ONE place to enter measured sensor positions and angles. The rig files
(cap_mounts.json, flat_mounts.json) read their geometry from the tables below; they only
keep the advanced per-sensor settings (roll, zone flips, range calibration).

Conventions (top-down view of the head, nose pointing up the page):

    x_cm       + = right of the centre, - = left            (centimetres)
    y_cm       + = in front of the centre, - = behind       (centimetres)
    angle_deg  where the sensor points, counter-clockwise seen from above:
               0 = straight ahead, 90 = left, 180 = behind, 270 = right   (0 ... 360)
    tilt_deg   0 = level, negative = pointing down, positive = pointing up
    height_cm  + = above the centre, - = below  (optional, use 0.0 if unknown)

    Centre:  CAP  -> head centre, the midpoint between the ear canals (ear height).
             FLAT -> centre of the bench plate (middle column, between the two rows).

                     y (front)
                       ^
             angle 90  |  angle 0
              (left)   |
         --------------+--------------> x (right)
                       |        angle 270 (right)
                       |
                 angle 180 (behind)

After editing, check it with (from sensing_pi/):
    .venv/bin/python -m geometry.layout config/cap_mounts.json
"""

# ---------------------------------------------------------------- the cap
# Values below are the planned layout from docs/cap_layout.md. Replace them with measurements.
CAP = {
    #        x_cm    y_cm   angle_deg  tilt_deg  height_cm
    "S1": (   0.0,   15.0,     10.0,     -5.0,     0.0),   # brim, front-centre
    "S2": ( -10.7,    7.3,     75.0,    -30.0,     0.0),   # brim, front-left
    "S3": (  10.7,    7.3,    290.0,    -30.0,     0.0),   # brim, front-right
    "S4": ( -10.0,    0.0,     90.0,    -25.0,     0.0),   # crown, left-ear
    "S5": (   0.0,  -10.0,    180.0,     -5.0,     0.0),   # back down
    "S6": (  10.0,    0.0,    270.0,    -35.0,     0.0),   # crown, right-ear
}

# ---------------------------------------------------------------- the flat bench plate
# All six face forward; 2.5 cm between neighbours in a row, 4 cm between the rows.
# (The bottom row's 180-degree mounting is the "roll_deg" in flat_mounts.json, not here.)
FLAT = {
    #        x_cm    y_cm   angle_deg  tilt_deg  height_cm
    "S1": (  -2.5,    0.0,      0.0,      0.0,     2.0),   # top row, left
    "S2": (   0.0,    0.0,      0.0,      0.0,     2.0),   # top row, middle
    "S3": (   2.5,    0.0,      0.0,      0.0,     2.0),   # top row, right
    "S4": (  -2.5,    0.0,      0.0,      0.0,    -2.0),   # bottom row, left
    "S5": (   0.0,    0.0,      0.0,      0.0,    -2.0),   # bottom row, middle
    "S6": (   2.5,    0.0,      0.0,      0.0,    -2.0),   # bottom row, right
}

LAYOUTS = {"CAP": CAP, "FLAT": FLAT}

# integration — selection, launcher, testing (Person C)

Not started yet. Owns: Pi software stack, obstacle selection logic,
camera stretch, testing, demo.

Subscribes to sensing's port 5555, publishes on port 5556 for `audio/` to
consume:

```json
[{"id": "S2", "az": 15, "dist": 0.64, "level": "warn"},
 {"id": "S5", "az": -80, "dist": 0.42, "level": "urgent"}]
```

At most 3 entries, nearest first, empty list = silence. `audio/engine.py`
is already built and tested against `audio/mock_warnings.py`, which
publishes this exact format — point `engine.py --endpoint` at this
process once it's live and it should work without changes on the audio
side.

Selection logic per the plan: median of the last 3 frames per sensor;
warn under 1.5m, clear above 1.7m, urgent under 0.5m; publish the 3
nearest.

# sensing — sensor reader (Person A)

Not started yet. Owns: six VL53L1X sensors, wiring, headband, power.

Delivers: a ZeroMQ PUB process on port 5555, ~20 Hz, publishing:

```json
{"t": 123456, "mm": [812, 1430, null, 3900, 640, null]}
```

Distances in mm, in S1-S6 order (see `../sectors.json`), `null` when that
sensor's range status is invalid.

Update `../sectors.json` with the measured mounting angles once the
headband is built — `audio/` and `integration/` both read it.

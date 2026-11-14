# Meridies

Obstacle-detection headband for blind/low-vision users. Six VL53L1X
time-of-flight sensors around the head feed obstacle direction + distance
to a selection process, which drives spatial audio warnings from the
direction of each obstacle.

Three processes talk over ZeroMQ PUB/SUB on localhost:

```
sensing (A) --5555--> integration (C) --5556--> audio (B)
```

- `sectors.json` — shared sensor layout (angles), owned by A, used by all three.
- `sensing/` — sensor reader (Person A). Publishes `{"t": ..., "mm": [..]}` on port 5555.
- `integration/` — obstacle selection, launcher, testing (Person C). Publishes
  `[{"id","az","dist","level"}, ...]` on port 5556.
- `audio/` — spatial audio engine (Person B). Subscribes to port 5556, plays
  directional warnings via live ITD/ILD panning and distance-based
  loudness/reverb (no HRTF). See `audio/README.md`.

**Azimuth convention** (shared across all three): 0° = straight ahead,
negative = left, positive = right, looking down at the wearer from above.

Each piece is independently runnable against a mock of its upstream
neighbor — nobody needs to wait on hardware or on another person's process
to start working.

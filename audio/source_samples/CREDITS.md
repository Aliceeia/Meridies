# Source samples

`glock_medium_G4.wav`, `glock_medium_G5.wav`, `glock_medium_C5.wav` — raw
glockenspiel one-shots, pulled from:

**VSCO 2 Community Edition** — Versilian Studios Chamber Orchestra
Recorded by Sam Gossner & Simon Dalzell; sample cutting by Elan
Hickler/Soundemote. https://github.com/sgossner/VSCO-2-CE

License: **CC0 1.0 Universal** (public domain) — see that repo's `LICENSE`.
No attribution required; credited here anyway for provenance.

These are the full raw recordings (~7s, long room tail). `hrtf_render.py`
trims each to a short transient and applies its own fade before anything
goes through HRTF rendering — see `load_percussive_sample()`.

# audio — spatial audio engine (Person B)

Turns C's warning list into directional sound. Two phases: an offline
asset-prep script you run once, and a runtime engine that plays those
assets live.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 1. Pre-render the binaural assets (do this first, and again if you touch
the timbre design)

```bash
python hrtf_render.py
```

Generates `sounds/` — one HRTF-rendered, loudness-normalized WAV per
(voice slot, angle) pair, every 15° from -90° to +90°, plus a separate
urgent-cue set. This is the only place that touches the KEMAR HRTF; the
runtime engine never convolves live.

## 2. Test the DSP offline (no ZMQ, no audio hardware required)

```bash
python test_mixer_smoke.py
```

Scripts a short scenario (sweep → urgent → three-at-once → silence)
through the real mixer and writes `preview.wav` so you can actually listen
to it. Asserts no NaNs/clipping and correct silence when the warning list
is empty.

## 3. Run live against a mock warning feed

```bash
python mock_warnings.py three &     # or: sweep | urgent | silence
python engine.py
```

`engine.py` connects to `tcp://localhost:5556` by default; point it at C's
real process with `--endpoint` once that's up. `--list-devices` shows
available audio output devices.

## Design notes (why it sounds the way it does)

- **Distance → tempo**, not pitch or loudness. Loudness is reserved for
  breaking the norm on the urgent cue; constant moderate-loud beeping reads
  as alarm-like, which we're deliberately avoiding (see Microsoft
  Soundscape's documented user complaints about an always-on beacon tone
  causing anxiety).
- **Tempo curve is eased and floored** (0.5m→0.1s, 1.5m→0.6s, eased so most
  differentiation happens close-in, never goes below ~0.1s) — tempo should
  never feel frantic; urgency is carried by the distinct urgent cue instead.
- **Three voice slots use stacked-fifth pitches** (A3-E4-B4) so up to three
  simultaneous obstacles stay consonant instead of clashing when heard
  together.
- **Secondary distance cue**: a one-pole lowpass filter whose cutoff tracks
  distance (far = darker/muffled, near = brighter) — mimics real acoustic
  distance attenuation instead of adding another alarm-like parameter.
- **Urgent cue is a categorically different motif** (upward chirp), not
  just a faster/louder beep — it needs to read as qualitatively distinct.
- **Default is silence.** No ambient carrier tone when there's nothing to
  report.
- **Fail-safe**: if the upstream warning feed goes stale (>0.5s without a
  message), the engine stops beeping rather than repeating a possibly-wrong
  last-known position forever.

## Not yet done / next up

- Pointing test with teammates (eyes closed, point to the angle you hear) —
  the plan's fallback if any direction is confused is stronger L/R panning.
- Tune the tempo/cutoff curves by ear once on real hardware — all tuning
  knobs are constants at the top of `mixer.py` and `hrtf_render.py`.
- Latency measurement on the actual Pi with real buffer sizes.
- Waypoint/target cue (stretch, for C's camera ArUco marker) — separate
  earcon, not built yet; needs its own motif so it doesn't read as "hazard."

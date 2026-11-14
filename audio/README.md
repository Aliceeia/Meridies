# audio — spatial audio engine (Person B)

Turns C's warning list into directional sound. Two phases: an offline
asset-prep script you run once, and a runtime engine that plays those
assets live.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 1. Generate the dry tones (do this first, and again if you touch the
timbre design)

```bash
python tone_render.py
```

Generates `sounds/beep_dry.wav` and `sounds/urgent_dry.wav` — two short
mono tones, no direction or distance baked in at all. **No HRTF anymore**
(see below) — direction and distance are computed live, per obstacle, in
`mixer.py`. The tone itself is synthesized (`ev_hum_tone()`): detuned
oscillator layers plus a sub-octave layer under a smooth swell envelope,
in the BMW IconicSounds Electric / Hans Zimmer EV-hum family, with a touch
of a real glockenspiel recording blended in for shimmer. (`source_samples/`
still has the raw glockenspiel WAVs, CC0-licensed, used only for that
shimmer blend now.)

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
python mock_warnings.py &     # default 'travel': two obstacles in continuous
                                # independent az+dist motion — the direct test of
                                # whether this feels spatial. Also: sweep | urgent | three | silence
python engine.py --device "Jerry"   # substring match; omit to use the system default
```

`engine.py` connects to `tcp://localhost:5556` by default; point it at C's
real process with `--endpoint` once that's up. `--list-devices` shows
available output devices — a stream opened against one device won't follow
the system default if it changes later, so pass `--device` explicitly
rather than assuming "default" is what you think it is (this bit us once).

## Design notes (why it sounds the way it does)

- **Ditched HRTF entirely — direction and distance are now physics-based,
  live DSP, not pre-rendered convolution.** A real demo with testers
  confirmed the failure mode you'd predict from theory: a generic
  (non-personalized) HRTF only reliably conveys left/right via ITD/ILD;
  front-back and distance need broadband spectral (pinna) cues that
  neither a dummy head (KEMAR) nor a narrow tonal source actually
  provides. Testers could tell left from right and nothing else.
  Replaced with `mixer.py`'s `spatialize()`:
  - **Direction**: real ITD (Woodworth head model — time delay between
    ears, ~0-0.65ms over ±90°) + ILD (cosine pan law). Both are physical/
    geometric, not spectral, so they don't depend on matching anyone's
    individual ear shape. Verified monotonic and correctly signed across
    the full range (az=-80° → left louder, +80° → right louder, 0° →
    equal, ~11x L/R ratio at the extremes).
  - **Distance**: the two strongest real-world distance cues — overall
    loudness (near 1.0x, far 0.35x gain) and direct-to-reverberant ratio
    (a handful of decaying, lowpassed echo taps standing in for diffuse
    room reflections; near ≈5% wet, far ≈45% wet). Near sounds are almost
    all direct signal; far sounds carry proportionally more diffuse
    reflection, the way distant sounds in a real room actually feel
    "sunk into" reverb. Verified ~5.4x RMS difference between 0.5m and
    1.5m — a strong, immediately audible cue, not a subtle one.
  - Both are computed fresh per onset from the obstacle's actual az/dist —
    no more snapping to a 15° grid, continuous resolution.
  - Previously tried loudness-as-distance and rejected it (worried it'd
    read as alarm-like, worried about conflating with HRTF's own ILD) —
    both worries dissolved once HRTF was gone: there's no HRTF-ILD to
    conflate with anymore, and a demo with real testers who couldn't
    perceive distance *at all* makes a strong, obvious cue the right
    call over a subtle "soothing" one that doesn't register.
  - **Front-back ambiguity is not fixable by this (or any ITD/ILD-only)
    design** — it's a mathematical property of the cues, not a bug: a
    source at azimuth θ in front and its mirror at 180°-θ behind produce
    *identical* ITD and ILD, always. It's worst right at ±90° (directly
    beside the head), which is uncomfortably close to where S5/S6 sit
    (±80°) — real human ears have the exact same ambiguity there and
    resolve it mainly via small head movements, which this system has no
    equivalent of. Worth testing explicitly (`mock_warnings.py` can be
    extended to sweep az toward the edges) rather than assuming it's fine
    because the sensor layout "never points backward" — near ±90° the
    ambiguity shows up even for a legitimately-forward source.
  - **Performance: `spatialize()` runs inside the realtime audio
    callback** (every onset, not pre-computed), so it has to fit the
    ~5.8ms/block budget (blocksize=256 @ 44.1kHz) or the stream glitches —
    measured and hit exactly that failure the first pass: a per-sample
    Python loop in the reverb's lowpass filter measured ~7.5ms *alone*,
    and `VoiceSlot.apply_lpf` plus the onset mixing loop (both per-sample
    Python, both running every block regardless of onsets) pushed worst-
    case `render_block()` to ~8.7ms. All three replaced with vectorized
    scipy `lfilter` / numpy slicing; now averages ~0.1ms with a ~1ms p99.
    If you touch this DSP path again, re-profile `render_block()` the same
    way — a per-sample Python loop anywhere in the onset or per-block path
    will eventually blow the budget again, even if it looks harmless in
    isolation.
- **Tempo curve is eased and floored at 0.45s-1.3s** (not the plan's
  original 0.1s-0.6s sketch, which buzzed like a smoke detector in
  practice) — tempo should read as a calm, countable pulse even at the
  closest distance; urgency is carried by the urgent cue's motif, not by
  beeping faster and faster. Floor nudged up from 0.35s to 0.45s to fit
  the current, longer note (0.42s) without heavy tail-overlap at the
  fastest repeat rate.
- **One neutral timbre for every obstacle.** We tried a distinct pitch/
  timbre per voice slot (even consonant stacked fifths) and multiple
  simultaneous obstacles sounded like a chaotic little ensemble — every
  extra timbre is another thing to *identify*, competing with the job of
  *locating* it. A single shared timbre offloads all "which one is this"
  work onto pure spatial hearing (cocktail-party effect; also how human
  echolocation experts work — one click timbre, pure spatial localization).
  Each obstacle keeps its own independent tempo. Slot number only sets a
  small fixed onset-phase stagger, so obstacles that appear at once (or
  land on near-identical periods) interleave instead of firing on the
  exact same sample and perceptually fusing into one blurred location —
  verified this empirically, staggers land ~117ms apart for 3 obstacles.
  Slot assignment is hysteresis-stabilized by current distance so it stays
  decoupled from direction and from any one obstacle's identity.
- **Secondary distance cue**: a 2-stage cascaded one-pole lowpass whose
  cutoff tracks distance (far ≈300Hz, near ≈1500Hz). Retuned three times
  now — every tone redesign moved where the energy actually sits, and the
  cutoff range has to be re-measured against it each time, not assumed.
  Current range: far retains ~55% RMS, near ~79%.
- **Beep and urgent cue are a composite tone**, built in three layers:
  (1) an EV-style hum base — detuned oscillators (slow beating = the "hum"
  motion) plus a sub-octave layer, in the BMW IconicSounds Electric / Hans
  Zimmer EV-sound family (that recording itself is proprietary; this
  reproduces the technique, not the audio); (2) a touch of the real
  glockenspiel recording blended in at low, RMS-matched gain for shimmer
  and presence, without it dominating into a "ring" (a pure hum came back
  sounding flat/dead); (3) a brief upward pitch "spin-up" (~12% over the
  first 30ms, not a continuous glide) plus a dynamic highpass+lowpass
  sweep — narrow and closed at onset, widening open over the attack — for
  the "squeezing" riser motion that gives it lift instead of sounding
  static. All baked into the dry tone in `tone_render.py`, before any
  live spatialization. Beep duration is 0.42s (attack 0.035s, release 0.2s) — lengthened
  from an initial 0.24s that felt too clipped/short; durations and the
  tempo floor above need to move together if this changes again.

  Urgent cue reuses the same composite-tone machinery at a brighter,
  quicker two-note falling fourth (C4→G3) — not a rising siren-style
  sweep, since a continuous glide reads as "fire alarm" almost universally;
  a discrete falling interval reads as "this needs attention" without the
  panic quality. Loudness bump is modest (~3dB), not dramatic — the motif
  carries urgency, not volume.
- **Default is silence.** No ambient carrier tone when there's nothing to
  report.
- **Fail-safe**: if the upstream warning feed goes stale (>0.5s without a
  message), the engine stops beeping rather than repeating a possibly-wrong
  last-known position forever.

## Not yet done / next up

- Re-run the pointing test now that direction is ITD/ILD-based — should be
  more robust than generic HRTF was, but verify on real listeners/earphones.
- Tune the distance loudness/reverb curves by ear once on real hardware —
  all tuning knobs are constants at the top of `mixer.py` and `tone_render.py`.
- Latency measurement on the actual Pi with real buffer sizes.
- Waypoint/target cue (stretch, for C's camera ArUco marker) — separate
  earcon, not built yet; needs its own motif so it doesn't read as "hazard."

## On hold — guidance/navigation (V2, not in current scope)

Idea: a GPS-driven or voice-prompted ("where's my destination?") directional
cue — something like Fortnite's nearby-chest audio hum, a spatial "pull"
toward a target. Deliberately not building this now; two reasons it's
architecturally distinct from obstacle avoidance, not an extension of it:

- **It means the opposite thing.** Obstacle beeps say "don't go that way";
  a guidance cue says "go that way." Running both as continuous spatial
  streams at once risks the ear not being able to tell which meaning is
  active — this isn't a loudness/clutter problem, it's a semantic collision.
- **Continuous guidance has a documented failure mode.** Microsoft
  Soundscape's always-on beacon caused real user complaints about anxiety
  and about it drowning out ambient-sound awareness — the exact risk we're
  already careful about with obstacle beeps.

If/when this gets built: obstacle avoidance always preempts/ducks it;
guidance should be an intermittent "check-in" (periodic, or triggered by
heading-error/voice-prompt), never a continuous hum; and it needs its own
sonic category, distinct from both the obstacle beep and the urgent cue —
probably warmer/sustained rather than percussive, so it reads as
"direction" not "hazard" even heard for half a second. Conceptually the
same category as the camera waypoint/target cue above — both are
"here's a point of interest," one GPS-driven and continuous-intermittent,
one camera-triggered and one-shot — worth designing as one unified
guidance channel rather than two. (Aside: blind users have fully normal
egocentric left/right — that's proprioception, not vision — so a
directional voice-prompted cue is viable; what's harder without vision is
allocentric/map-level spatial reasoning, not left/right itself.)

"""
Offline asset generator — run this once (and again any time ANGLES or the
timbre design changes) to pre-render every binaural sample the runtime
engine plays. Nothing in here runs at demo time.

Azimuth convention (shared with sectors.json and the warning-list messages):
0 deg = straight ahead, negative = left, positive = right, as if looking
down at the wearer from above.

Usage:
    python hrtf_render.py
Writes WAVs into ./sounds/, consumed by engine.py / mixer.py.
"""
import json
from pathlib import Path

import numpy as np
import slab
import soundfile as sf

SR = 44100
ANGLES = list(range(-90, 91, 15))  # -90, -75, ..., 90 ; matches S1-S6 plus gaps
SOUNDS_DIR = Path(__file__).parent / "sounds"

# Target loudness after HRTF rendering. HRTF magnitude response varies with
# angle, so without this step side obstacles would sound quieter/louder than
# front ones for the wrong reason (filter response, not the "closer=louder"
# cue we're NOT using). -18 dBFS RMS leaves headroom and sits comfortably
# below the urgent cue's louder target, so breaking the loudness norm still
# means something when it happens.
BEEP_TARGET_DBFS = -18.0
URGENT_TARGET_DBFS = -13.0  # deliberately louder — the one norm-break we use

# Three voice "slots" = nearest / 2nd-nearest / 3rd-nearest obstacle. Pitches
# are stacked perfect fifths (A3-E4-B4) so up to three simultaneous beeps
# stay consonant instead of clashing — this is the main lever for "soothing"
# rather than "smoke detector." Each voice also gets a distinct harmonic
# recipe so slots stay distinguishable by timbre, not just pitch.
VOICE_DEFS = {
    1: dict(freq=220.00, harmonics=[1, 2, 3], gains=[1.0, 0.22, 0.06], duration=0.085),
    2: dict(freq=329.63, harmonics=[1, 2, 4], gains=[1.0, 0.30, 0.12], duration=0.085),
    3: dict(freq=493.88, harmonics=[1, 3, 5], gains=[1.0, 0.28, 0.10], duration=0.085),
}

ATTACK_S = 0.006    # soft onset, no click, no percussive snap
RELEASE_S = 0.018   # longer tail fade — reads as a mellow "tone" not a "tick"
DECAY_TAU = 0.045   # exponential decay time constant after attack

URGENT_DEF = dict(duration=0.10)  # short upward chirp, rendered separately below


def harmonic_tone(freq, harmonics, gains, duration, sr=SR):
    """Soft mallet-like tone: sum of sine harmonics under one shared envelope."""
    n = int(duration * sr)
    t = np.arange(n) / sr
    dry = np.zeros(n)
    for h, g in zip(harmonics, gains):
        dry += g * np.sin(2 * np.pi * freq * h * t)
    dry /= sum(gains)

    env = np.ones(n)
    attack_n = max(1, int(ATTACK_S * sr))
    env[:attack_n] = np.linspace(0.0, 1.0, attack_n)
    decay_mask = t >= ATTACK_S
    env[decay_mask] = np.exp(-(t[decay_mask] - ATTACK_S) / DECAY_TAU)
    release_n = max(1, int(RELEASE_S * sr))
    if release_n < n:
        fade = np.linspace(1.0, 0.0, release_n)
        env[-release_n:] *= fade
    else:
        env *= np.linspace(1.0, 0.0, n)

    return (dry * env).astype(np.float64)


def urgent_chirp(duration=URGENT_DEF["duration"], sr=SR, f0=600.0, f1=950.0):
    """Short rising chirp — categorically different motif from the base beep,
    not just a faster/louder version of it, per the "urgent must feel
    qualitatively distinct" takeaway from the ETA research."""
    n = int(duration * sr)
    t = np.arange(n) / sr
    freq_t = f0 + (f1 - f0) * (t / duration)
    phase = 2 * np.pi * np.cumsum(freq_t) / sr
    dry = np.sin(phase)

    env = np.ones(n)
    attack_n = max(1, int(0.004 * sr))
    env[:attack_n] = np.linspace(0.0, 1.0, attack_n)
    release_n = max(1, int(0.015 * sr))
    env[-release_n:] *= np.linspace(1.0, 0.0, release_n)
    return (dry * env).astype(np.float64)


def nearest_kemar_source(hrtf, az_deg, el_deg=0.0):
    """slab's KEMAR azimuths run 0-360 and increase toward the LEFT (verified
    empirically — source az=15 is louder in the left channel). We flip sign
    to match our right-positive convention before matching to the nearest
    measured source at (roughly) ear height."""
    vp = np.asarray(hrtf.sources.vertical_polar)
    kemar_az = (-az_deg) % 360
    el_mask = np.abs(vp[:, 1] - el_deg) < 1.0
    candidates = np.where(el_mask)[0]
    diffs = np.abs(((vp[candidates, 0] - kemar_az + 180) % 360) - 180)
    return int(candidates[np.argmin(diffs)])


def normalize_rms(stereo, target_dbfs):
    target_lin = 10 ** (target_dbfs / 20.0)
    rms = np.sqrt(np.mean(stereo ** 2))
    if rms < 1e-9:
        return stereo
    return stereo * (target_lin / rms)


def render_and_save(hrtf, dry_mono, az_deg, out_path, target_dbfs):
    sound = slab.Sound(dry_mono, samplerate=SR)
    source_idx = nearest_kemar_source(hrtf, az_deg)
    binaural = hrtf.apply(source_idx, sound)
    stereo = np.asarray(binaural.data, dtype=np.float64)
    stereo = normalize_rms(stereo, target_dbfs)
    peak = np.max(np.abs(stereo))
    if peak > 0.98:
        stereo *= 0.98 / peak  # hard safety clamp, shouldn't normally trigger
    sf.write(str(out_path), stereo.astype(np.float32), SR)


def main():
    SOUNDS_DIR.mkdir(exist_ok=True)
    hrtf = slab.HRTF.kemar()
    print(f"Loaded KEMAR HRTF: {hrtf.n_sources} sources, {hrtf.samplerate} Hz")

    manifest = {"samplerate": SR, "angles": ANGLES, "voices": {}, "urgent": {}}

    for voice_id, params in VOICE_DEFS.items():
        dry = harmonic_tone(params["freq"], params["harmonics"], params["gains"], params["duration"])
        for az in ANGLES:
            out_path = SOUNDS_DIR / f"voice{voice_id}_az{az:+04d}.wav"
            render_and_save(hrtf, dry, az, out_path, BEEP_TARGET_DBFS)
        manifest["voices"][voice_id] = {
            "freq": params["freq"], "duration": params["duration"],
            "files": [f"voice{voice_id}_az{az:+04d}.wav" for az in ANGLES],
        }
        print(f"voice {voice_id}: rendered {len(ANGLES)} angles")

    dry_urgent = urgent_chirp()
    for az in ANGLES:
        out_path = SOUNDS_DIR / f"urgent_az{az:+04d}.wav"
        render_and_save(hrtf, dry_urgent, az, out_path, URGENT_TARGET_DBFS)
    manifest["urgent"] = {"files": [f"urgent_az{az:+04d}.wav" for az in ANGLES]}
    print(f"urgent cue: rendered {len(ANGLES)} angles")

    with open(SOUNDS_DIR / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Done. Assets in {SOUNDS_DIR}")


if __name__ == "__main__":
    main()

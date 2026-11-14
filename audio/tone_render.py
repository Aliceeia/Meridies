"""
Offline tone generator — run once (and again if the timbre design changes)
to synthesize the dry mono source tones the runtime engine spatializes live.

Ditched HRTF entirely (see mixer.py's spatialize()): a generic, non-
personalized HRTF (KEMAR) only reliably conveys left/right — front-back and
distance need broadband spectral (pinna) cues that neither a dummy head nor
a narrow tonal source actually provides. Direction and distance are now
handled live, per obstacle, in mixer.py using physics-based ITD/ILD panning
and distance-based loudness/reverb — robust cues that don't depend on
spectral individualization at all. This file only produces the raw dry
tones; nothing in here is angle- or distance-aware.

Usage:
    python tone_render.py
Writes beep_dry.wav / urgent_dry.wav into ./sounds/, consumed by mixer.py.
"""
import json
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 44100
SOUNDS_DIR = Path(__file__).parent / "sounds"
SAMPLES_DIR = Path(__file__).parent / "source_samples"

# Loudness baseline between the two dry tones before any live distance/
# direction processing — keeps the urgent cue's intentional "norm-break"
# bump meaningful regardless of how mixer.py's distance-loudness is tuned.
BEEP_TARGET_DBFS = -18.0
URGENT_TARGET_DBFS = -15.0

# ONE neutral timbre for every "warn"-level obstacle — see mixer.py's
# single-shared-timbre rationale (cocktail-party effect / echolocation
# precedent: direction and distance should carry identity, not timbre).
#
# Composite design layers three things: (1) a synthesized EV-style hum base
# (detuned oscillators + sub, in the BMW IconicSounds Electric / Hans
# Zimmer vein — that recording itself is proprietary, this reproduces the
# technique, not the audio), (2) a touch of the real glockenspiel recording
# blended in at low gain for shimmer/presence, and (3) a brief upward pitch
# "spin-up" plus a dynamic highpass+lowpass sweep — narrow-and-closed at
# onset, widening open over the attack — for a "squeezing" riser motion.
BEEP_FREQ = 196.00    # G3
URGENT_FREQS = (261.63, 196.00)  # C4 -> G3, falling fourth, same family, brighter/quicker
SHIMMER_GAIN = 0.28
SHIMMER_NOTE_FOR = {196.00: "G4", 261.63: "C5"}  # nearest available glock sample per base freq


def cents_to_ratio(cents):
    return 2 ** (cents / 1200.0)


def load_shimmer(note, n, sr=SR):
    """Trim a raw VSCO2 glockenspiel recording to `n` samples with its own
    short fade — this is blended in at low gain, not the main tone."""
    raw, file_sr = sf.read(str(SAMPLES_DIR / f"glock_medium_{note}.wav"), dtype="float64")
    assert file_sr == sr
    mono = raw.mean(axis=1) if raw.ndim > 1 else raw
    shimmer = mono[:n].copy()
    fade_n = min(n, max(1, int(0.08 * sr)))
    shimmer[-fade_n:] *= np.linspace(1.0, 0.0, fade_n)
    return shimmer


def time_varying_onepole_lowpass(x, cutoff_env, sr):
    alpha = np.exp(-2 * np.pi * np.asarray(cutoff_env) / sr)
    y = np.empty_like(x)
    state = 0.0
    for i in range(len(x)):
        state = alpha[i] * state + (1 - alpha[i]) * x[i]
        y[i] = state
    return y


def ev_hum_tone(base_freq, duration, attack, release, detune_cents=5.0, sub_gain=0.6,
                glide_depth=0.12, glide_s=0.03, shimmer_gain=SHIMMER_GAIN, sr=SR):
    """EV-style hum base with a brief upward pitch spin-up, a dynamic
    highpass+lowpass "squeeze then open" filter sweep, and a blended-in
    glockenspiel shimmer layer for presence without reading as a ring."""
    n = int(duration * sr)
    t = np.arange(n) / sr

    glide_n = max(1, int(glide_s * sr))
    ratio = np.ones(n)
    ratio[:glide_n] = (1 - glide_depth) + glide_depth * np.linspace(0.0, 1.0, glide_n)
    detunes = (-detune_cents, 0.0, detune_cents)

    dry = np.zeros(n)
    for d in detunes:
        f = base_freq * cents_to_ratio(d)
        inst_freq = f * ratio
        phase = 2 * np.pi * np.cumsum(inst_freq) / sr
        dry += np.sin(phase)
    sub_phase = 2 * np.pi * np.cumsum((base_freq / 2) * ratio) / sr
    dry += sub_gain * np.sin(sub_phase)
    up_phase = 2 * np.pi * np.cumsum((base_freq * 2) * ratio) / sr
    dry += 0.22 * np.sin(up_phase)
    dry /= (len(detunes) + sub_gain + 0.22)

    shimmer_note = SHIMMER_NOTE_FOR.get(base_freq)
    if shimmer_note and shimmer_gain > 0:
        shimmer = load_shimmer(shimmer_note, n, sr)
        hum_rms = np.sqrt(np.mean(dry ** 2))
        shimmer_rms = np.sqrt(np.mean(shimmer ** 2))
        if shimmer_rms > 1e-9:
            shimmer *= hum_rms / shimmer_rms
        dry = dry + shimmer_gain * shimmer

    sweep_n = max(glide_n, int((attack + 0.04) * sr))
    sweep_n = min(sweep_n, n)
    hp_cutoff = np.full(n, 80.0)
    hp_cutoff[:sweep_n] = np.linspace(600.0, 80.0, sweep_n)
    lp_cutoff = np.full(n, 2600.0)
    lp_cutoff[:sweep_n] = np.linspace(500.0, 2600.0, sweep_n)

    low = time_varying_onepole_lowpass(dry, lp_cutoff, sr)
    hp_low = time_varying_onepole_lowpass(dry, hp_cutoff, sr)
    dry = low - hp_low

    attack_n = max(1, int(attack * sr))
    release_n = max(1, int(release * sr))
    env = np.ones(n)
    env[:attack_n] = 0.5 - 0.5 * np.cos(np.pi * np.linspace(0.0, 1.0, attack_n))
    sustain = slice(attack_n, n - release_n)
    if sustain.stop > sustain.start:
        decay_tau = duration * 0.7
        env[sustain] *= np.exp(-(t[sustain] - t[attack_n]) / decay_tau)
    env[-release_n:] *= 0.5 + 0.5 * np.cos(np.pi * np.linspace(0.0, 1.0, release_n))

    return (dry * env).astype(np.float64)


def beep_tone(sr=SR):
    return ev_hum_tone(BEEP_FREQ, duration=0.42, attack=0.035, release=0.2, sr=sr)


def urgent_tone(sr=SR):
    """Categorically different from the base beep — a brighter, quicker
    two-note falling motif in the same hum vocabulary, not a rising
    siren-style frequency sweep."""
    note1 = ev_hum_tone(URGENT_FREQS[0], duration=0.26, attack=0.02, release=0.11, detune_cents=7.0, sr=sr)
    note2 = ev_hum_tone(URGENT_FREQS[1], duration=0.36, attack=0.025, release=0.17, detune_cents=7.0, sr=sr)
    gap = np.zeros(int(0.03 * sr))
    return np.concatenate([note1, gap, note2])


def normalize_rms(mono, target_dbfs):
    target_lin = 10 ** (target_dbfs / 20.0)
    rms = np.sqrt(np.mean(mono ** 2))
    if rms < 1e-9:
        return mono
    return mono * (target_lin / rms)


def main():
    SOUNDS_DIR.mkdir(exist_ok=True)

    beep = normalize_rms(beep_tone(), BEEP_TARGET_DBFS)
    urgent = normalize_rms(urgent_tone(), URGENT_TARGET_DBFS)

    for arr, name in ((beep, "beep_dry.wav"), (urgent, "urgent_dry.wav")):
        peak = np.max(np.abs(arr))
        if peak > 0.98:
            arr = arr * (0.98 / peak)
        sf.write(str(SOUNDS_DIR / name), arr.astype(np.float32), SR)

    with open(SOUNDS_DIR / "manifest.json", "w") as f:
        json.dump({"samplerate": SR, "beep_file": "beep_dry.wav", "urgent_file": "urgent_dry.wav"}, f, indent=2)
    print(f"Done. Dry tones in {SOUNDS_DIR}")


if __name__ == "__main__":
    main()

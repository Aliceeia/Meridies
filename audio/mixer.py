"""
Pure DSP core: turns a warning list into audio blocks. No ZMQ, no
sounddevice, no I/O — this is the part that's actually worth unit-testing,
and engine.py is just plumbing around it.

Warning list format (from C, port 5556, ~20 Hz):
    [{"id": "S2", "az": 15, "dist": 0.64, "level": "warn"},
     {"id": "S5", "az": -80, "dist": 0.42, "level": "urgent"}]
At most 3 entries, nearest first, empty list = silence.
"""
import json
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import lfilter

SOUNDS_DIR = Path(__file__).parent / "sounds"
NUM_SLOTS = 3

# Distance -> inter-onset period mapping. Deliberately much slower than the
# plan's original 0.1s-0.6s sketch — that read as a frantic buzz in practice.
# Floored well above "trill" territory: even at the closest/urgent distance,
# this should feel like a calm, readable pulse, not an alarm. Floor nudged
# up from 0.35s to 0.45s to comfortably fit the longer tone (tone_render.py
# beep is now 0.42s) without heavy tail-overlap at the fastest repeat rate.
DIST_NEAR, DIST_FAR = 0.5, 1.5
PERIOD_NEAR, PERIOD_FAR = 0.45, 1.3
EASE_EXP = 1.6

# Secondary "soothing" cue: a one-pole lowpass whose cutoff tracks distance,
# so near obstacles sound a little fuller/more present and far ones sound
# a little warmer/more muffled — mimics real acoustic distance attenuation
# instead of adding another alarm-like parameter. Re-measured again against
# the current composite tone (hum + shimmer + its own baked-in filter
# sweep) — its spectral balance shifts every time the tone design changes,
# so this always needs re-checking, not assuming. Current range: far
# retains ~55% RMS, near ~79% (~1.44x ratio) — audible without gutting far
# obstacles' loudness the way an untuned guess did earlier.
LPF_CUTOFF_FAR_HZ = 300.0
LPF_CUTOFF_NEAR_HZ = 1500.0


def dist_to_period(dist):
    d = min(max(dist, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)  # 0 at near, 1 at far
    eased = t ** EASE_EXP
    return PERIOD_NEAR + eased * (PERIOD_FAR - PERIOD_NEAR)


def dist_to_cutoff(dist):
    d = min(max(dist, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)
    return LPF_CUTOFF_NEAR_HZ + t * (LPF_CUTOFF_FAR_HZ - LPF_CUTOFF_NEAR_HZ)


class SoundBank:
    """Loads the two dry mono tones once; engine.py builds one of these at
    startup and hands it to Mixer. No per-angle variants anymore — all
    direction/distance processing happens live in spatialize()."""

    def __init__(self, sounds_dir=SOUNDS_DIR):
        manifest_path = Path(sounds_dir) / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"{manifest_path} missing — run tone_render.py first to generate audio assets."
            )
        with open(manifest_path) as f:
            manifest = json.load(f)
        self.samplerate = manifest["samplerate"]

        beep, sr = sf.read(str(Path(sounds_dir) / manifest["beep_file"]), dtype="float64")
        assert sr == self.samplerate
        self.beep_dry = beep.mean(axis=1) if beep.ndim > 1 else beep

        urgent, sr = sf.read(str(Path(sounds_dir) / manifest["urgent_file"]), dtype="float64")
        assert sr == self.samplerate
        self.urgent_dry = urgent.mean(axis=1) if urgent.ndim > 1 else urgent


# --- Live spatialization: ITD/ILD direction + distance loudness/reverb ---
#
# Ditched HRTF entirely. A generic (non-personalized) HRTF only reliably
# conveys left/right in practice — front-back and distance rely on
# broadband spectral (pinna) cues that neither a dummy head nor a narrow
# tonal source provides, and a demo with real testers confirmed exactly
# that: left/right came through, nothing else did. Direction now comes
# from the two physics-based cues that don't depend on any spectral
# individualization at all — interaural time difference (ITD, via the
# Woodworth head model) and interaural level difference (ILD, a cosine pan
# law) — and distance comes from the two strongest real-world distance
# cues: overall loudness, and direct-to-reverberant energy ratio (near
# sounds are almost all direct signal; far sounds carry proportionally more
# diffuse reflection, the way a real room makes distant sounds feel "sunk
# into" reverb). This also means azimuth and distance are no longer
# snapped to a pre-rendered grid — both are continuous now.
HEAD_RADIUS_M = 0.0875
SPEED_OF_SOUND_MPS = 343.0

LOUDNESS_NEAR_GAIN, LOUDNESS_FAR_GAIN = 1.0, 0.35
REVERB_WET_NEAR, REVERB_WET_FAR = 0.05, 0.45
# a handful of decaying, lowpassed echo taps standing in for diffuse room
# reflections — not a pristine convolution reverb, just enough to give the
# direct-to-reverberant ratio something real to vary
REVERB_TAPS = [(0.018, 0.35), (0.031, 0.25), (0.047, 0.18), (0.065, 0.12), (0.085, 0.08)]
REVERB_SMOOTH_HZ = 3500.0


def dist_to_loudness(dist):
    d = min(max(dist, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)
    return LOUDNESS_NEAR_GAIN + t * (LOUDNESS_FAR_GAIN - LOUDNESS_NEAR_GAIN)


def dist_to_wet(dist):
    d = min(max(dist, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)
    return REVERB_WET_NEAR + t * (REVERB_WET_FAR - REVERB_WET_NEAR)


def _onepole_lowpass_scalar(x, cutoff_hz, sr):
    """y[n] = alpha*y[n-1] + (1-alpha)*x[n], via scipy's C-level lfilter
    instead of a per-sample Python loop. That loop measured at ~7.5ms for a
    single spatialize() call — spatialize() runs synchronously inside the
    realtime audio callback (5.8ms budget at blocksize=256), so it was
    reliably blowing the deadline and producing audible dropouts/glitches
    every time a voice triggered a new onset. lfilter does the same math
    in well under a millisecond."""
    alpha = np.exp(-2 * np.pi * cutoff_hz / sr)
    return lfilter([1 - alpha], [1, -alpha], x)


def _reverb_tail(dry_mono, sr):
    """Reflections only (no direct copy) — caller mixes direct + this by `wet`."""
    tail_s = REVERB_TAPS[-1][0] + 0.05
    n_tail = int(tail_s * sr)
    tail = np.zeros(len(dry_mono) + n_tail)
    for delay_s, gain in REVERB_TAPS:
        d = int(delay_s * sr)
        n = min(len(dry_mono), len(tail) - d)
        tail[d:d + n] += gain * dry_mono[:n]
    return _onepole_lowpass_scalar(tail, REVERB_SMOOTH_HZ, sr)


def spatialize(dry_mono, az_deg, dist_m, sr):
    """Dry mono tone -> stereo, positioned by real ITD/ILD and distance-
    loudness/reverb. Called once per onset, from the obstacle's current
    az/dist — continuous resolution, no angle snapping."""
    az = max(-90.0, min(90.0, az_deg))
    theta = np.radians(abs(az))
    itd_s = (HEAD_RADIUS_M / SPEED_OF_SOUND_MPS) * (theta + np.sin(theta))  # Woodworth model
    itd_samples = int(round(itd_s * sr))

    norm = (az + 90.0) / 180.0  # 0 at full left, 1 at full right
    gain_left = np.cos(norm * np.pi / 2)
    gain_right = np.sin(norm * np.pi / 2)

    loud = dist_to_loudness(dist_m)
    wet = dist_to_wet(dist_m)

    reflections = _reverb_tail(dry_mono, sr) if wet > 1e-4 else np.zeros(0)
    n = max(len(dry_mono), len(reflections))
    direct = np.zeros(n)
    direct[:len(dry_mono)] = dry_mono
    refl = np.zeros(n)
    refl[:len(reflections)] = reflections
    mono_mix = (direct * (1 - wet) + refl * wet) * loud

    n_out = n + itd_samples
    left = np.zeros(n_out)
    right = np.zeros(n_out)
    if az >= 0:  # source on the right -> sound reaches the left ear later
        left[itd_samples:itd_samples + n] = mono_mix * gain_left
        right[:n] = mono_mix * gain_right
    else:
        right[itd_samples:itd_samples + n] = mono_mix * gain_right
        left[:n] = mono_mix * gain_left

    return np.stack([left, right], axis=1).astype(np.float32)


class VoiceSlot:
    """One of up to 3 concurrently-active obstacles. All slots share the
    same timbre now — slot_num only sets a small fixed onset-phase offset,
    so that if two obstacles appear in the same control update (or end up
    with near-identical periods) their beeps interleave instead of landing
    on the exact same sample and perceptually fusing into one blurred
    location (the precedence effect). Scheduling itself is sample-accurate
    (absolute sample counters), not wall-clock, so tempo stays steady
    regardless of control-loop jitter."""

    def __init__(self, slot_num):
        self.slot_num = slot_num  # 1, 2, or 3 -> only affects onset stagger
        self.active = False
        self.obstacle_id = None
        self.az = 0.0
        self.dist = DIST_FAR
        self.urgent = False
        self.period_samples = None
        self.next_onset_sample = 0
        self.playing = []  # list of [buffer, playhead] for overlapping bursts
        # two cascaded one-pole stages = ~12dB/oct rolloff, steep enough to
        # actually hear over an ~85ms burst (a single stage was too gentle).
        # zi = scipy lfilter's carried-over filter state between blocks,
        # shape (order, channels) = (1, 2) for a first-order stereo filter.
        self.lpf_zi1 = np.zeros((1, 2))
        self.lpf_zi2 = np.zeros((1, 2))
        self.lpf_alpha = 0.0

    def assign(self, obstacle, samplerate, now_sample):
        new_id = obstacle["id"]
        if new_id != self.obstacle_id:
            # new obstacle in this slot: retrigger promptly so the user
            # hears the change right away, but offset by a small fixed
            # per-slot stagger (capped, not scaled to this obstacle's own
            # period) so simultaneously-appearing obstacles don't all fire
            # their first beep on the exact same sample.
            stagger_s = (self.slot_num - 1) / NUM_SLOTS * PERIOD_NEAR
            self.next_onset_sample = now_sample + int(stagger_s * samplerate)
        self.obstacle_id = new_id
        self.az = obstacle["az"]
        self.dist = obstacle["dist"]
        self.urgent = obstacle.get("level") == "urgent"
        self.period_samples = max(1, int(dist_to_period(obstacle["dist"]) * samplerate))
        cutoff = dist_to_cutoff(obstacle["dist"])
        self.lpf_alpha = float(np.exp(-2 * np.pi * cutoff / samplerate))
        self.active = True

    def release(self):
        # let in-flight bursts finish naturally (they already fade to zero);
        # just stop scheduling new onsets
        self.active = False
        self.obstacle_id = None

    def apply_lpf(self, block):
        """Same cascaded one-pole lowpass as before, but via scipy's
        vectorized lfilter (with carried-over `zi` state) instead of a
        per-sample Python loop — this ran once per active slot, every
        single block regardless of onsets, and was part of what was
        blowing the realtime audio callback's time budget."""
        alpha = self.lpf_alpha
        b, a = [1 - alpha], [1, -alpha]
        stage1, self.lpf_zi1 = lfilter(b, a, block, axis=0, zi=self.lpf_zi1)
        stage2, self.lpf_zi2 = lfilter(b, a, stage1, axis=0, zi=self.lpf_zi2)
        return stage2


# If a lower-ranked obstacle isn't at least this much closer than the one
# above it, keep the previous ordering rather than swapping voice timbres —
# prevents two near-tied obstacles from flicker-swapping which voice they're
# heard as every ~50ms.
RANK_HYSTERESIS_M = 0.08


class Mixer:
    def __init__(self, sound_bank, samplerate=None):
        self.bank = sound_bank
        self.sr = samplerate or sound_bank.samplerate
        self.slots = [VoiceSlot(i + 1) for i in range(NUM_SLOTS)]
        self.sample_counter = 0
        self._last_order = []  # list of obstacle ids, slot-index order

    def update_warnings(self, warnings):
        """Call this whenever a new warning-list message arrives (~20 Hz).
        Slot N always carries the timbre of the Nth-nearest obstacle
        *right now* (hysteresis-stabilized) — timbre tracks proximity rank,
        never direction and never a specific obstacle's identity forever."""
        by_id = {w["id"]: w for w in warnings[:NUM_SLOTS] if "id" in w}
        ids = list(by_id.keys())

        if self._last_order and set(self._last_order) == set(ids):
            order = [i for i in self._last_order]
            changed = True
            while changed:
                changed = False
                for i in range(len(order) - 1):
                    a, b = order[i], order[i + 1]
                    if by_id[b]["dist"] + RANK_HYSTERESIS_M < by_id[a]["dist"]:
                        order[i], order[i + 1] = b, a
                        changed = True
        else:
            order = sorted(ids, key=lambda i: by_id[i]["dist"])

        self._last_order = order

        for slot, obstacle_id in zip(self.slots, order):
            slot.assign(by_id[obstacle_id], self.sr, self.sample_counter)
        for slot in self.slots[len(order):]:
            if slot.active:
                slot.release()

    def render_block(self, n_frames):
        out = np.zeros((n_frames, 2), dtype=np.float32)
        block_end = self.sample_counter + n_frames

        for slot in self.slots:
            if slot.active and slot.period_samples:
                while slot.next_onset_sample < block_end:
                    onset = slot.next_onset_sample
                    offset_in_block = max(0, onset - self.sample_counter)
                    buf = spatialize(self.bank.beep_dry, slot.az, slot.dist, self.sr)
                    if slot.urgent:
                        urgent_buf = spatialize(self.bank.urgent_dry, slot.az, slot.dist, self.sr)
                        n = max(len(buf), len(urgent_buf))
                        mixed = np.zeros((n, 2), dtype=np.float32)
                        mixed[: len(buf)] += buf
                        mixed[: len(urgent_buf)] += urgent_buf
                        buf = mixed
                    slot.playing.append([buf, -offset_in_block if offset_in_block else 0])
                    slot.next_onset_sample += slot.period_samples

            # Vectorized overlap-add instead of a per-sample Python loop
            # (this ran every block regardless of onsets — together with
            # the two loops above, it was part of what blew the realtime
            # callback's ~5.8ms budget and caused audible dropouts).
            still_playing = []
            voice_contribution = np.zeros((n_frames, 2), dtype=np.float32)
            for buf, playhead in slot.playing:
                i_start = max(0, -playhead)
                i_end = min(n_frames, len(buf) - playhead)
                if i_end > i_start:
                    voice_contribution[i_start:i_end] += buf[playhead + i_start: playhead + i_end]
                new_head = playhead + n_frames
                if new_head < len(buf):
                    still_playing.append([buf, new_head])
            slot.playing = still_playing

            if slot.lpf_alpha > 0:
                voice_contribution = slot.apply_lpf(voice_contribution)
            out += voice_contribution

        self.sample_counter = block_end
        peak = np.max(np.abs(out))
        if peak > 0.98:
            out *= 0.98 / peak  # simple safety limiter across overlapping voices
        return out

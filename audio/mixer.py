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
import time
from pathlib import Path

import numpy as np
import soundfile as sf

SOUNDS_DIR = Path(__file__).parent / "sounds"
NUM_SLOTS = 3

# Distance -> inter-onset period mapping. Anchored at the plan's own
# numbers (0.5m -> 0.1s, 1.5m -> 0.6s) but eased so most of the
# differentiation happens close-in, and floored so tempo never goes
# "frantic" even well inside 0.5m — the urgent cue carries that signal
# instead of an ever-faster beep.
DIST_NEAR, DIST_FAR = 0.5, 1.5
PERIOD_NEAR, PERIOD_FAR = 0.10, 0.60
EASE_EXP = 1.6

# Secondary "soothing" cue: a one-pole lowpass whose cutoff tracks distance,
# so near obstacles sound a little brighter/more present and far ones sound
# a little more muffled/natural — mimics real acoustic distance attenuation
# instead of adding another alarm-like parameter.
LPF_CUTOFF_FAR_HZ = 2200.0
LPF_CUTOFF_NEAR_HZ = 9000.0


def dist_to_period(dist):
    d = min(max(dist, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)  # 0 at near, 1 at far
    eased = t ** EASE_EXP
    return PERIOD_NEAR + eased * (PERIOD_FAR - PERIOD_NEAR)


def dist_to_cutoff(dist):
    d = min(max(dist, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)
    return LPF_CUTOFF_NEAR_HZ + t * (LPF_CUTOFF_FAR_HZ - LPF_CUTOFF_NEAR_HZ)


def nearest_angle(az, angles):
    return min(angles, key=lambda a: abs(a - az))


class SoundBank:
    """Loads every pre-rendered WAV once; engine.py should build one of
    these at startup and hand it to Mixer."""

    def __init__(self, sounds_dir=SOUNDS_DIR):
        manifest_path = Path(sounds_dir) / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"{manifest_path} missing — run hrtf_render.py first to generate audio assets."
            )
        with open(manifest_path) as f:
            self.manifest = json.load(f)
        self.samplerate = self.manifest["samplerate"]
        self.angles = self.manifest["angles"]

        self.voice = {}  # (voice_id, az) -> np.ndarray [n,2]
        for voice_id_str, info in self.manifest["voices"].items():
            voice_id = int(voice_id_str)
            for az, fname in zip(self.angles, info["files"]):
                data, sr = sf.read(str(Path(sounds_dir) / fname), dtype="float32")
                assert sr == self.samplerate
                self.voice[(voice_id, az)] = data

        self.urgent = {}  # az -> np.ndarray [n,2]
        for az, fname in zip(self.angles, self.manifest["urgent"]["files"]):
            data, sr = sf.read(str(Path(sounds_dir) / fname), dtype="float32")
            self.urgent[az] = data

    def beep(self, voice_id, az):
        return self.voice[(voice_id, nearest_angle(az, self.angles))]

    def urgent_cue(self, az):
        return self.urgent[nearest_angle(az, self.angles)]


class VoiceSlot:
    """One of up to 3 concurrently-active obstacle voices. Scheduling is
    sample-accurate (absolute sample counters), not wall-clock, so tempo
    stays steady regardless of control-loop jitter."""

    def __init__(self, slot_num):
        self.slot_num = slot_num  # 1, 2, or 3 -> picks the timbre
        self.active = False
        self.obstacle_id = None
        self.az = 0.0
        self.urgent = False
        self.period_samples = None
        self.next_onset_sample = 0
        self.playing = []  # list of [buffer, playhead] for overlapping bursts
        self.lpf_state = np.zeros(2, dtype=np.float32)
        self.lpf_alpha = 0.0

    def assign(self, obstacle, samplerate, now_sample):
        new_id = obstacle["id"]
        if new_id != self.obstacle_id:
            # new obstacle in this slot: retrigger immediately so the user
            # hears the change right away instead of waiting out the old period
            self.next_onset_sample = now_sample
        self.obstacle_id = new_id
        self.az = obstacle["az"]
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
        alpha = self.lpf_alpha
        out = np.empty_like(block)
        state = self.lpf_state.copy()
        for i in range(block.shape[0]):
            state = alpha * state + (1 - alpha) * block[i]
            out[i] = state
        self.lpf_state = state
        return out


class Mixer:
    def __init__(self, sound_bank, samplerate=None):
        self.bank = sound_bank
        self.sr = samplerate or sound_bank.samplerate
        self.slots = [VoiceSlot(i + 1) for i in range(NUM_SLOTS)]
        self.sample_counter = 0

    def update_warnings(self, warnings):
        """Call this whenever a new warning-list message arrives (~20 Hz).
        Sticky slot assignment: an obstacle keeps its slot across frames
        while present, so its timbre/voice doesn't jump around."""
        warnings = warnings[:NUM_SLOTS]
        ids_present = {w["id"] for w in warnings}

        # free slots whose obstacle disappeared
        for slot in self.slots:
            if slot.active and slot.obstacle_id not in ids_present:
                slot.release()

        # keep existing id->slot pairing where possible
        remaining = list(warnings)
        for slot in self.slots:
            if slot.active and slot.obstacle_id in ids_present:
                match = next((w for w in remaining if w["id"] == slot.obstacle_id), None)
                if match:
                    slot.assign(match, self.sr, self.sample_counter)
                    remaining.remove(match)

        free_slots = [s for s in self.slots if not s.active]
        for slot, w in zip(free_slots, remaining):
            slot.assign(w, self.sr, self.sample_counter)

    def render_block(self, n_frames):
        out = np.zeros((n_frames, 2), dtype=np.float32)
        block_end = self.sample_counter + n_frames

        for slot in self.slots:
            if slot.active and slot.period_samples:
                while slot.next_onset_sample < block_end:
                    onset = slot.next_onset_sample
                    offset_in_block = max(0, onset - self.sample_counter)
                    buf = self.bank.beep(slot.slot_num, slot.az)
                    if slot.urgent:
                        urgent_buf = self.bank.urgent_cue(slot.az)
                        n = max(len(buf), len(urgent_buf))
                        mixed = np.zeros((n, 2), dtype=np.float32)
                        mixed[: len(buf)] += buf
                        mixed[: len(urgent_buf)] += urgent_buf
                        buf = mixed
                    slot.playing.append([buf, -offset_in_block if offset_in_block else 0])
                    slot.next_onset_sample += slot.period_samples

            still_playing = []
            voice_contribution = np.zeros((n_frames, 2), dtype=np.float32)
            for buf, playhead in slot.playing:
                for i in range(n_frames):
                    idx = playhead + i
                    if 0 <= idx < len(buf):
                        voice_contribution[i] += buf[idx]
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

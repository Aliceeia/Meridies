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
from scipy.signal import butter, fftconvolve, lfilter, sosfilt

SOUNDS_DIR = Path(__file__).parent / "sounds"
NUM_SLOTS = 3

# Distance -> inter-onset period mapping. Deliberately much slower than the
# plan's original 0.1s-0.6s sketch — that read as a frantic buzz in practice.
# Floored well above "trill" territory: even at the closest/urgent distance,
# this should feel like a calm, readable pulse, not an alarm. Floor nudged
# up from 0.35s to 0.45s to comfortably fit the longer tone (tone_render.py
# beep is now 0.42s) without heavy tail-overlap at the fastest repeat rate.
DIST_NEAR, DIST_FAR = 0.5, 1.5
# Both ends slowed to 2/3 of the earlier beep rate (period x1.5, was 0.45 / 1.3 s).
PERIOD_NEAR, PERIOD_FAR = 0.675, 1.95
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


def _distance_mix(dry_mono, dist_m, sr, wet=None):
    """Distance cues shared by both spatializers: loudness + direct/reverb ratio (mono).
    `wet` overrides the distance-derived reverb amount (used by the direction cues)."""
    loud = dist_to_loudness(dist_m)
    wet = dist_to_wet(dist_m) if wet is None else wet
    reflections = _reverb_tail(dry_mono, sr) if wet > 1e-4 else np.zeros(0)
    n = max(len(dry_mono), len(reflections))
    direct = np.zeros(n)
    direct[:len(dry_mono)] = dry_mono
    refl = np.zeros(n)
    refl[:len(reflections)] = reflections
    return (direct * (1 - wet) + refl * wet) * loud


# --- Direction via slab's KEMAR HRTF (default) ---
#
# Direction now comes from a measured head-related transfer function
# (slab.HRTF.kemar(), 710 directions, 512-tap IRs at 44.1 kHz) instead of
# the ITD/ILD model below. The HRIRs carry ITD and ILD too, plus the
# spectral cues for front/back and elevation, so azimuth is no longer
# clamped to +/-90 (rear obstacles from the cap's side sensors play from
# behind). Distance handling, the tones, tempo and the lowpass are
# unchanged. The IRs are loaded once; per onset we pick the nearest
# measured direction and FFT-convolve (~1 ms), which fits the callback.
# Set SPATIALIZER = "itd_ild" to go back to the previous model.
SPATIALIZER = "hrtf"
HRTF_EL_MIN, HRTF_EL_MAX = -40.0, 90.0   # KEMAR has no data below -40 deg


class _Kemar:
    def __init__(self):
        import slab  # only needed for the HRTF spatializer
        h = slab.HRTF.kemar()
        self.sr = int(h.samplerate)
        vp = np.asarray(h.sources.vertical_polar, dtype=float)
        # slab/KEMAR azimuth grows to the LEFT; store unit vectors in our
        # head frame (x forward, y right, z up) for nearest-direction lookup
        az = np.radians(-vp[:, 0])
        el = np.radians(vp[:, 1])
        self.dirs = np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=1)
        self.irs = np.stack([np.asarray(f.data, dtype=float) for f in h.data])  # (n, taps, 2)
        self.level_matched = False

    def match_level(self, beep_dry, sr):
        """One global gain so the beep straight ahead is as loud as it was with
        the ITD/ILD model (KEMAR's response is weaker at the tone's low
        frequencies). Relative left/right/front/back levels stay as measured."""
        if self.level_matched:
            return
        ref = spatialize_itd_ild(beep_dry, 0.0, 1.0, sr)
        ir = self.ir_for(0.0, 0.0)
        mono = _distance_mix(beep_dry, 1.0, sr)
        new = np.stack([fftconvolve(mono, ir[:, 0]), fftconvolve(mono, ir[:, 1])], axis=1)
        self.irs *= np.sqrt((ref ** 2).mean() / (new ** 2).mean())
        self.level_matched = True

    def idx_for(self, az_deg, el_deg):
        az = np.radians(az_deg)
        el = np.radians(min(max(el_deg, HRTF_EL_MIN), HRTF_EL_MAX))
        v = np.array([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)])
        return int(np.argmax(self.dirs @ v))

    def ir_for(self, az_deg, el_deg):
        return self.irs[self.idx_for(az_deg, el_deg)]


_KEMAR = None


def _kemar():
    global _KEMAR
    if _KEMAR is None:
        _KEMAR = _Kemar()
    return _KEMAR


# --- Extra direction cues on top of the HRTF ---
#
# Why: the beep keeps ~96% of its energy below 250 Hz. Down there the head
# casts almost no acoustic shadow (tiny ILD) and the pinna adds no
# front/back/elevation colouring, so the HRTF alone has little to work
# with. These cues add direction information without changing the tone
# (a broadband onset "tick" and Blauert directional bands on it were tried
# and removed; see git history on the sound-slab branch):
#
#   room  - Dolby Atmos binaural style "Near / Far" render modes by direction:
#           front sources are drier (Near), rear sources more reverberant (Far).
#   cutoff- the tone's distance lowpass opens up in front and closes behind
#           (front brighter, back darker).
#   ild   - extra interaural level difference, since the low tone has almost
#           none naturally. Scales with sin(az), so back-left is still left.
#
# Every cue can be switched off here (or live in slab_spatial_test.py: `cues ...`).
CUES = {"room": True, "cutoff": True, "ild": True, "hum": True}   # "hum": see HUM_* below

ROOM_FRONT_DRY = 0.5        # front: wet x (1 - 0.5)
ROOM_BACK_EXTRA = 0.30      # back: wet + 0.30
CUTOFF_FRONT_X, CUTOFF_BACK_X = 1.6, 0.55
EXTRA_ILD_DB = 10.0         # total L-R difference added at +/-90 deg
# The darker/wetter rear treatment loses ~9 dB vs front, and loudness is also
# the distance cue, so a rear obstacle would read as farther away. These
# trims (scaled by frontness/backness) bring front and back to ~equal level.
FRONT_TRIM_DB, BACK_COMP_DB = -2.0, 7.0


def front_back(az_deg):
    """(frontness, backness), each 0..1: 1 straight ahead / straight behind, 0 at the sides."""
    c = np.cos(np.radians(az_deg))
    return max(c, 0.0), max(-c, 0.0)


def _room_wet(az_deg, dist_m):
    wet = dist_to_wet(dist_m)
    if not CUES["room"]:
        return wet
    f, b = front_back(az_deg)
    return float(min(0.8, wet * (1 - ROOM_FRONT_DRY * f) + ROOM_BACK_EXTRA * b))


def cutoff_factor(az_deg):
    """Multiplier for the voice's distance-lowpass cutoff (front brighter, back darker)."""
    if not CUES["cutoff"]:
        return 1.0
    f, b = front_back(az_deg)
    return 1.0 + (CUTOFF_FRONT_X - 1.0) * f + (CUTOFF_BACK_X - 1.0) * b


def _dir_level(az_deg):
    """Front/back level trim that offsets the rear treatment's loss (see FRONT_TRIM_DB)."""
    if not (CUES["cutoff"] or CUES["room"]):
        return 1.0
    f, b = front_back(az_deg)
    return 10 ** ((FRONT_TRIM_DB * f + BACK_COMP_DB * b) / 20)


def _extra_ild(stereo, az_deg):
    stereo = stereo * _dir_level(az_deg)
    if not CUES["ild"]:
        return stereo
    s = np.sin(np.radians(az_deg))                      # + right
    gl = 10 ** (-EXTRA_ILD_DB / 2 * s / 20)
    gr = 10 ** (+EXTRA_ILD_DB / 2 * s / 20)
    norm = np.sqrt((gl * gl + gr * gr) / 2)              # keep overall power
    return stereo * np.array([gl / norm, gr / norm])


def _hrtf(mono, az_deg, el_deg):
    ir = _kemar().ir_for(az_deg, el_deg)
    return np.stack([fftconvolve(mono, ir[:, 0]), fftconvolve(mono, ir[:, 1])], axis=1)


# --- Continuous presence hum ---
#
# Every active voice also carries a quiet, continuous bed of low-passed white
# noise (no pitch), placed with the same HRTF + cues as its beeps. It says
# "something is there, about this far" all the time, between the beeps,
# without the alarm character of the tones. Level follows 1/distance
# (continuous, not stepped), the lowpass opens up as the obstacle gets closer,
# and it fades in/out when an obstacle appears/disappears. Direction changes
# crossfade over one block so a moving obstacle glides instead of clicking.
HUM_RMS_AT_REF = 0.025      # output RMS (per ear, straight ahead) at HUM_REF_DIST_M
HUM_REF_DIST_M = 0.5
HUM_MAX_GAIN = 2.0          # cap on the 1/d boost (reached at 0.25 m)
HUM_CUTOFF_NEAR_HZ, HUM_CUTOFF_FAR_HZ = 700.0, 180.0   # over DIST_NEAR .. DIST_FAR
HUM_ATTACK_S, HUM_RELEASE_S = 0.25, 0.35


def hum_gain(dist_m):
    return HUM_RMS_AT_REF * min(HUM_MAX_GAIN, HUM_REF_DIST_M / max(dist_m, 0.05))


def hum_cutoff(dist_m, az_deg):
    d = min(max(dist_m, DIST_NEAR), DIST_FAR)
    t = (d - DIST_NEAR) / (DIST_FAR - DIST_NEAR)
    return (HUM_CUTOFF_NEAR_HZ + t * (HUM_CUTOFF_FAR_HZ - HUM_CUTOFF_NEAR_HZ)) * cutoff_factor(az_deg)


_HUM_NFFT = 4096
_HUM_IR_PSD = {}     # hrtf idx -> |IR|^2 per ear
_HUM_LP_PSD = {}     # (cutoff, sr) -> |lowpass|^2
_HUM_NORM = {}       # (idx, cutoff, sr) -> norm


def _hum_norm(idx, cutoff_hz, sr):
    """RMS per ear (mean power of both ears) of unit white noise through
    (HRIR idx x 4th-order lowpass at cutoff). Dividing by it makes the hum's
    loudness independent of direction and cutoff, so it stays a clean distance
    cue; the left/right balance within the pair is kept."""
    key = (idx, cutoff_hz, sr)
    if key not in _HUM_NORM:
        if idx not in _HUM_IR_PSD:
            _HUM_IR_PSD[idx] = np.abs(np.fft.rfft(_kemar().irs[idx], _HUM_NFFT, axis=0)) ** 2
        if (cutoff_hz, sr) not in _HUM_LP_PSD:
            from scipy.signal import sosfreqz
            _, h = sosfreqz(butter(4, cutoff_hz / (sr / 2), output="sos"), worN=_HUM_NFFT // 2 + 1)
            _HUM_LP_PSD[(cutoff_hz, sr)] = np.abs(h) ** 2
        p = (_HUM_IR_PSD[idx] * _HUM_LP_PSD[(cutoff_hz, sr)][:, None]).mean()
        _HUM_NORM[key] = float(np.sqrt(p))
    return _HUM_NORM[key]


def _ild_gains(az_deg):
    """Power-preserving extra ILD only (no front/back level trim)."""
    if not CUES["ild"]:
        return 1.0, 1.0
    s = np.sin(np.radians(az_deg))
    gl, gr = 10 ** (-EXTRA_ILD_DB / 2 * s / 20), 10 ** (EXTRA_ILD_DB / 2 * s / 20)
    norm = np.sqrt((gl * gl + gr * gr) / 2)
    return gl / norm, gr / norm


class Hum:
    """Per-voice noise bed. State-carrying so it is seamless across blocks."""

    def __init__(self, seed):
        self.rng = np.random.default_rng(seed)
        self.sos, self.cut, self.zi = None, None, None   # post-HRTF lowpass + its state
        self.hist = None          # last (taps-1) mono samples, for block convolution
        self.env = 0.0
        self.gain = 0.0
        self.key = None           # (hrtf idx, left gain, right gain) of the last block

    def _key(self, az, el):
        gl, gr = _ild_gains(az)
        return _kemar().idx_for(az, el), float(gl), float(gr)

    def _binaural(self, x, key, cut, sr):
        ir = _kemar().irs[key[0]]
        n = 1.0 / _hum_norm(key[0], cut, sr)
        left = fftconvolve(x, ir[:, 0], mode="valid") * (key[1] * n)
        right = fftconvolve(x, ir[:, 1], mode="valid") * (key[2] * n)
        return np.stack([left, right], axis=1)

    def _post_lowpass(self, y, c, sr):
        """Steep lowpass AFTER the HRTF (KEMAR boosts ~3 kHz, which would turn a
        gently filtered noise into hiss). Level normalisation is in _binaural."""
        if c != self.cut:
            self.sos = butter(4, c / (sr / 2), output="sos")
            self.cut = c
            if self.zi is None:
                self.zi = np.zeros((self.sos.shape[0], 2, 2))
        y, self.zi = sosfilt(self.sos, y, axis=0, zi=self.zi)
        return y

    def block(self, n, sr, active, az, el, dist):
        if not CUES["hum"] or SPATIALIZER != "hrtf":
            self.env, self.hist, self.key, self.zi = 0.0, None, None, None
            return None
        target = 1.0 if active else 0.0
        if self.env == 0.0 and target == 0.0:
            self.hist, self.key, self.zi = None, None, None
            return None
        # envelope (per-sample ramp toward on/off)
        step = n / sr / (HUM_ATTACK_S if target > self.env else HUM_RELEASE_S)
        env_new = min(target, self.env + step) if target > self.env else max(target, self.env - step)
        env = np.linspace(self.env, env_new, n, endpoint=False)
        self.env = env_new
        # gain ramp (distance), so dragging an obstacle never zippers
        g_new = hum_gain(dist)
        gain = np.linspace(self.gain if self.gain else g_new, g_new, n, endpoint=False)
        self.gain = g_new
        # white noise (unit RMS) with the envelope and distance gain
        x = self.rng.standard_normal(n) * env * gain
        # direction: convolve with history so block edges are seamless
        taps = _kemar().irs.shape[1]
        if self.hist is None:
            self.hist = np.zeros(taps - 1)
        buf = np.concatenate([self.hist, x])
        self.hist = buf[-(taps - 1):]
        key = self._key(az, el)
        cut = int(round(min(hum_cutoff(dist, az), 0.45 * sr) / 10.0)) * 10   # 10 Hz steps
        out = self._binaural(buf, key, cut, sr)
        if self.key is not None and self.key != key:   # moved: crossfade old -> new placement
            fade = np.linspace(0.0, 1.0, n)[:, None]
            out = self._binaural(buf, self.key, cut, sr) * (1 - fade) + out * fade
        self.key = key
        # low "hum": steep lowpass after the HRTF, cutoff opens as it gets closer
        return self._post_lowpass(out, cut, sr).astype(np.float32)


def spatialize(dry_mono, az_deg, dist_m, sr, el_deg=0.0):
    """Dry mono tone -> stereo at (az, el, dist). az: 0 = ahead, + right,
    full 360; el: + up. Called once per onset from the obstacle's current position."""
    if SPATIALIZER != "hrtf":
        return spatialize_itd_ild(dry_mono, az_deg, dist_m, sr)
    k = _kemar()
    if sr != k.sr:
        raise ValueError(f"tones are {sr} Hz but the KEMAR HRTF is {k.sr} Hz; re-render at {k.sr}")
    mono_mix = _distance_mix(dry_mono, dist_m, sr, wet=_room_wet(az_deg, dist_m))
    return _extra_ild(_hrtf(mono_mix, az_deg, el_deg), az_deg).astype(np.float32)


def spatialize_itd_ild(dry_mono, az_deg, dist_m, sr):
    """Previous model: stereo positioned by ITD/ILD and distance-
    loudness/reverb. Left/right only; azimuth clamped to +/-90."""
    az = max(-90.0, min(90.0, az_deg))
    theta = np.radians(abs(az))
    itd_s = (HEAD_RADIUS_M / SPEED_OF_SOUND_MPS) * (theta + np.sin(theta))  # Woodworth model
    itd_samples = int(round(itd_s * sr))

    norm = (az + 90.0) / 180.0  # 0 at full left, 1 at full right
    gain_left = np.cos(norm * np.pi / 2)
    gain_right = np.sin(norm * np.pi / 2)

    mono_mix = _distance_mix(dry_mono, dist_m, sr)
    n = len(mono_mix)

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
        self.el = 0.0
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
        self.hum = Hum(seed=slot_num)

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
        self.el = obstacle.get("el") or 0.0   # sensing_pi sends el; older feeds don't
        self.dist = obstacle["dist"]
        self.urgent = obstacle.get("level") == "urgent"
        self.period_samples = max(1, int(dist_to_period(obstacle["dist"]) * samplerate))
        cutoff = min(dist_to_cutoff(obstacle["dist"]) * cutoff_factor(self.az), 0.45 * samplerate)
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
RANK_HYSTERESIS_M = 0.30


def _overlap_add(playing, n_frames):
    """Vectorized overlap-add of [buffer, playhead] bursts into one block
    (a per-sample Python loop here blew the realtime callback's ~5.8 ms
    budget and caused audible dropouts). Returns (block, still_playing)."""
    still_playing = []
    block = np.zeros((n_frames, 2), dtype=np.float32)
    for buf, playhead in playing:
        i_start = max(0, -playhead)
        i_end = min(n_frames, len(buf) - playhead)
        if i_end > i_start:
            block[i_start:i_end] += buf[playhead + i_start: playhead + i_end]
        new_head = playhead + n_frames
        if new_head < len(buf):
            still_playing.append([buf, new_head])
    return block, still_playing


class Mixer:
    def __init__(self, sound_bank, samplerate=None):
        self.bank = sound_bank
        self.sr = samplerate or sound_bank.samplerate
        if SPATIALIZER == "hrtf":
            _kemar().match_level(sound_bank.beep_dry, self.sr)  # load now, not in the first callback
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
                    buf = spatialize(self.bank.beep_dry, slot.az, slot.dist, self.sr, slot.el)
                    if slot.urgent:
                        urgent_buf = spatialize(self.bank.urgent_dry, slot.az, slot.dist, self.sr, slot.el)
                        n = max(len(buf), len(urgent_buf))
                        mixed = np.zeros((n, 2), dtype=np.float32)
                        mixed[: len(buf)] += buf
                        mixed[: len(urgent_buf)] += urgent_buf
                        buf = mixed
                    slot.playing.append([buf, -offset_in_block if offset_in_block else 0])
                    slot.next_onset_sample += slot.period_samples

            voice_contribution, slot.playing = _overlap_add(slot.playing, n_frames)
            if slot.lpf_alpha > 0:
                voice_contribution = slot.apply_lpf(voice_contribution)
            out += voice_contribution
            hum = slot.hum.block(n_frames, self.sr, slot.active, slot.az, slot.el, slot.dist)
            if hum is not None:
                out += hum

        self.sample_counter = block_end
        peak = np.max(np.abs(out))
        if peak > 0.98:
            out *= 0.98 / peak  # simple safety limiter across overlapping voices
        return out

"""
Offline smoke test for mixer.py — no ZMQ, no sounddevice, no audio hardware
needed. Scripts a short scenario (obstacle sweeping, then an urgent spike,
then three at once) through the real Mixer and writes the result to
preview.wav so you can actually listen to it, plus asserts the signal is
sane (no NaN, no clipping, not silent when it shouldn't be).

Run: python test_mixer_smoke.py
"""
import numpy as np
import soundfile as sf

from mixer import Mixer, SoundBank

BLOCK = 256


def scripted_warnings(t):
    """t in seconds -> warning list, mimicking mock_warnings.py's scenarios."""
    if t < 1.5:
        # single obstacle sweeping left -> right, far (hum only) -> near (beeping)
        az = -80 + (t / 1.5) * 160
        dist = 0.9 - (t / 1.5) * 0.6
        return [{"id": "S_sweep", "az": az, "dist": dist, "level": "warn"}]
    elif t < 3.0:
        # that obstacle is now urgent-close
        return [{"id": "S_sweep", "az": 70, "dist": 0.2, "level": "urgent"}]
    elif t < 5.0:
        # three simultaneous obstacles: hum only, beeping, urgent
        return [
            {"id": "A", "az": -60, "dist": 0.8, "level": "warn"},
            {"id": "B", "az": 10, "dist": 0.4, "level": "warn"},
            {"id": "C", "az": 75, "dist": 0.2, "level": "urgent"},
        ]
    else:
        return []  # silence tail — should produce near-zero output


def main():
    bank = SoundBank()
    mixer = Mixer(bank)
    sr = mixer.sr
    duration_s = 7.5  # margin after the t=5.0 silence transition for a long in-flight note to finish
    n_blocks = int(duration_s * sr / BLOCK)

    out_chunks = []
    last_update_t = -1
    for b in range(n_blocks):
        t = b * BLOCK / sr
        if int(t * 20) != int(last_update_t * 20):  # update warnings ~20 Hz
            mixer.update_warnings(scripted_warnings(t))
            last_update_t = t
        out_chunks.append(mixer.render_block(BLOCK))

    audio = np.concatenate(out_chunks, axis=0)

    assert not np.isnan(audio).any(), "NaN in output!"
    peak = np.max(np.abs(audio))
    assert peak <= 1.0, f"Clipping! peak={peak}"

    tail = audio[int(6.5 * sr):]  # 1.5s past the silence transition — covers the longest note (~0.65s) plus stagger
    tail_rms = np.sqrt(np.mean(tail ** 2))
    active_rms = np.sqrt(np.mean(audio[int(3.0 * sr):int(5.0 * sr)] ** 2))

    print(f"duration={len(audio)/sr:.2f}s peak={peak:.3f}")
    print(f"3-voice-section RMS={active_rms:.4f}  silence-tail RMS={tail_rms:.5f}")
    assert active_rms > 0.01, "expected audible output during the 3-obstacle section"
    assert tail_rms < 0.001, "expected near-silence once warning list goes empty"

    sf.write("preview.wav", audio, sr)
    hum_comes_back(bank)
    trigger_distances(bank)
    print("OK — wrote preview.wav, all smoke assertions passed")


def trigger_distances(bank):
    """Beeps only from BEEP_DIST_M, urgent cue only from URGENT_DIST_M, decided by
    distance (the feed's "level" is ignored). Hum off so only beeps are measured."""
    import mixer as M
    hum_was, M.CUES["hum"] = M.CUES["hum"], False
    try:
        def run(dist, level="warn", el=0.0):
            mixer = Mixer(bank)
            mixer.update_warnings([{"id": "T", "az": 0, "el": el, "dist": dist, "level": level}])
            audio = np.concatenate([mixer.render_block(BLOCK) for _ in range(int(2.0 * mixer.sr / BLOCK))])
            return float(np.sqrt(np.mean(audio ** 2))), mixer.slots[0].urgent

        far, far_urgent = run(M.BEEP_DIST_M + 0.05, level="urgent")   # Pi says urgent; still too far
        near, near_urgent = run(M.BEEP_DIST_M - 0.05)
        close, close_urgent = run(M.URGENT_DIST_M - 0.05)
        assert far < 1e-4 and not far_urgent, f"no beeps beyond {M.BEEP_DIST_M} m (rms {far})"
        assert near > 0.01 and not near_urgent, f"beeps (not urgent) inside {M.BEEP_DIST_M} m (rms {near})"
        assert close > 0.01 and close_urgent, f"urgent cue inside {M.URGENT_DIST_M} m"
        # beep trigger is HORIZONTAL distance: 0.85 m at 45 deg below = 0.60 m horizontal -> beeps
        low, _ = run(0.85, el=-45.0)
        # (quieter than straight ahead: loudness follows straight-line distance)
        assert low > 1e-3, f"0.85 m at el -45 is 0.60 m horizontal, should beep (rms {low})"
        print(f"trigger distances: silent > {M.BEEP_DIST_M} m horiz, beeps <= {M.BEEP_DIST_M} m horiz "
              f"(also for low objects), urgent <= {M.URGENT_DIST_M} m: OK")
    finally:
        M.CUES["hum"] = hum_was


def hum_comes_back(bank):
    """Regression: an obstacle vanishes long enough for its hum to fade out fully,
    then returns at the same distance (same hum cutoff). That used to crash the
    audio callback in Hum._post_lowpass (filter state dropped, never rebuilt)."""
    mixer = Mixer(bank)
    sr = mixer.sr
    w = [{"id": "R", "az": 30, "dist": 1.0, "level": "warn"}]
    out = []
    for warnings, secs in ((w, 0.5), ([], 1.0), (w, 0.5), ([], 1.0), (w, 0.5)):
        mixer.update_warnings(warnings)
        out += [mixer.render_block(BLOCK) for _ in range(int(secs * sr / BLOCK))]
    audio = np.concatenate(out)
    assert not np.isnan(audio).any()
    back = audio[int(3.1 * sr):]
    assert np.sqrt(np.mean(back ** 2)) > 0.001, "expected sound after the obstacle returned"
    print("hum on -> off -> on at the same distance: OK")


if __name__ == "__main__":
    main()

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
        # single obstacle sweeping left -> right, far -> near
        az = -80 + (t / 1.5) * 160
        dist = 1.4 - (t / 1.5) * 0.9
        return [{"id": "S_sweep", "az": az, "dist": dist, "level": "warn"}]
    elif t < 3.0:
        # that obstacle is now urgent-close
        return [{"id": "S_sweep", "az": 70, "dist": 0.35, "level": "urgent"}]
    elif t < 5.0:
        # three simultaneous obstacles, one urgent
        return [
            {"id": "A", "az": -60, "dist": 0.9, "level": "warn"},
            {"id": "B", "az": 10, "dist": 1.2, "level": "warn"},
            {"id": "C", "az": 75, "dist": 0.4, "level": "urgent"},
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
    print("OK — wrote preview.wav, all smoke assertions passed")


if __name__ == "__main__":
    main()

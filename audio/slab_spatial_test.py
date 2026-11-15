#!/usr/bin/env python3
"""Type a coordinate, hear the engine's real warning sound from there. Use headphones.

Goes through the same Mixer as engine.py (same tones, tempo, distance cues,
slab KEMAR HRTF direction), fed a fake one-obstacle warning list.

    python slab_spatial_test.py [--device NAME] [--secs 2.5]

At the prompt:
    x y z              metres, head frame: x forward, y right, z up   e.g.  1 -1 0  (front-left)
    a AZ EL [D]        degrees: az 0 = ahead, + right; el + up; D metres (default 1)
    s [D] [SECS] [EL]  round sweep: one full circle clockwise from ahead
                       (ahead -> right -> behind -> left -> ahead), default 1 m, 8 s, el 0
    fb [AZ] [D]        front/back pair: AZ then its mirror behind (180-AZ), twice; default 0 deg, 1 m
    cues               show the extra cues (room cutoff ild) and the hum layer
    cues on | off      all of them on / off (off = plain HRTF, for A/B)
    cues -NAME +NAME   switch single cues, e.g.  cues -hum +ild
    q                  quit

Under 0.5 m the obstacle is sent as "urgent", so the urgent cue plays too.
"""
import argparse
import math

import numpy as np
import sounddevice as sd

import mixer as M
from mixer import Mixer, SoundBank

BLOCK = 256
UPDATE_S = 0.05   # warning-list rate, like the real feed (~20 Hz)


def to_az_el_dist(x, y, z):
    dist = math.sqrt(x * x + y * y + z * z)
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y))), dist


def warning(az, el, dist, oid="T"):
    az = (az + 180) % 360 - 180
    return [{"id": oid, "az": az, "el": el, "dist": dist, "level": "urgent" if dist < 0.5 else "warn"}]


def cues_cmd(t):
    for arg in t[1:]:
        if arg in ("on", "off"):
            for k in M.CUES:
                M.CUES[k] = arg == "on"
        elif arg[:1] in "+-" and arg[1:] in M.CUES:
            M.CUES[arg[1:]] = arg[0] == "+"
        else:
            print(f"  ? unknown cue '{arg}' (have: {' '.join(M.CUES)})")
    print("  cues: " + "  ".join(f"{k}={'on' if v else 'off'}" for k, v in M.CUES.items()))


def render(bank, secs, at):
    """at(t) -> warning list; returns stereo float32 of `secs` seconds through a fresh Mixer."""
    mixer = Mixer(bank)
    sr = mixer.sr
    out, t, next_update = [], 0.0, 0.0
    while t < secs:
        if t >= next_update:
            mixer.update_warnings(at(t))
            next_update += UPDATE_S
        out.append(mixer.render_block(BLOCK))
        t += BLOCK / sr
    mixer.update_warnings([])
    for _ in range(int(0.6 * sr / BLOCK)):      # let the last beep ring out
        out.append(mixer.render_block(BLOCK))
    return np.concatenate(out), sr


def side(az):
    az = (az + 180) % 360 - 180
    s = "ahead" if abs(az) < 10 else ("right" if az > 0 else "left")
    return s + (", behind" if abs(az) > 100 else "")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", help="output device (substring); see engine.py --list-devices")
    p.add_argument("--secs", type=float, default=2.5, help="how long a single position plays")
    a = p.parse_args()

    print("loading tones + KEMAR HRTF...")
    bank = SoundBank()
    print(__doc__.split("At the prompt:")[1].rstrip())
    while True:
        try:
            line = input("\ncoord> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        t = line.split()
        cmd = t[0].lower()
        try:
            if cmd in ("q", "quit", "exit"):
                break
            if cmd == "cues":
                cues_cmd(t)
                continue
            if cmd == "fb":
                az = float(t[1]) if len(t) > 1 else 0.0
                dist = float(t[2]) if len(t) > 2 else 1.0
                back = 180.0 - az
                seg = 2.0
                order = [az, back, az, back]
                print("  " + " | ".join(f"{'FRONT' if i % 2 == 0 else 'BACK '} az {(x + 180) % 360 - 180:+.0f}"
                                         for i, x in enumerate(order)) + f"  @ {dist:.2f} m")
                audio, sr = render(bank, seg * len(order),
                                   lambda tt: warning(order[min(int(tt // seg), 3)], 0.0, dist,
                                                      oid=f"T{min(int(tt // seg), 3)}"))
            elif cmd == "s":
                dist = float(t[1]) if len(t) > 1 else 1.0
                secs = float(t[2]) if len(t) > 2 else 8.0
                el = float(t[3]) if len(t) > 3 else 0.0
                print(f"  sweep: full circle at {dist:.2f} m, el {el:+.0f}, {secs:.0f} s "
                      f"(ahead -> right -> behind -> left -> ahead)")
                audio, sr = render(bank, secs, lambda tt: warning(360.0 * tt / secs, el, dist))
            else:
                if cmd == "a":
                    az, el = float(t[1]), float(t[2])
                    dist = float(t[3]) if len(t) > 3 else 1.0
                elif len(t) == 3:
                    az, el, dist = to_az_el_dist(*map(float, t))
                else:
                    raise ValueError
                print(f"  az {az:+.0f} deg ({side(az)}), el {el:+.0f} deg, {dist:.2f} m"
                      + ("  [urgent]" if dist < 0.5 else ""))
                audio, sr = render(bank, a.secs, lambda tt: warning(az, el, dist))
        except (ValueError, IndexError):
            print("  ? type 'x y z', 'a AZ EL [DIST]', 's [DIST] [SECS] [EL]', 'fb [AZ] [DIST]', 'cues ...' or 'q'")
            continue
        try:
            sd.play(audio, sr, device=a.device)
            sd.wait()
        except KeyboardInterrupt:
            sd.stop()
            print("  (stopped)")


if __name__ == "__main__":
    main()

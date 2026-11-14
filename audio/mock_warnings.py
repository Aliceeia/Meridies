"""
Standalone stand-in for C's warning-list publisher, so engine.py can be
developed and demoed without waiting on the selection process. Publishes
the same wire format C will eventually send on port 5556.

Usage:
    python mock_warnings.py             # travel: two obstacles in continuous independent motion
    python mock_warnings.py sweep       # one obstacle sweeping L->R, far->near
    python mock_warnings.py urgent      # single close/urgent obstacle
    python mock_warnings.py three       # three simultaneous, one urgent
    python mock_warnings.py silence     # nothing (tests the "quiet by default" rule)
"""
import json
import math
import sys
import time

import zmq

PORT = 5556
HZ = 20


def scenario_sweep(t):
    az = -80 + ((t * 0.3) % 2.0) / 2.0 * 160
    dist = 1.4 - abs(((t * 0.3) % 2.0) - 1.0) * 0.9
    return [{"id": "S_sweep", "az": round(az, 1), "dist": round(dist, 2), "level": "warn"}]


def scenario_urgent(t):
    return [{"id": "S5", "az": -80, "dist": 0.35, "level": "urgent"}]


def scenario_three(t):
    """Three obstacles at FIXED directions whose distances oscillate out of
    phase, so which one is nearest keeps rotating. The list is sorted
    nearest-first (matching C's real contract) before being returned, so
    whichever obstacle is currently closest gets voice-slot 1's timbre —
    proving timbre tracks proximity rank, not direction. Listen for a given
    direction's beep changing timbre as a *different* obstacle becomes the
    nearest one."""
    period = 6.0
    obstacles = [
        {"id": "A_left", "az": -60},
        {"id": "B_center", "az": 10},
        {"id": "C_right", "az": 75},
    ]
    phases = [0, 2 * math.pi / 3, 4 * math.pi / 3]
    out = []
    for obs, phase in zip(obstacles, phases):
        dist = 0.9 + 0.55 * math.sin(2 * math.pi * t / period + phase)
        dist = round(max(0.35, dist), 2)
        level = "urgent" if dist < 0.5 else "warn"
        out.append({"id": obs["id"], "az": obs["az"], "dist": dist, "level": level})
    out.sort(key=lambda w: w["dist"])
    return out


def scenario_silence(t):
    return []


def _triangle_wave(t, period, amplitude, phase=0.0):
    """Linear (constant angular velocity) sweep, unlike sin() — a sine
    sweep moves fastest through the center and lingers at the extremes, so
    straight-ahead (az=0) gets the LEAST dwell time of any angle, which
    made "front" feel like a non-event in practice. Triangle wave gives
    every angle, including center, equal dwell time."""
    x = ((t + phase) / period) % 1.0  # 0..1
    tri = 4 * abs(x - 0.5) - 1  # -1..1..-1, linear ramps
    return amplitude * -tri


def scenario_travel(t):
    """Two obstacles in continuous independent motion — azimuth AND distance
    both constantly changing, different periods/phases so they move
    independently rather than mirroring each other. This is the direct test
    of "does it actually feel spatial": a static sweep-then-stop scenario
    doesn't give you a strong sense of something moving THROUGH 3D space
    the way continuous, simultaneous az+dist motion does."""
    az1 = _triangle_wave(t, period=4.0, amplitude=90.0)
    dist1 = max(0.35, 1.0 + 0.55 * math.sin(2 * math.pi * t / 6.0))
    az2 = _triangle_wave(t, period=5.0, amplitude=90.0, phase=1.5)
    dist2 = max(0.35, 1.0 + 0.55 * math.cos(2 * math.pi * t / 7.0))

    out = [
        {"id": "T1", "az": round(az1, 1), "dist": round(dist1, 2)},
        {"id": "T2", "az": round(az2, 1), "dist": round(dist2, 2)},
    ]
    for w in out:
        w["level"] = "urgent" if w["dist"] < 0.5 else "warn"
    out.sort(key=lambda w: w["dist"])
    return out


SCENARIOS = {
    "travel": scenario_travel,
    "sweep": scenario_sweep,
    "urgent": scenario_urgent,
    "three": scenario_three,
    "silence": scenario_silence,
}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "travel"
    scenario = SCENARIOS[name]

    ctx = zmq.Context()
    pub = ctx.socket(zmq.PUB)
    pub.bind(f"tcp://*:{PORT}")
    print(f"mock_warnings: publishing '{name}' on tcp://*:{PORT} at {HZ} Hz (Ctrl+C to stop)")
    time.sleep(0.3)  # give subscribers a moment to connect (slow-joiner)

    t0 = time.time()
    try:
        while True:
            t = time.time() - t0
            warnings = scenario(t)
            pub.send_string(json.dumps(warnings))
            time.sleep(1.0 / HZ)
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

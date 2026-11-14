"""
Standalone stand-in for C's warning-list publisher, so engine.py can be
developed and demoed without waiting on the selection process. Publishes
the same wire format C will eventually send on port 5556.

Usage:
    python mock_warnings.py            # default scenario
    python mock_warnings.py sweep       # one obstacle sweeping L->R, far->near
    python mock_warnings.py urgent      # single close/urgent obstacle
    python mock_warnings.py three       # three simultaneous, one urgent
    python mock_warnings.py silence     # nothing (tests the "quiet by default" rule)
"""
import json
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
    return [
        {"id": "S3", "az": -45, "dist": 0.9, "level": "warn"},
        {"id": "S1", "az": -15, "dist": 1.2, "level": "warn"},
        {"id": "S6", "az": 80, "dist": 0.4, "level": "urgent"},
    ]


def scenario_silence(t):
    return []


SCENARIOS = {
    "sweep": scenario_sweep,
    "urgent": scenario_urgent,
    "three": scenario_three,
    "silence": scenario_silence,
}


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "sweep"
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

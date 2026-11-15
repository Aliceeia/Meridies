# sensing_pi: six VL53L5CX ToF sensors → obstacle objects → audio warnings

Runs on the Raspberry Pi in the cap. One command starts everything.

## Run (from `sensing_pi/`)

```bash
.venv/bin/python main.py
```

That starts:

| Port | Stream | Who uses it |
|---|---|---|
| TCP **5557** | raw frames from the six sensors (8×8 distances, status, sigma, signal) | `tools/tof6_view.py`, recordings |
| ZMQ **5556** | up to 3 warnings, nearest first (`id, az, dist, level` + extras) | the audio engine (`audio/engine.py`) |
| TCP **5558** | every object + scan points | `tools/objects_view.py` |

Ctrl-C stops it. Useful options (`main.py --help` for all):

| Option | What it does |
|---|---|
| `--rig config/flat_mounts.json` | the flat bench plate instead of the cap |
| `--csv run.csv` | also record the raw frames |
| `--raw-only` | only the raw stream, no object detection |
| `--sim corridor` | no hardware: a simulated scene (`wall pole_sweep approach pole_wall corridor`) |
| `--replay run.csv` | no hardware: replay a recording |
| `--no-audio` | don't publish on 5556 |
| `--verbose` | print every object frame |

First time on a new Pi: `python3 -m venv --system-site-packages .venv && .venv/bin/pip install -r requirements.txt`, and enable I2C at 1 MHz (`docs/wiring.md`).

## What to edit

| To change | Edit |
|---|---|
| where each sensor sits and points (cm, angle, tilt) | `config/sensor_layout.py` |
| rig settings, per-sensor roll / flips / range calibration | `config/cap_mounts.json`, `config/flat_mounts.json` |
| detection thresholds, noise filter, body zone, ports | `config/objects.json` |
| the learned self-mask | re-learn with `tools/learn_selfmask.py` (writes `config/selfmask_cap_mounts.json`) |
| which GPIO each sensor's LPn / INT uses | `sensors/pins.py` |

## Layout

```
main.py            the one entry point: starts the raw stream and the object stream
config/            layout, rig and tuning files (see above)
sensors/           six-sensor driver (tof6.py), pins, frame/CSV format (frames.py),
                   raw stream program (record.py = python -m sensors.record)
geometry/          zones -> 3D points in the head frame, layout conversion, calibration, IMU hook
objects/           cleanup (quality.py, body.py) -> scan -> clusters -> objects -> tracking -> ranking,
                   outputs (publish.py), object stream program (run.py = python -m objects.run)
streams/           frame sources (live / tcp / csv / sim) and the TCP JSON-line server
sim/               ray-cast scene simulator (tests and --sim)
tools/             helpers: laptop viewers (tof6_view.py, objects_view.py), feed_check.py, and the calibration
                   tools learn_selfmask.py, hand_test.py (left/right, up/down), range_cal.py (wall distances)
tests/             automated tests (run_all.py) and hardware bench checks (tests/hardware/)
docs/              wiring, cap layout, object pipeline, guides for the audio side and the laptop agent
```

## Test

```bash
.venv/bin/python tests/run_all.py
```

Runs every automated test (no sensors needed). With the sensors connected, `tests/hardware/tof6_ping.py` checks that all six boards answer on their own LPn line.

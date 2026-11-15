"""Checks for the six-sensor driver logic that doesn't need hardware. Run from sensing_pi/:
    .venv/bin/python tests/test_sensors.py        (or all tests: .venv/bin/python tests/run_all.py)
(also works under pytest)."""
import fixtures  # noqa: F401  (puts sensing_pi/ on sys.path)
from sensors.tof6 import STALE_S, stale_sensors


def test_silent_sensor_is_stale_after_the_limit():
    now = 100.0
    last = [now - 0.05, now - 0.2, now - (STALE_S + 0.1), now - 5.0, now, now - STALE_S]
    assert stale_sensors(last, [0, 1, 2, 3, 4, 5], now) == {2, 3}


def test_missing_sensors_are_not_reported_as_stale():
    now = 10.0
    last = [now - 9.0] * 6
    assert stale_sensors(last, [0, 1, 4], now) == {0, 1, 4}     # only the active ones are considered
    assert stale_sensors(last, [], now) == set()


if __name__ == "__main__":
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")

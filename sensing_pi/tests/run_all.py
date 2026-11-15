#!/usr/bin/env python3
"""Run every automated test (tests/test_*.py) without pytest. From sensing_pi/:

    .venv/bin/python tests/run_all.py            # all
    .venv/bin/python tests/run_all.py body       # only files/tests whose name contains "body"

The bench scripts in tests/hardware/ need the sensors and are not run here.
"""
import importlib
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402,F401  (puts sensing_pi/ on sys.path)


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else ""
    passed, failed = 0, []
    t0 = time.monotonic()
    for path in sorted(HERE.glob("test_*.py")):
        mod = importlib.import_module(path.stem)
        tests = [(n, f) for n, f in vars(mod).items() if n.startswith("test_") and callable(f)]
        for name, fn in tests:
            if pattern and pattern not in path.stem and pattern not in name:
                continue
            try:
                fn()
                passed += 1
                print(f"ok    {path.stem}.{name}")
            except Exception:
                failed.append(f"{path.stem}.{name}")
                print(f"FAIL  {path.stem}.{name}")
                traceback.print_exc()
    print(f"\n{passed} passed, {len(failed)} failed in {time.monotonic() - t0:.1f} s")
    for f in failed:
        print("  failed:", f)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

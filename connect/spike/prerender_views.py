"""Pre-render the 4 first-person views of every sweep of a showcase model (cached for /api/view and descriptions).

    python connect/spike/prerender_views.py <model> <plan.json in connect/data> [procs]
"""
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connect.backend.interior_render import CACHE, Renderer  # noqa: E402

YAWS = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2]


def chunk(model, sweeps):
    n = 0
    with Renderer(model=model) as r:
        for s in sweeps:
            for y in YAWS:
                try:
                    r.render_view(s, y); n += 1
                except Exception as e:
                    print(f"  sweep {s} yaw {y:.2f} failed: {e!r}"[:140], flush=True)
    return n


if __name__ == "__main__":
    model, plan = sys.argv[1], sys.argv[2]
    procs = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    fp = json.loads((Path(__file__).resolve().parents[1] / "data" / plan).read_text())
    todo = [s["index"] for s in fp["sweeps"]
            if not all((CACHE / f"{model}_ss{s['index'] + 1}_sr0.000,{y:.3f}.png").exists() for y in YAWS)]
    print(f"{model}: {len(todo)} of {len(fp['sweeps'])} sweeps to render in {procs} browsers", flush=True)
    t0 = time.time()
    with ProcessPoolExecutor(procs) as ex:
        for f in as_completed([ex.submit(chunk, model, todo[i::procs]) for i in range(procs)]):
            print(f"  chunk done: {f.result()} views ({time.time() - t0:.0f}s)", flush=True)

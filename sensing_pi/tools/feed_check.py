#!/usr/bin/env python3
"""Check the object pipeline's outputs from any machine (Pi or laptop).

  python tools/feed_check.py --host 172.20.10.5 --secs 8

Subscribes to the audio warnings exactly like audio/engine.py (ZMQ SUB tcp://HOST:5556),
reads the debug stream (TCP JSON lines on HOST:5558) and optionally the raw sensor stream
(HOST:5557), then reports rates, schema, ids and a sample. Needs pyzmq for the 5556 check.
"""
import argparse
import json
import socket
import sys
import threading
import time


def tcp_counter(host, port, out, secs):
    out.update(n=0, keys=None, err=None)
    try:
        s = socket.create_connection((host, port), timeout=3)
    except OSError as e:
        out["err"] = str(e)
        return
    s.settimeout(1)
    f = s.makefile("r")
    t0 = time.time()
    while time.time() - t0 < secs:
        try:
            line = f.readline()
        except OSError:
            continue
        if not line:
            break
        out["n"] += 1
        out["keys"] = sorted(json.loads(line))
    s.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="localhost")
    p.add_argument("--secs", type=float, default=8)
    p.add_argument("--raw", action="store_true", help="also check the raw sensor stream on 5557")
    a = p.parse_args()

    dbg, raw = {}, {}
    threads = [threading.Thread(target=tcp_counter, args=(a.host, 5558, dbg, a.secs), daemon=True)]
    if a.raw:
        threads.append(threading.Thread(target=tcp_counter, args=(a.host, 5557, raw, a.secs), daemon=True))
    for t in threads:
        t.start()

    import zmq
    ctx = zmq.Context()
    sub = ctx.socket(zmq.SUB)
    sub.connect(f"tcp://{a.host}:5556")
    sub.setsockopt_string(zmq.SUBSCRIBE, "")
    sub.RCVTIMEO = 500
    msgs, t0 = [], time.time()
    while time.time() - t0 < a.secs:
        try:
            msgs.append((time.time(), json.loads(sub.recv_string())))
        except zmq.Again:
            pass
    for t in threads:
        t.join(a.secs + 2)

    ok = True
    if len(msgs) < 2:
        print(f"5556 audio warnings: only {len(msgs)} messages -> is main.py running? firewall?")
        ok = False
    else:
        rate = (len(msgs) - 1) / (msgs[-1][0] - msgs[0][0])
        schema = all(isinstance(w, list) and len(w) <= 3
                     and all({"id", "az", "dist", "level"} <= set(e) and e["dist"] is not None for e in w)
                     and [e["dist"] for e in w] == sorted(e["dist"] for e in w) for _, w in msgs)
        ok &= schema
        ids = sorted({e["id"] for _, w in msgs for e in w}, key=lambda s: int(s[1:]) if s[1:].isdigit() else 0)
        print(f"5556 audio warnings: {len(msgs)} msgs, {rate:.1f}/s, schema ok={schema}, ids seen={ids[:12]}"
              f"{' ...' if len(ids) > 12 else ''}")
        print("  latest:", json.dumps(msgs[-1][1]))
    print(f"5558 debug stream:   {dbg.get('n', 0)} msgs, keys={dbg.get('keys')}" + (f"  ERROR {dbg['err']}" if dbg.get("err") else ""))
    if a.raw:
        print(f"5557 raw frames:     {raw.get('n', 0)} msgs, keys={raw.get('keys')}" + (f"  ERROR {raw['err']}" if raw.get("err") else ""))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

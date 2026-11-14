#!/usr/bin/env python3
"""Record six VL53L5CX sensors (2 x 3 grid) as one combined frame. Pi only.

Brings the sensors up one at a time with their LPn lines, moves them to 0x30-0x35,
then reads each one when its INT line fires. A combined frame is emitted once all six
have a new reading (or after 2 periods, so one dead sensor can't freeze the stream).

Run from sensing_pi/:
  .venv/bin/python tests/multi6/tof6_record.py --csv rec.csv --serve 5557
"""
import argparse
import csv
import json
import socket
import sys
import time
from pathlib import Path

import lgpio
import vl53l5cx_ctypes as vl53
from smbus2 import SMBus

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tof6_common as C  # noqa: E402


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--res", type=int, choices=(4, 8), default=8)
    p.add_argument("--hz", type=int, help="per-sensor rate (default 10 at 8x8, 15 at 4x4)")
    p.add_argument("--frames", type=int, default=0, help="stop after N combined frames (0 = until Ctrl-C)")
    p.add_argument("--csv", help="write every combined frame to this CSV")
    p.add_argument("--serve", type=int, metavar="PORT", help="stream frames as JSON lines over TCP on this port")
    p.add_argument("--no-int", action="store_true", help="ignore INT pins and poll every sensor over I2C")
    return p.parse_args()


def present(bus, addr):
    try:
        bus.write_quick(addr)
        return True
    except OSError:
        return False


def bring_up(h, bus, res, hz):
    """LPn sequencing: all low, then raise one at a time and move it to its own address."""
    for pin in C.LPN_PINS:
        lgpio.gpio_claim_output(h, pin, 0)
    time.sleep(0.05)

    sensors = []
    for k in range(C.N_SENSORS):
        name, target = f"S{k + 1}", C.ADDRS[k]
        lgpio.gpio_write(h, C.LPN_PINS[k], 1)
        time.sleep(0.02)

        if present(bus, target):          # already moved in an earlier run (no power cycle since)
            how = "already at"
        elif present(bus, C.DEFAULT_ADDR):
            s = vl53.VL53L5CX(i2c_addr=C.DEFAULT_ADDR, i2c_dev=bus, skip_init=True)
            if not s.set_i2c_address(target):
                sys.exit(f"{name}: failed to move 0x29 -> 0x{target:02x}")
            how = "moved to"
        else:
            sys.exit(f"{name}: nothing at 0x29 or 0x{target:02x} with LPn GPIO{C.LPN_PINS[k]} high. "
                     f"Check its LPn wire, power and SDA/SCL.")

        t0 = time.monotonic()
        s = vl53.VL53L5CX(i2c_addr=target, i2c_dev=bus, skip_init=True)
        if not s.init():
            sys.exit(f"{name}: firmware init failed at 0x{target:02x}")
        s.set_resolution(res * res)
        s.set_ranging_frequency_hz(hz)
        s.set_target_order(vl53.TARGET_ORDER_CLOSEST)
        print(f"{name}: LPn GPIO{C.LPN_PINS[k]:<2}  {how} 0x{target:02x}  init {time.monotonic() - t0:.1f} s", flush=True)
        sensors.append(s)
    return sensors


class Server:
    """Tiny TCP fan-out: every client gets one JSON line per frame. Slow clients are dropped."""

    def __init__(self, port):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("0.0.0.0", port))
        self.srv.listen()
        self.srv.setblocking(False)
        self.clients = []
        print(f"streaming on tcp port {port}", flush=True)

    def send(self, frame):
        try:
            c, addr = self.srv.accept()
            c.settimeout(0.2)
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.clients.append(c)
            print(f"viewer connected from {addr[0]}", flush=True)
        except BlockingIOError:
            pass
        line = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
        for c in self.clients[:]:
            try:
                c.sendall(line)
            except OSError:
                self.clients.remove(c)
                c.close()

    def close(self):
        for c in self.clients:
            c.close()
        self.srv.close()


def main():
    a = parse_args()
    hz = a.hz or (10 if a.res == 8 else 15)
    z = a.res * a.res
    period = 1.0 / hz

    h = lgpio.gpiochip_open(0)
    bus = SMBus(1)
    sensors = bring_up(h, bus, a.res, hz)

    # INT: open-drain, active low; count falling edges and flag the sensor as ready.
    flags = [False] * C.N_SENSORS
    int_edges = [0] * C.N_SENSORS
    callbacks = []
    if not a.no_int:
        for k, pin in enumerate(C.INT_PINS):
            lgpio.gpio_claim_alert(h, pin, lgpio.FALLING_EDGE, lgpio.SET_PULL_UP)

            def on_edge(chip, gpio, level, tick, k=k):
                flags[k] = True
                int_edges[k] += 1
            callbacks.append(lgpio.callback(h, pin, lgpio.FALLING_EDGE, on_edge))

    writer = csv_file = None
    if a.csv:
        csv_file = open(a.csv, "w", newline="")
        csv_file.write(C.csv_meta_line(a.res, hz))
        writer = csv.writer(csv_file)
        writer.writerow(C.csv_header(a.res))
    server = Server(a.serve) if a.serve else None

    for s in sensors:
        s.start_ranging()
    print(f"ranging {a.res}x{a.res} @ {hz} Hz per sensor, INT {'off (polling)' if a.no_int else 'on'}. Ctrl-C to stop.",
          flush=True)

    dist = [[-1] * z for _ in range(C.N_SENSORS)]
    stat = [[0] * z for _ in range(C.N_SENSORS)]
    fresh = [False] * C.N_SENSORS
    reads = [0] * C.N_SENSORS
    errors = [0] * C.N_SENSORS
    now = time.monotonic()
    last_read = [now] * C.N_SENSORS
    last_emit = last_report = t_start = now
    n_frames = 0

    try:
        while not a.frames or n_frames < a.frames:
            now = time.monotonic()
            for k, s in enumerate(sensors):
                # Read on INT edge; if INT is off/broken, fall back to polling once the sensor is overdue.
                if not (a.no_int or flags[k] or now - last_read[k] > 3 * period):
                    continue
                flags[k] = False
                try:
                    if not s.data_ready():
                        continue
                    data = s.get_data()
                except (RuntimeError, OSError):
                    errors[k] += 1
                    continue
                dist[k] = list(data.distance_mm[0][:z])
                stat[k] = list(data.target_status[0][:z])
                fresh[k] = True
                reads[k] += 1
                last_read[k] = now

            if all(fresh) or (any(fresh) and now - last_emit > 2 * period):
                frame = {"t": round(time.time(), 4), "res": a.res,
                         "d": [d[:] for d in dist], "s": [s_[:] for s_ in stat]}
                if writer:
                    writer.writerow(C.frame_to_row(frame))
                if server:
                    server.send(frame)
                fresh = [False] * C.N_SENSORS
                last_emit = now
                n_frames += 1
            else:
                time.sleep(0.001)

            if now - last_report >= 2.0:
                el = now - t_start
                per = "  ".join(f"S{k + 1} {reads[k] / el:4.1f}Hz int={int_edges[k]} err={errors[k]}"
                                for k in range(C.N_SENSORS))
                print(f"[{el:5.0f}s] frames={n_frames} ({n_frames / el:4.1f}/s) | {per}", flush=True)
                last_report = now
    except KeyboardInterrupt:
        pass
    finally:
        for s in sensors:
            try:
                s.stop_ranging()
            except Exception:
                pass
        for cb in callbacks:
            cb.cancel()
        if csv_file:
            csv_file.close()
        if server:
            server.close()
        lgpio.gpiochip_close(h)   # LPn lines float back up via the boards' pull-ups; addresses are kept

    el = time.monotonic() - t_start
    print(f"\n--- {n_frames} combined frames in {el:.1f} s")
    for k in range(C.N_SENSORS):
        warn = "  <- no INT edges: check INT wire" if not a.no_int and int_edges[k] == 0 else ""
        print(f"S{k + 1}: {reads[k]} reads, {errors[k]} errors, {int_edges[k]} INT edges{warn}")
    if a.csv:
        print(f"saved {a.csv}")


if __name__ == "__main__":
    main()

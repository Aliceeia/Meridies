"""Six VL53L5CX sensors on one I2C bus: LPn re-addressing, INT-driven reads, combined frames. Pi only.

    with Tof6(res=8, hz=10) as tof:
        for frame in tof.frames():   # {"t", "res", "d": [6 x zones], "s": [6 x zones]}
            ...

Pin map and addresses: sensors/pins.py (wiring chart in docs/wiring.md).
"""
from __future__ import annotations

import sys
import time

from .pins import ADDRS, DEFAULT_ADDR, INT_PINS, LPN_PINS, N_SENSORS


class Tof6:
    def __init__(self, res: int = 8, hz: int | None = None, use_int: bool = True, log=print,
                 allow_missing: bool = False):
        import lgpio
        import vl53l5cx_ctypes as vl53
        from smbus2 import SMBus

        self._lgpio, self._vl53 = lgpio, vl53
        self.res = res
        self.hz = hz or (10 if res == 8 else 15)
        self.use_int = use_int
        self.log = log
        self.zones = res * res
        self.period = 1.0 / self.hz
        self.allow_missing = allow_missing

        self.h = lgpio.gpiochip_open(0)
        self.bus = SMBus(1)
        self.sensors = self._bring_up()

        self.flags = [False] * N_SENSORS
        self.int_edges = [0] * N_SENSORS
        self.reads = [0] * N_SENSORS
        self.errors = [0] * N_SENSORS
        self._callbacks = []
        if use_int:
            for k, pin in enumerate(INT_PINS):
                lgpio.gpio_claim_alert(self.h, pin, lgpio.FALLING_EDGE, lgpio.SET_PULL_UP)

                def on_edge(chip, gpio, level, tick, k=k):
                    self.flags[k] = True
                    self.int_edges[k] += 1
                self._callbacks.append(lgpio.callback(self.h, pin, lgpio.FALLING_EDGE, on_edge))
        self._ranging = False

    # ------------------------------------------------------------ setup
    def _present(self, addr):
        try:
            self.bus.write_quick(addr)
            return True
        except OSError:
            return False

    def _bring_up(self):
        """LPn sequencing: all low, then raise one at a time and move it to its own address."""
        lgpio, vl53 = self._lgpio, self._vl53
        for pin in LPN_PINS:
            lgpio.gpio_claim_output(self.h, pin, 0)
        time.sleep(0.05)

        sensors = []
        for k in range(N_SENSORS):
            name, target = f"S{k + 1}", ADDRS[k]
            lgpio.gpio_write(self.h, LPN_PINS[k], 1)
            time.sleep(0.02)

            if self._present(target):          # moved in an earlier run, no power cycle since
                how = "already at"
            elif self._present(DEFAULT_ADDR):
                s = vl53.VL53L5CX(i2c_addr=DEFAULT_ADDR, i2c_dev=self.bus, skip_init=True)
                if not s.set_i2c_address(target):
                    sys.exit(f"{name}: failed to move 0x29 -> 0x{target:02x}")
                how = "moved to"
            else:
                msg = (f"{name}: nothing at 0x29 or 0x{target:02x} with LPn GPIO{LPN_PINS[k]} high. "
                       f"Check its LPn wire, power and SDA/SCL.")
                if not self.allow_missing:
                    sys.exit(msg)
                self.log(f"{msg}  -> MISSING, its zones will read as invalid")
                lgpio.gpio_write(self.h, LPN_PINS[k], 0)
                sensors.append(None)
                continue

            t0 = time.monotonic()
            s = vl53.VL53L5CX(i2c_addr=target, i2c_dev=self.bus, skip_init=True)
            if not s.init():
                sys.exit(f"{name}: firmware init failed at 0x{target:02x}")
            s.set_resolution(self.zones)
            s.set_ranging_frequency_hz(self.hz)
            s.set_target_order(vl53.TARGET_ORDER_CLOSEST)
            self.log(f"{name}: LPn GPIO{LPN_PINS[k]:<2}  {how} 0x{target:02x}  init {time.monotonic() - t0:.1f} s")
            sensors.append(s)
        return sensors

    # ------------------------------------------------------------ run
    @property
    def missing(self) -> list[int]:
        return [k for k, s in enumerate(self.sensors) if s is None]

    def start(self):
        for s in self.sensors:
            if s is not None:
                s.start_ranging()
        self._ranging = True

    def frames(self):
        """Yield combined frames. A frame is emitted once all six have a new reading,
        or after 2 periods (so one dead sensor can't freeze the stream)."""
        if not self._ranging:
            self.start()
        z = self.zones
        dist = [[-1] * z for _ in range(N_SENSORS)]
        stat = [[0] * z for _ in range(N_SENSORS)]
        sigma = [[0] * z for _ in range(N_SENSORS)]    # range_sigma_mm: the chip's own error estimate
        signal = [[0] * z for _ in range(N_SENSORS)]   # signal_per_spad (kcps/SPAD): return strength
        active = [k for k, s in enumerate(self.sensors) if s is not None]
        fresh = [False] * N_SENSORS
        now = time.monotonic()
        last_read = [now] * N_SENSORS
        last_emit = now
        while True:
            now = time.monotonic()
            for k, s in enumerate(self.sensors):
                if s is None:
                    continue
                # Read on an INT edge; if INT is off or broken, poll once the sensor is overdue.
                if not (not self.use_int or self.flags[k] or now - last_read[k] > 3 * self.period):
                    continue
                self.flags[k] = False
                try:
                    if not s.data_ready():
                        continue
                    data = s.get_data()
                except (RuntimeError, OSError):
                    self.errors[k] += 1
                    continue
                dist[k] = list(data.distance_mm[0][:z])
                stat[k] = list(data.target_status[0][:z])
                sigma[k] = list(data.range_sigma_mm[0][:z])
                signal[k] = list(data.signal_per_spad[0][:z])
                fresh[k] = True
                self.reads[k] += 1
                last_read[k] = now

            if all(fresh[k] for k in active) or (any(fresh) and now - last_emit > 2 * self.period):
                fresh = [False] * N_SENSORS
                last_emit = now
                yield {"t": round(time.time(), 4), "res": self.res,
                       "d": [d[:] for d in dist], "s": [s_[:] for s_ in stat],
                       "sigma": [x[:] for x in sigma], "signal": [x[:] for x in signal]}
            else:
                time.sleep(0.001)

    def close(self):
        for s in self.sensors:
            if s is None:
                continue
            try:
                s.stop_ranging()
            except Exception:
                pass
        for cb in self._callbacks:
            cb.cancel()
        self._lgpio.gpiochip_close(self.h)  # LPn lines are released (may float); addresses kept until power-off

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

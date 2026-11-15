#!/usr/bin/env python3
"""Ping each of the six VL53L5CX boards through its own LPn line. Pi only, takes ~1 s.

For every sensor: hold all LPn low, raise only this one, find it at 0x29 (or its
assigned 0x30-0x35 if it was already moved since power-up), read the chip ID
(0xF0 / 0x02), and check its INT line idles high. No firmware upload, no address change.

Run from sensing_pi/:  .venv/bin/python tests/multi6/tof6_ping.py
"""
import sys
import time
from pathlib import Path

import lgpio
from smbus2 import SMBus, i2c_msg

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sensors.pins import ADDRS, DEFAULT_ADDR, INT_PINS, LPN_PINS, N_SENSORS  # noqa: E402

PARK_ADDR = 0x3E   # spare address for a board that ignores its LPn line


def present(bus, addr):
    try:
        bus.write_quick(addr)
        return True
    except OSError:
        return False


def chip_id(bus, addr):
    """Same check as the ST driver's is_alive: page 0, read regs 0x0000/0x0001, back to page 2."""
    def wr(reg, val):
        bus.i2c_rdwr(i2c_msg.write(addr, [reg >> 8, reg & 0xFF, val]))

    def rd(reg):
        w, r = i2c_msg.write(addr, [reg >> 8, reg & 0xFF]), i2c_msg.read(addr, 1)
        bus.i2c_rdwr(w, r)
        return list(r)[0]
    wr(0x7FFF, 0x00)
    ids = (rd(0x0000), rd(0x0001))
    wr(0x7FFF, 0x02)
    return ids


def main():
    h = lgpio.gpiochip_open(0)
    bus = SMBus(1)
    for pin in LPN_PINS:
        lgpio.gpio_claim_output(h, pin, 0)
    for pin in INT_PINS:
        lgpio.gpio_claim_input(h, pin, lgpio.SET_PULL_UP)
    time.sleep(0.05)

    # Boards that still answer with every LPn low are not controlled by their LPn wire.
    # Park them on a spare address (until power-off) so they can't answer for the others.
    parked = []
    for a in [DEFAULT_ADDR] + ADDRS:
        if present(bus, a):
            try:
                chip_id(bus, a)
                bus.i2c_rdwr(i2c_msg.write(a, [0x7F, 0xFF, 0x00]))
                bus.i2c_rdwr(i2c_msg.write(a, [0x00, 0x04, PARK_ADDR]))   # VL53L5CX address register
                bus.i2c_rdwr(i2c_msg.write(PARK_ADDR, [0x7F, 0xFF, 0x02]))
            except OSError:
                pass
            parked.append(a)
    if parked:
        still = present(bus, DEFAULT_ADDR)
        print(f"WARNING: with every LPn low, {[hex(a) for a in parked]} still answered -> a board's LPn is not "
              f"controlled by its GPIO. Parked it at {hex(PARK_ADDR)} (until power-off)"
              f"{'; but 0x29 still answers: more than one such board' if still else ''}.\n")

    ok_all = True
    print(f"{'sensor':<7}{'LPn':<8}{'INT':<8}{'address':<9}{'chip id':<12}{'INT now':<9}result")
    for k in range(N_SENSORS):
        lgpio.gpio_write(h, LPN_PINS[k], 1)
        time.sleep(0.02)
        addr = next((a for a in (ADDRS[k], DEFAULT_ADDR) if present(bus, a)), None)
        cid, ok = "-", False
        if addr is not None:
            try:
                a, b = chip_id(bus, addr)
                cid = f"{a:02X} {b:02X}"
                ok = (a, b) == (0xF0, 0x02)
            except OSError as e:
                cid = f"err {e.errno}"
        int_level = lgpio.gpio_read(h, INT_PINS[k])
        if ok:
            res = "OK"
        elif addr is None:
            res = "NOT FOUND" + (" <- likely the board with the bad LPn wire (parked)" if parked else "")
        else:
            res = "WRONG ID"
        ok_all &= ok
        print(f"S{k + 1:<6}GPIO{LPN_PINS[k]:<4}GPIO{INT_PINS[k]:<4}{(hex(addr) if addr else '-'):<9}{cid:<12}"
              f"{('high' if int_level else 'low'):<9}{res}")
        lgpio.gpio_write(h, LPN_PINS[k], 0)
        time.sleep(0.01)
    if parked:
        try:
            print(f"\nparked board at {hex(PARK_ADDR)}: chip id {' '.join(f'{v:02X}' for v in chip_id(bus, PARK_ADDR))}")
        except OSError:
            print(f"\nparked board at {hex(PARK_ADDR)}: no answer")
    print("(INT level is informational: it only means something once the sensor is ranging; "
          "tof6_record.py reports INT edges per sensor.)")

    for pin in LPN_PINS:            # leave every board enabled, as after power-up
        lgpio.gpio_write(h, pin, 1)
    lgpio.gpiochip_close(h)
    if ok_all and not parked:
        print("\nall six found, each controlled by its own LPn")
    else:
        print("\nNOT all good: a missing sensor -> check its VIN/GND, SDA/SCL; a parked board -> check its LPn wire")
    sys.exit(0 if ok_all and not parked else 1)


if __name__ == "__main__":
    main()

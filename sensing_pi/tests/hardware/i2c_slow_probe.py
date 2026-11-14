"""Slow software-I2C probe on GPIO2/3 (open-drain emulation, Pi's 1.8k pull-ups).
Tries normal and swapped SDA/SCL at ~5 kHz; reports which addresses ACK.
Rules out bus speed / wire length when `i2cdetect -y 1` (1 MHz) finds nothing.
Puts GPIO2/3 back into I2C mode (alt0) when done.

Run from sensing_pi/: .venv/bin/python tests/hardware/i2c_slow_probe.py
"""
import subprocess
import time
import lgpio

h = lgpio.gpiochip_open(0)
D = 0.0001  # ~5 kHz


def rel(p):
    lgpio.gpio_claim_input(h, p)


def low(p):
    lgpio.gpio_claim_output(h, p, 0)


def rd(p):
    return lgpio.gpio_read(h, p)


def probe(sda, scl, addr):
    rel(sda); rel(scl); time.sleep(D)
    low(sda); time.sleep(D); low(scl); time.sleep(D)        # START
    byte = addr << 1                                          # write
    for i in range(7, -1, -1):
        (rel if (byte >> i) & 1 else low)(sda); time.sleep(D)
        rel(scl); time.sleep(D); low(scl); time.sleep(D)
    rel(sda); time.sleep(D); rel(scl); time.sleep(D)
    ack = rd(sda) == 0
    low(scl); time.sleep(D)
    low(sda); time.sleep(D); rel(scl); time.sleep(D); rel(sda); time.sleep(D)  # STOP
    return ack


try:
    for p in (2, 3):
        rel(p)
    time.sleep(0.01)
    print(f"idle levels: GPIO2={rd(2)} GPIO3={rd(3)} (both should be 1)")
    for name, sda, scl in (("normal  SDA=GPIO2 SCL=GPIO3", 2, 3), ("swapped SDA=GPIO3 SCL=GPIO2", 3, 2)):
        found = [hex(a) for a in range(0x08, 0x78) if probe(sda, scl, a)]
        print(f"{name}: {found or 'no ACKs'}")
finally:
    lgpio.gpio_free(h, 2); lgpio.gpio_free(h, 3)
    lgpio.gpiochip_close(h)
    subprocess.run(["pinctrl", "set", "2,3", "a0"], check=False)  # back to hardware I2C

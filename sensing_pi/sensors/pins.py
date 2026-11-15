"""Wiring for the six VL53L5CX boards (BCM numbering). Chart: docs/wiring.md."""
N_SENSORS = 6
LPN_PINS = [17, 27, 22, 23, 24, 25]   # S1..S6
INT_PINS = [5, 6, 13, 19, 26, 16]     # S1..S6
ADDRS = [0x30, 0x31, 0x32, 0x33, 0x34, 0x35]
DEFAULT_ADDR = 0x29

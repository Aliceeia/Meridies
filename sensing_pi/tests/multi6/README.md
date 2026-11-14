# 6 × VL53L5CX co-test (2 × 3 grid)

Sensor layout, **as seen from behind the sensors (the way the wearer looks)**:

```
 S1  S2  S3
 S4  S5  S6
```

## Wiring chart

All six Pimoroni breakouts share one I2C bus. Each one gets **its own LPn and its own INT** GPIO.

| Sensor | LPn → GPIO (header pin) | INT → GPIO (header pin) | Address after boot |
|---|---|---|---|
| S1 | GPIO17 (pin 11) | GPIO5  (pin 29) | 0x30 |
| S2 | GPIO27 (pin 13) | GPIO6  (pin 31) | 0x31 |
| S3 | GPIO22 (pin 15) | GPIO13 (pin 33) | 0x32 |
| S4 | GPIO23 (pin 16) | GPIO19 (pin 35) | 0x33 |
| S5 | GPIO24 (pin 18) | GPIO26 (pin 37) | 0x34 |
| S6 | GPIO25 (pin 22) | GPIO16 (pin 36) | 0x35 |

Shared by all six (daisy-chain or a small bus strip):

| Breakout pin | Pi header |
|---|---|
| SDA | pin 3 (GPIO2) |
| SCL | pin 5 (GPIO3) |
| GND | pin 6, 9, 14, 20, 25, 30, 34 or 39 (use 2 or more GND wires) |
| 3-5V | see **Power** |

The pins are kept on the left column (LPn: pins 11, 13, 15) / right column (16, 18, 22) and the bottom of the header (INT: 29–37), so each group is easy to ribbon together. GPIO17 = S1 LPn is the same pin as the single-board test.

### How addressing works
Every board powers up at 0x29. At start, `tof6_record.py` drives all six LPn **low**, which disables I2C on every board. It then raises one LPn at a time and moves that board to 0x30–0x35. Addresses survive until the power is cut, so re-running the script without a power cycle also works.

### Power
Six sensors draw about 6 × ~90 mA ≈ 0.5 A while ranging, which is too much for the Pi's 3.3 V pin. Pimoroni breakouts have their own regulator, so **feed 3-5V from 5 V (pin 2 or 4)**.
Before connecting SDA/SCL to the Pi, power one board from 5 V and measure SDA to GND on the breakout. It must read **≤ 3.3 V** (the pull-ups are on the regulated side). If it reads 5 V, stop and power from 3.3 V instead.

### Pull-ups
Each breakout has its own SDA/SCL pull-ups, and six in parallel plus the Pi's own 1.8 kΩ is a stiff bus. If you get `Remote I/O error` or garbage only with all six connected, look for a pull-up cut-trace on the back of 4–5 of the boards, or drop the bus to 400 kHz (`dtparam=i2c_arm_baudrate=400000`).

### Bus bandwidth
At 8×8, each frame is ~1.4 KB over I2C (~13 ms at 1 MHz). Six sensors share the bus, so the default is **10 Hz each at 8×8** (~80 % bus load). Use `--res 4` for 4×4 at 15–20 Hz.

### Check the wiring is right
1. Run `i2cdetect -y 1` before the script: nothing, or only addresses 0x30–0x35 from a previous run (with the LPn lines floating, boards may show at 0x29; that's fine).
2. Run the recorder. It prints which LPn/address each board came up on and counts INT edges per sensor. A sensor with 0 INT edges has a bad INT wire. It still works, because the script falls back to polling, but fix it.
3. In the viewer, cover each sensor with a finger: the matching tile should go white. If the wrong tile reacts, the LPn wires are swapped.

## Running

On the Pi, from `sensing_pi/`:

```bash
.venv/bin/python tests/multi6/tof6_record.py --csv rec.csv --serve 5557
```

Options: `--res 4|8`, `--hz N`, `--frames N`, `--no-int` (ignore INT, poll over I2C), `--csv FILE`, `--serve PORT`.

On a laptop (needs `pip install numpy matplotlib`), from the repo's `sensing_pi/`:

```bash
python tests/multi6/tof6_view.py --live raspberrypi.local:5557
```

```bash
python tests/multi6/tof6_view.py --file rec.csv
```

Near = white, far = black, invalid zone = dark red. Options: `--max-mm 2000`, `--tile flipx|flipy|rot180|none` to match how the boards are mounted, `--speed 2` for playback.

## Files
- `tof6_common.py`: layout, CSV columns, and the mosaic builder (used on both the Pi and the laptop; no hardware imports).
- `tof6_record.py`: Pi only. Brings up the six sensors, records CSV, and streams JSON lines over TCP.
- `tof6_view.py`: laptop. Grayscale live view or playback.

# 6 × VL53L5CX co-test (2 × 3 grid)

Sensor layout, **as seen from behind the sensors (the way the wearer looks)**:

```
 S1  S2  S3
 S4  S5  S6
```

## Wiring chart

Board: **AITRIP VL53L5CX "V2"** module (generic green board, 6-pin header labelled `VIN GND SCL SDA INT LPn`).
All six share one I2C bus. Each one gets **its own LPn and its own INT** GPIO.

Per board, header pin by header pin:

| Module pin | Goes to |
|---|---|
| VIN | 3.3 V supply (see **Power**, don't use 5 V until you've checked) |
| GND | GND (shared) |
| SCL | Pi pin 5 (GPIO3), shared |
| SDA | Pi pin 3 (GPIO2), shared |
| INT | that sensor's INT GPIO below |
| LPn | that sensor's LPn GPIO below |

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
| VIN | 3.3 V, see **Power** |

The pins are kept on the left column (LPn: pins 11, 13, 15) / right column (16, 18, 22) and the bottom of the header (INT: 29–37), so each group is easy to ribbon together. GPIO17 = S1 LPn is the same pin as the single-board test.

### How addressing works
Every board powers up at 0x29. At start, the raw stream (`main.py`, or `python -m sensors.record`) drives all six LPn **low**, which disables I2C on every board. It then raises one LPn at a time and moves that board to 0x30–0x35. Addresses survive until the power is cut, so re-running the script without a power cycle also works.

### Power
The listing doesn't say whether this generic module has a regulator, and the VL53L5CX itself is a **3.3 V max** part. So:

- **Default: VIN → 3.3 V.** One board can run from Pi pin 1 or 17. Six boards draw about 6 × ~90 mA ≈ 0.5 A while ranging, which is more than the Pi's 3.3 V pins should supply. For all six, use a separate 3.3 V regulator (e.g. an AMS1117-3.3 board fed from Pi 5 V pin 2), with its GND tied to the Pi's GND.
- **5 V only if you've checked.** Look on the back of the module for a small 3- or 5-leg regulator (SOT-23, often marked `662K` or `LLVB`). If there is one, power one board from 5 V *without* SDA/SCL connected to the Pi, and measure SDA and LPn to GND. Only if both read **≤ 3.3 V** is it safe to power the modules from 5 V. If either reads ~5 V, using 5 V would push 5 V into the Pi's GPIO.

### Pull-ups
- **SDA/SCL:** the Pi already has 1.8 kΩ pull-ups on pins 3/5, so the bus works even if the modules have none. If the modules do have them, six in parallel make a stiff bus. If you get `Remote I/O error` only with all six connected, drop to 400 kHz (`dtparam=i2c_arm_baudrate=400000`).
- **LPn:** may have no pull-up on this module. A floating LPn turns I2C off, so **LPn must always be wired**, even for a single-board test. The recorder drives every LPn line, so this is handled while it runs.
- **INT:** the recorder turns on the Pi's internal pull-up, so no external resistor is needed.

### Bus bandwidth
At 8×8, each frame is ~1.4 KB over I2C (~13 ms at 1 MHz). Six sensors share the bus, so the default is **10 Hz each at 8×8** (~80 % bus load). Use `--res 4` for 4×4 at 15–20 Hz.

### Check the wiring is right
1. Run `.venv/bin/python tests/hardware/tof6_ping.py`. It wakes each board through its own LPn and reads its chip ID; it should end with "all six found, each controlled by its own LPn".
2. Run the raw stream (`main.py --raw-only`). It prints which LPn/address each board came up on and counts INT edges per sensor. A sensor with 0 INT edges has a bad INT wire. It still works, because the code falls back to polling, but fix it.
3. In the viewer, cover each sensor with a finger: the matching tile should go white. If the wrong tile reacts, the LPn wires are swapped.

## Running

On the Pi, from `sensing_pi/`:

```bash
.venv/bin/python main.py --raw-only --csv rec.csv
```

(or the raw stream on its own: `.venv/bin/python -m sensors.record --serve 5557 --csv rec.csv`; options `--res 4|8`, `--hz N`, `--frames N`, `--no-int` (poll over I2C instead of INT), `--allow-missing`.)

On a laptop (needs `pip install numpy matplotlib`), from the repo's `sensing_pi/`:

```bash
python tools/tof6_view.py --live raspberrypi.local:5557
```

```bash
python tools/tof6_view.py --file rec.csv
```

Near = white, far = black, invalid zone = dark red. Options: `--max-mm 2000`, `--tile flipx|flipy|rot180|none` to match how the boards are mounted, `--top-tile ...` for the top row, `--speed 2` for playback.

## Files
- `sensors/frames.py`: layout, CSV columns, and the mosaic builder (used on both the Pi and the laptop; no hardware imports).
- `sensors/record.py`: Pi only. Brings up the six sensors, records CSV, and streams JSON lines over TCP.
- `tools/tof6_view.py`: laptop. Grayscale live view or playback.

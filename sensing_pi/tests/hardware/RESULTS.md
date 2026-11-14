# VL53L5CX single-board bench results

Board: AITRIP VL53L5CX V2 module · Label: **S1** · Tester: ____ · Date: ____
Pi I2C baudrate: 1 MHz / 400 kHz (circle one)

Run from `sensing_pi/`: `.venv/bin/python tests/hardware/vl53l5cx_single.py [options]`
If `i2cdetect -y 1` is empty: `.venv/bin/python tests/hardware/i2c_slow_probe.py` (slow probe, normal and swapped SDA/SCL)

## 1. Detect and init
| Check | Result |
|---|---|
| `i2cdetect -y 1` shows 29 | |
| Init (firmware upload) time | s |

## 2. Frame rate
| Mode | Set Hz | Measured Hz |
|---|---|---|
| 8x8 | 15 | |
| 4x4 | 20 | |
| 4x4 | 30 | |

## 3. Wall accuracy (`--frames 100 --no-grid --csv wall_XXX.csv`, sensor square to a flat wall)
| True distance | Centre mean (mm) | Std (mm) | Invalid % | Offset (true − mean) |
|---|---|---|---|---|
| 500 mm | | | | |
| 1000 mm | | | | |
| 2000 mm | | | | |

## 4. Direction
Hand moved from the wearer's left to right at ~50 cm: the near zone moves from column __ to column __.
So column 0 = wearer's **left / right** (circle one) with the board mounted pins-down.

## 5. Thin object and range
| Test | Result |
|---|---|
| Broom handle at 1 m: zones that see it | |
| Max valid range indoors (wall) | mm |
| Near a bright window: max valid range | mm |

## 6. Optional: LPn
LPn (GPIO17) pulled low → 0x29 disappears from `i2cdetect`: yes / no

## Notes

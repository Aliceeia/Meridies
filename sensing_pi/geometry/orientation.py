"""Head orientation -> world frame. The MPU6050 source is a stub for now.

World frame: gravity-aligned, z up; x/y are the head frame's forward/right at yaw 0
(yaw is relative to wherever the head pointed at start-up: the MPU6050 has no magnetometer).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from .transform import basis


@dataclass
class HeadOrientation:
    yaw_deg: float = 0.0     # + = head turned right
    pitch_deg: float = 0.0   # + = looking up
    roll_deg: float = 0.0    # + = right ear down


def head_to_world(xyz, o: HeadOrientation) -> np.ndarray:
    """Rotate head-frame points (N, 3) into the world frame (origin stays at the head centre)."""
    return np.asarray(xyz, dtype=float) @ basis(o.yaw_deg, o.pitch_deg, o.roll_deg).T


def world_to_head(xyz, o: HeadOrientation) -> np.ndarray:
    return np.asarray(xyz, dtype=float) @ basis(o.yaw_deg, o.pitch_deg, o.roll_deg)


class OrientationSource(ABC):
    @abstractmethod
    def read(self) -> HeadOrientation:
        ...

    def calibrate_level(self, seconds: float = 2.0) -> None:
        """Wearer holds the head level and still: store the offsets that make this read (0, 0, 0)."""


class LevelHead(OrientationSource):
    """Assumes the head is level and facing forward. Use until the MPU6050 is wired."""

    def read(self) -> HeadOrientation:
        return HeadOrientation()


class MPU6050Source(OrientationSource):
    """TODO: MPU6050 on I2C bus 1 (0x68; 0x69 if AD0 is high), mounted flat at the back of the crown.

    To implement:
      - wake the chip (PWR_MGMT_1 = 0), set gyro +-500 dps and accel +-4 g, DLPF ~44 Hz
      - read accel + gyro at ~100 Hz in a background thread
      - gyro bias: average ~2 s while still (in calibrate_level)
      - complementary filter: pitch/roll from gravity, yaw from integrated gyro z (it will drift)
      - map the chip axes to head axes (x forward, y right, z up) with a fixed matrix
      - calibrate_level: store pitch/roll offsets with the head level, zero the yaw
    """

    def __init__(self, bus: int = 1, address: int = 0x68):
        raise NotImplementedError("MPU6050 support not written yet; use LevelHead()")

    def read(self) -> HeadOrientation:
        raise NotImplementedError

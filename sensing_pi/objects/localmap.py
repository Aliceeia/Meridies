"""Step 3 (optional): short-term local map. Keeps recent points in the world frame so head
movement sweeps the sensors across the scene and fills the gaps between their views."""
from __future__ import annotations

from collections import deque

from .scan import ScanPoint


class LocalMap:
    def __init__(self, window_s: float):
        self.window_s = window_s
        self._frames: deque[tuple[float, list[ScanPoint]]] = deque()

    def add(self, t: float, points: list[ScanPoint]) -> None:
        self._frames.append((t, points))
        while self._frames and t - self._frames[0][0] > self.window_s:
            self._frames.popleft()

    def points(self) -> list[ScanPoint]:
        return [p for _, pts in self._frames for p in pts]

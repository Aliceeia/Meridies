"""Hardware-free scene simulator for the ToF rigs."""
from .scene import SCENES, Plane, Pole, render, scene_shapes

__all__ = ["Plane", "Pole", "render", "scene_shapes", "SCENES"]

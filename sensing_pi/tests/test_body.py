"""Checks for the body zone (wearer's shoulders/torso/arms). Run from sensing_pi/:
    .venv/bin/python tests/test_body.py           (or all tests: .venv/bin/python tests/run_all.py)
(also works under pytest)."""
import numpy as np

from fixtures import MEASURED_CAP, ROOT, cap_rig, pinned_cfg
from geometry import load_mounts
from objects import ObjectPipeline, load_cfg
from objects.body import BodyZone
from sim.scene import Plane, Pole, render

RIG = cap_rig(MEASURED_CAP)
CFG = pinned_cfg(RIG)
ARMS = [Pole(0.03, 0.27, r=0.05, z_min=-0.9, z_max=-0.15), Pole(0.03, -0.27, r=0.05, z_min=-0.9, z_max=-0.15)]


def cfg_with(enabled):
    return {**CFG, "body_zone": {**CFG["body_zone"], "enabled": enabled}}


def test_cap_turns_it_on_and_default_is_off():
    assert CFG["body_zone"]["enabled"] is True
    assert load_cfg()["body_zone"]["enabled"] is False
    assert not load_cfg(rig=load_mounts(ROOT / "config" / "flat_mounts.json"))["body_zone"]["enabled"]


def test_inside_boxes_and_mirror():
    bz = BodyZone(CFG)
    pts = np.array([
        [0.0, 0.0, -0.40],    # chest            -> body
        [0.0, 0.22, -0.16],   # right shoulder   -> body
        [0.1, 0.30, -0.50],   # right arm        -> body (arm box)
        [0.1, -0.30, -0.50],  # left arm         -> body (mirrored box)
        [0.6, 0.0, -0.40],    # 60 cm in front   -> not body
        [0.0, 0.0, 0.05],     # above shoulders  -> not body
        [0.1, 0.60, -0.45],   # 60 cm to the side (e.g. a table) -> not body
    ])
    assert bz.inside(pts).tolist() == [True, True, True, True, False, False, False]


def test_disabled_is_a_no_op():
    assert not BodyZone(cfg_with(False)).inside(np.zeros((3, 3))).any()


def test_arms_warn_without_body_zone_and_vanish_with_it():
    for on, expect_warning in ((False, True), (True, False)):
        p = ObjectPipeline(RIG, cfg_with(on))
        for i in range(3):
            _, w = p.step(render(RIG, ARMS, t=i / 10))
        assert bool(w) == expect_warning, (on, w)


def test_real_obstacle_next_to_the_arm_still_warns():
    """A wall 60 cm to the right is outside the body boxes, so it is kept."""
    p = ObjectPipeline(RIG, cfg_with(True))
    for i in range(3):
        objs, _ = p.step(render(RIG, ARMS + [Plane((0, 0.6, 0), (0, 1, 0))], t=i / 10))
    assert any(o.az > 45 and 0.45 < o.dist < 0.8 for o in objs), [o.to_dict() for o in objs]


def test_wall_40cm_beside_you_is_untouched():
    """Boxes stay within ~34 cm of the centre line, so a wall 40 cm to the side comes out exactly
    the same with the body zone on as with it off (nothing of it is cut away)."""
    out = {}
    for on in (False, True):
        p = ObjectPipeline(RIG, cfg_with(on))
        for i in range(3):
            objs, _ = p.step(render(RIG, [Plane((0, 0.4, 0), (0, 1, 0))], t=i / 10))
        out[on] = [(o.kind, round(o.az, 1), round(o.dist, 3), o.n_bins) for o in objs]
    assert out[True] == out[False] and out[True], out


if __name__ == "__main__":
    tests = [(n, fn) for n, fn in globals().items() if n.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"all {len(tests)} passed")

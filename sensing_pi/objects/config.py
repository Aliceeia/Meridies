"""Pipeline settings: config/objects.json, overridden by the rig json's "objects" section."""
from __future__ import annotations

import copy
import json
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "config" / "objects.json"


def load_cfg(path: str | Path | None = None, rig=None) -> dict:
    cfg = json.loads(Path(path or DEFAULT_PATH).read_text())
    cfg.pop("_comment", None)
    if rig is not None:
        for k, v in (rig.extra.get("objects") or {}).items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = copy.deepcopy(v)
    return cfg

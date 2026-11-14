"""PART 1 sanity check: render sweeps 0-8 at four yaws each -> connect/spike/out/interior_sheet.png

    python connect/spike/interior_sheet.py

Row 10 renders the porch sweep (38, outside the front of the house at +X) at the same yaws,
to read off which way positive yaw turns.
"""
import io
import math
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from connect.backend.interior_render import Renderer, SS_BASE  # noqa: E402

OUT = Path(__file__).resolve().parent / "out"
YAWS = [0.0, math.pi / 2, math.pi, 3 * math.pi / 2]
ROWS = list(range(9)) + [38]
TW, TH = 384, 192

OUT.mkdir(parents=True, exist_ok=True)
font = ImageFont.load_default(size=16) if hasattr(ImageFont, "load_default") else None
sheet = Image.new("RGB", (TW * len(YAWS), TH * len(ROWS)), "black")
failed = []
t0 = time.time()
with Renderer() as r:
    for i, sweep in enumerate(ROWS):
        for j, yaw in enumerate(YAWS):
            try:
                tile = Image.open(io.BytesIO(r.render_view(sweep, yaw))).convert("RGB").resize((TW, TH))
            except Exception as e:  # keep going; a black tile marks the failure
                failed.append((sweep, yaw, repr(e)[:80]))
                tile = Image.new("RGB", (TW, TH), (60, 0, 0))
            d = ImageDraw.Draw(tile)
            d.rectangle((0, 0, 210, 22), fill=(0, 0, 0))
            d.text((4, 2), f"sweep {sweep} (ss={sweep + SS_BASE})  yaw {yaw:.2f}", fill="white", font=font)
            sheet.paste(tile, (TW * j, TH * i))
            print(f"sweep {sweep:2d} yaw {yaw:.2f}  {time.time() - t0:5.0f}s", flush=True)
sheet.save(OUT / "interior_sheet.png")
print("wrote", OUT / "interior_sheet.png", "| failed:", failed or "none")

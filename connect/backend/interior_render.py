"""First-person interior renders from the PUBLIC Matterport showcase (no API key, no SDK).

    from connect.backend.interior_render import Renderer
    with Renderer() as r:
        png = r.render_view(sweep_index=0, yaw_rad=1.57)

Deep-link params (all verified empirically on zvKHR9DHNjJ, 2026-11-14):
  ss = sweep index + 1  -> ss is 1-BASED. ss=0 is ignored (default start view); ss=39 is index 38
                           (the porch, the one outlying sweep). Checked against sweep positions.
  sr = <pitch>,<yaw>    -> radians, PITCH FIRST: sr=1.5,0 looks at the ceiling, sr=0,1.5 turns.
  qs=1                  -> skip the intro flythrough (sr still applies).
We wait until the WebGL canvas stops changing AND shows real texture (never a fixed sleep;
the loading screen is static and dark, so stability alone is not enough), crop the logo and
control bar that the hide-UI flags leave behind, and cache by (model, ss, sr).

Runs the installed Chrome headless with SwiftShader (software WebGL).
"""
from __future__ import annotations

import io
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "connect" / "data" / "cache" / "interior"
MODEL = "zvKHR9DHNjJ"
VIEW_W, VIEW_H = 1280, 800
# Fraction of the frame trimmed after hiding UI: top (logo/address) and bottom (control bar).
CROP_TOP, CROP_BOTTOM = 0.10, 0.10
HIDE_UI = "qs=1&play=1&help=0&hl=0&brand=0&title=0&dh=0&gt=0&hr=0&mt=0&f=0&fp=0&vr=0&search=0&nt=0&pin=0&portal=0&tourcta=0&wh=0&lang=en"
SS_BASE = 1  # ss = sweep index + SS_BASE


def sr_string(yaw: float, pitch: float = 0.0) -> str:
    return f"{pitch:.3f},{yaw:.3f}"  # pitch first


def view_url(model: str, ss: int, sr: str) -> str:
    return f"https://my.matterport.com/show/?m={model}&ss={ss}&sr={sr}&{HIDE_UI}"


def _thumb(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("L").resize((96, 60), Image.BILINEAR), float)


class Renderer:
    def __init__(self, model: str = MODEL, timeout_s: float = 60.0):
        self.model, self.timeout_s = model, timeout_s

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chrome", headless=True, args=[
            "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def cache_path(self, ss: int, sr: str) -> Path:
        return CACHE / f"{self.model}_ss{ss}_sr{sr}.png"

    def render_view(self, sweep_index: int, yaw_rad: float, pitch_rad: float = 0.0, *, url_sr: str | None = None) -> bytes:
        sr = url_sr or sr_string(yaw_rad, pitch_rad)
        ss = sweep_index + SS_BASE
        path = self.cache_path(ss, sr)
        if path.exists():
            return path.read_bytes()
        page = self._browser.new_page(viewport={"width": VIEW_W, "height": VIEW_H})
        try:
            page.goto(view_url(self.model, ss, sr), wait_until="domcontentloaded", timeout=60_000)
            page.wait_for_selector("canvas", timeout=30_000)
            png = self._wait_stable(page)
        finally:
            page.close()
        img = Image.open(io.BytesIO(png)).convert("RGB")
        top, bottom = int(img.height * CROP_TOP), int(img.height * (1 - CROP_BOTTOM))
        out = io.BytesIO()
        img.crop((0, top, img.width, bottom)).save(out, "PNG", optimize=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_bytes(out.getvalue())
        return out.getvalue()

    def _wait_stable(self, page, poll=0.7, still_needed=4, min_s=3.0, diff_tol=0.8, dark_tol=40.0, texture_tol=25.0) -> bytes:
        """Poll the frame until it stops changing and looks like a real scene. Raises on timeout."""
        t0, prev, still = time.monotonic(), None, 0
        while time.monotonic() - t0 < self.timeout_s:
            time.sleep(poll)
            png = page.screenshot(type="png")
            th = _thumb(png)
            scene = th.mean() > dark_tol and th.std() > texture_tol  # loading screen: dark, flat
            if prev is not None and scene and np.abs(th - prev).mean() < diff_tol:
                still += 1
                if still >= still_needed and time.monotonic() - t0 >= min_s:
                    return png
            else:
                still = 0
            prev = th
        raise TimeoutError(f"frame never settled within {self.timeout_s:.0f}s")

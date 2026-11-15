#!/usr/bin/env python3
"""Interactive top-down simulator: drag obstacles around the listener and hear them
through the real audio engine (mixer.Mixer: tones, tempo, HRTF, cues). Use headphones.

    python sim_view.py [--device NAME]

Mouse / keys (click the plot first so it has keyboard focus):
    drag a dot          move that obstacle
    double-click        add an obstacle there (max 3, same as the engine)
    right-click a dot   remove it
    o                   orbit: the selected obstacle circles you (again to stop)
    space               mute / unmute

The warning list sent to the mixer mirrors sensing_pi: nearest first, max 3,
nothing beyond its warn range (1.0 m) unless that box is unticked. The plot is the
horizontal plane; the elevation slider lifts/lowers every obstacle, so the
straight-line distance sent is horiz / cos(el). Colours show what the mixer does:
blue = hum only, orange = beeps (horizontal <= 0.65 m),
red = beeps + urgent cue (straight-line <= 0.25 m).
"""
import argparse
import math
import threading

import matplotlib.pyplot as plt
import numpy as np
import sounddevice as sd
from matplotlib.widgets import CheckButtons, Slider

import mixer as M
from mixer import Mixer, SoundBank

BLOCKSIZE = 256
R = 2.5                 # plot radius, m
PICK_M = 0.2            # how close a click must be to grab a dot
WARN_RANGE_M = 1.0              # sensing_pi sends objects out to its warn_dist_m
BEEP_M = M.BEEP_DIST_M          # mixer: beeps start here ...
URGENT_M = M.URGENT_DIST_M      # ... and the urgent cue joins here
COLORS = {"urgent": "#d62728", "beep": "#ff7f0e", "hum": "#1f77b4", "silent": "#9e9e9e"}


class Sim:
    def __init__(self, device, swap_lr=False):
        self.bank = SoundBank()
        self.mixer = Mixer(self.bank)
        self.mixer.swap_lr = swap_lr
        self.lock = threading.Lock()
        self.warnings = []
        self.muted = False
        self.obs = [{"id": "A", "x": 0.0, "y": 0.4}]   # x = right, y = forward (m)
        self.next_id = 1
        self.sel = 0
        self.drag = None
        self.orbit = False
        self.el = 0.0
        self.gate = True

        self.stream = sd.OutputStream(samplerate=self.mixer.sr, blocksize=BLOCKSIZE, channels=2,
                                      dtype="float32", callback=self._audio, device=device)
        self._build_ui()
        self._publish()

    # ---------- audio thread ----------
    def _audio(self, outdata, frames, time_info, status):
        with self.lock:
            w = [] if self.muted else self.warnings
        self.mixer.update_warnings(w)
        outdata[:] = self.mixer.render_block(frames)

    # ---------- model ----------
    @staticmethod
    def polar(o):
        dist = math.hypot(o["x"], o["y"])
        az = math.degrees(math.atan2(o["x"], o["y"]))   # 0 = ahead, + right
        return az, dist

    def straight(self, horiz):
        """The plot is the horizontal plane; with the elevation slider the
        object is above/below it, so the sensor's straight-line distance is longer."""
        return horiz / math.cos(math.radians(self.el))

    def level(self, horiz):
        if self.straight(horiz) <= URGENT_M:
            return "urgent"
        if horiz <= BEEP_M:
            return "beep"
        if self.gate and self.straight(horiz) > WARN_RANGE_M:
            return "silent"
        return "hum"

    def _publish(self):
        ws = []
        for o in self.obs:
            az, horiz = self.polar(o)
            lv = self.level(horiz)
            if lv != "silent":
                ws.append({"id": o["id"], "az": round(az, 1), "el": self.el,
                           "dist": round(max(self.straight(horiz), 0.05), 3),
                           "level": "urgent" if lv == "urgent" else "warn"})
        ws.sort(key=lambda w: w["dist"])
        with self.lock:
            self.warnings = ws[:M.NUM_SLOTS]
        self._redraw()

    # ---------- UI ----------
    def _build_ui(self):
        self.fig = plt.figure(figsize=(8.5, 7.5))
        self.fig.canvas.manager.set_window_title("Meridies sound simulator")
        self.ax = self.fig.add_axes([0.05, 0.12, 0.68, 0.83])
        ax = self.ax
        ax.set_xlim(-R, R)
        ax.set_ylim(-R, R)
        ax.set_aspect("equal")
        ax.set_xlabel("right (m)")
        ax.set_ylabel("forward (m)")
        for r, ls in ((URGENT_M, "-"), (BEEP_M, "-"), (WARN_RANGE_M, "--"), (1.5, ":"), (2.0, ":")):
            ax.add_patch(plt.Circle((0, 0), r, fill=False, ls=ls, lw=0.8, color="grey"))
            ax.text(r * 0.72, r * 0.72, f"{r} m", fontsize=7, color="grey")
        for deg in range(0, 360, 45):
            a = math.radians(deg)
            ax.plot([0, R * math.sin(a)], [0, R * math.cos(a)], lw=0.4, color="#dddddd", zorder=0)
            ax.text(1.08 * R * math.sin(a) * 0.9, 1.08 * R * math.cos(a) * 0.9,
                    f"{(deg + 180) % 360 - 180:+d}°", fontsize=7, color="#999999", ha="center", va="center")
        # listener: head + nose pointing forward
        ax.add_patch(plt.Circle((0, 0), 0.12, color="black", zorder=3))
        ax.plot([0, 0], [0.12, 0.24], color="black", lw=3, zorder=3)
        ax.text(0, -0.3, "you", ha="center", fontsize=8)
        self.dots, self.labels, self.rays = [], [], []
        self.status = self.fig.text(0.05, 0.03, "", fontsize=9, family="monospace")

        cax = self.fig.add_axes([0.76, 0.6, 0.22, 0.24])
        cax.set_title("cues / layers", fontsize=9)
        names = list(M.CUES)
        self.checks = CheckButtons(cax, names, [M.CUES[n] for n in names])
        self.checks.on_clicked(self._toggle_cue)
        gax = self.fig.add_axes([0.76, 0.5, 0.22, 0.08])
        self.gate_btn = CheckButtons(gax, [f"silent > {WARN_RANGE_M} m"], [self.gate])
        self.gate_btn.on_clicked(self._toggle_gate)
        sax = self.fig.add_axes([0.80, 0.14, 0.03, 0.3])
        self.slider = Slider(sax, "elev °", -40, 60, valinit=0, orientation="vertical")
        self.slider.on_changed(self._set_el)
        self.fig.text(0.76, 0.96, "drag dots · dbl-click add\nright-click remove\n'o' orbit · space mute",
                      fontsize=8, va="top")

        c = self.fig.canvas
        c.mpl_connect("button_press_event", self._press)
        c.mpl_connect("motion_notify_event", self._motion)
        c.mpl_connect("button_release_event", self._release)
        c.mpl_connect("key_press_event", self._key)
        self.timer = c.new_timer(interval=50)
        self.timer.add_callback(self._tick)
        self.timer.start()

    def _redraw(self):
        for art in self.dots + self.labels + self.rays:
            art.remove()
        self.dots, self.labels, self.rays = [], [], []
        with self.lock:
            sent = {w["id"]: i for i, w in enumerate(self.warnings)}
        for i, o in enumerate(self.obs):
            az, dist = self.polar(o)
            lv = self.level(dist)
            raw = self.straight(dist)
            col = COLORS[lv]
            heard = o["id"] in sent
            self.rays.append(self.ax.plot([0, o["x"]], [0, o["y"]], color=col,
                                          lw=1.2 if heard else 0.6, ls="-" if heard else ":", zorder=1)[0])
            self.dots.append(self.ax.plot(o["x"], o["y"], "o", ms=16 if i == self.sel else 13, color=col,
                                          mec="black" if i == self.sel else col, mew=2, zorder=4)[0])
            voice = f"voice {sent[o['id']] + 1}" if heard else "not sent"
            self.labels.append(self.ax.annotate(
                f"{o['id']}  az {az:+.0f}°  horiz {dist:.2f} m"
                + (f" (line {raw:.2f})" if abs(self.el) >= 1 else "")
                + f"\n{lv} · {voice}", (o["x"], o["y"]),
                xytext=(10, 8), textcoords="offset points", fontsize=8, zorder=5))
        cues = " ".join(n for n, v in M.CUES.items() if v) or "none (plain HRTF)"
        self.status.set_text(f"{len(sent)} sent to engine · cues: {cues} · el {self.el:+.0f}°"
                             f"{' · ORBIT' if self.orbit else ''}{' · MUTED' if self.muted else ''}")
        self.fig.canvas.draw_idle()

    # ---------- events ----------
    def _nearest(self, ev):
        if ev.inaxes is not self.ax or ev.xdata is None or not self.obs:
            return None
        d = [math.hypot(o["x"] - ev.xdata, o["y"] - ev.ydata) for o in self.obs]
        i = int(np.argmin(d))
        return i if d[i] < PICK_M else None

    def _press(self, ev):
        if ev.inaxes is not self.ax:
            return
        i = self._nearest(ev)
        if ev.button == 3 and i is not None:
            self.obs.pop(i)
            self.sel = min(self.sel, len(self.obs) - 1) if self.obs else 0
            self._publish()
        elif ev.dblclick and i is None:
            if len(self.obs) >= M.NUM_SLOTS:
                self.status.set_text(f"max {M.NUM_SLOTS} obstacles (the engine has {M.NUM_SLOTS} voices)")
                self.fig.canvas.draw_idle()
                return
            self.obs.append({"id": chr(ord("A") + self.next_id % 26), "x": ev.xdata, "y": ev.ydata})
            self.next_id += 1
            self.sel = len(self.obs) - 1
            self._publish()
        elif ev.button == 1 and i is not None:
            self.sel = self.drag = i
            self._redraw()

    def _motion(self, ev):
        if self.drag is None or ev.inaxes is not self.ax or ev.xdata is None:
            return
        o = self.obs[self.drag]
        o["x"], o["y"] = float(np.clip(ev.xdata, -R, R)), float(np.clip(ev.ydata, -R, R))
        self._publish()

    def _release(self, ev):
        self.drag = None

    def _key(self, ev):
        if ev.key == "o":
            self.orbit = not self.orbit
            self._redraw()
        elif ev.key == " ":
            self.muted = not self.muted
            self._publish()

    def _tick(self):
        if self.orbit and self.obs and self.drag is None:
            o = self.obs[self.sel]
            az, dist = self.polar(o)
            a = math.radians(az + 360 / 8 * 0.05)        # one full circle every 8 s
            o["x"], o["y"] = dist * math.sin(a), dist * math.cos(a)
            self._publish()

    def _toggle_cue(self, name):
        M.CUES[name] = not M.CUES[name]
        self._redraw()

    def _toggle_gate(self, _):
        self.gate = not self.gate
        self._publish()

    def _set_el(self, v):
        self.el = float(v)
        self._publish()

    def run(self):
        with self.stream:
            plt.show()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", help="output device (substring); see engine.py --list-devices")
    p.add_argument("--swap-lr", action="store_true", help="swap left and right channels (earphones worn the other way round)")
    a = p.parse_args()
    device = None
    if a.device:
        m = [d["name"] for d in sd.query_devices() if a.device.lower() in d["name"].lower()
             and d["max_output_channels"] > 0]
        if not m:
            raise SystemExit(f"no output device matching '{a.device}'")
        device = m[0]
    print("loading tones + KEMAR HRTF...")
    Sim(device, swap_lr=a.swap_lr).run()


if __name__ == "__main__":
    main()

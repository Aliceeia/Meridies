#!/usr/bin/env python3
"""Meridies control window (runs on the LAPTOP; the Pi is assumed to be streaming already).

    python meridies_launcher.py            # from the repo root, in the laptop venv

Modes (pick one; switching stops the previous one):
  * Spatial audio demo      audio/sim_view.py: drag virtual obstacles around you and hear them
  * Sensing (no camera)     sensing_pi/tools/objects_view.py (top-down map) + audio/engine.py (sound)
  * Sensing + camera (Meri) the same, plus voice_agent/agent.py: say "Hey Meri"; it uses the Pi camera
Earphones:
  * Output device           sent to the sound engine / demo / Meri's voice (or the system default)
  * Swap left/right         for earphones worn the other way round (sound engine and demo)
  * Test left/right         plays a tone in the LEFT ear, then the RIGHT ear, with the current settings

Needs (laptop venv): pip install -r audio/requirements.txt -r voice_agent/requirements.txt matplotlib
Meri also needs voice_agent/.env with GEMINI_API_KEY (see sensing_pi/docs/laptop_agent_guide.md).
"""
from __future__ import annotations

import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = Path.home() / ".meridies_launcher.json"
DEFAULT_PI = "172.20.10.5"
DEFAULT_DEVICE = "System default"

MODES = {
    "off": "Off",
    "demo": "Spatial audio demo",
    "sensing": "Sensing (no camera)",
    "camera": "Sensing + camera (Meri)",
}


# ---------------------------------------------------------------------- what to run (no GUI)
def build_commands(mode: str, s: dict, py: str = sys.executable) -> list[tuple[str, list[str], Path]]:
    """(name, argv, cwd) for every program a mode needs. s = settings dict:
    pi, out_device, mic_device, swap_lr."""
    pi = s.get("pi") or DEFAULT_PI
    dev = s.get("out_device")
    dev = None if not dev or dev == DEFAULT_DEVICE else dev
    mic = s.get("mic_device")
    mic = None if not mic or mic == DEFAULT_DEVICE else mic
    sound_opts = (["--device", dev] if dev else []) + (["--swap-lr"] if s.get("swap_lr") else [])

    if mode == "demo":
        return [("demo", [py, "sim_view.py", *sound_opts], ROOT / "audio")]
    cmds = []
    if mode in ("sensing", "camera"):
        cmds.append(("map", [py, str(ROOT / "sensing_pi" / "tools" / "objects_view.py"), "--live", f"{pi}:5558"], ROOT))
        cmds.append(("sound", [py, "engine.py", "--endpoint", f"tcp://{pi}:5556", *sound_opts], ROOT / "audio"))
    if mode == "camera":
        agent = [py, "agent.py", "--camera", f"http://{pi}:8090/snapshot.jpg", "--lidar", f"tcp://{pi}:5556"]
        if dev:
            agent += ["--speaker", dev]
        if mic:
            agent += ["--mic", mic]
        cmds.append(("meri", agent, ROOT / "voice_agent"))
    return cmds


def preflight(mode: str, s: dict) -> list[str]:
    """Problems worth showing before a mode starts (empty list = all good)."""
    pi = s.get("pi") or DEFAULT_PI
    msgs = []
    if mode in ("demo", "sensing", "camera") and not (ROOT / "audio" / "sounds" / "manifest.json").exists():
        msgs.append("audio/sounds is missing: will run audio/tone_render.py once first")
    if mode in ("sensing", "camera"):
        for port, what in ((5556, "warnings"), (5558, "object map")):
            if not port_open(pi, port):
                msgs.append(f"can't reach the Pi's {what} on {pi}:{port} (is main.py running on the Pi? same hotspot?)")
    if mode == "camera":
        if not (ROOT / "voice_agent" / ".env").exists():
            msgs.append("voice_agent/.env is missing (GEMINI_API_KEY): copy it from the Pi, see the laptop guide")
        if not http_ok(f"http://{pi}:8090/health"):
            msgs.append(f"the Pi camera at http://{pi}:8090 doesn't answer (camera_server.py on the Pi)")
    return msgs


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def list_devices() -> tuple[list[str], list[str]]:
    """(output device names, input device names), each starting with 'System default'."""
    outs, ins = [DEFAULT_DEVICE], [DEFAULT_DEVICE]
    try:
        import sounddevice as sd
        for d in sd.query_devices():
            if d["max_output_channels"] > 0 and d["name"] not in outs:
                outs.append(d["name"])
            if d["max_input_channels"] > 0 and d["name"] not in ins:
                ins.append(d["name"])
    except Exception:  # noqa: BLE001  (sounddevice missing or no audio system)
        pass
    return outs, ins


def load_settings() -> dict:
    s = {"pi": DEFAULT_PI, "out_device": DEFAULT_DEVICE, "mic_device": DEFAULT_DEVICE, "swap_lr": False}
    try:
        s.update(json.loads(SETTINGS_FILE.read_text()))
    except Exception:  # noqa: BLE001
        pass
    return s


def save_settings(s: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=1))
    except OSError:
        pass


# ---------------------------------------------------------------------- child programs
class Child:
    def __init__(self, name: str, argv: list[str], cwd: Path, out: queue.Queue):
        self.name = name
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, text=True, bufsize=1, **kw)
        threading.Thread(target=self._pump, args=(out,), daemon=True).start()

    def _pump(self, out):
        for line in self.proc.stdout:
            out.put(f"[{self.name}] {line.rstrip()}")
        out.put(f"[{self.name}] exited ({self.proc.wait()})")

    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        if not self.running():
            return
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(self.proc.pid, signal.SIGINT)
            self.proc.wait(3)
        except Exception:  # noqa: BLE001
            pass
        if self.running():
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def play_lr_test(device: str | None, swap_lr: bool) -> str:
    """0.4 s tone in the LEFT ear, pause, 0.4 s higher tone in the RIGHT ear (after the swap setting)."""
    import numpy as np
    import sounddevice as sd
    sr = 44100
    t = np.arange(int(0.4 * sr)) / sr
    fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)
    left = 0.3 * np.sin(2 * np.pi * 440 * t) * fade
    right = 0.3 * np.sin(2 * np.pi * 660 * t) * fade
    gap = np.zeros(int(0.25 * sr))
    stereo = np.zeros((len(t) * 2 + len(gap), 2), dtype=np.float32)
    stereo[:len(t), 0] = left                    # left ear first (low tone)
    stereo[len(t) + len(gap):, 1] = right         # then right ear (high tone)
    if swap_lr:
        stereo = np.ascontiguousarray(stereo[:, ::-1])
    sd.play(stereo, sr, device=device)
    sd.wait()
    return "played: LOW tone should be in your LEFT ear, then HIGH tone in your RIGHT ear"


# ---------------------------------------------------------------------- window
def run_gui():
    import tkinter as tk
    from tkinter import scrolledtext, ttk

    s = load_settings()
    outs, ins = list_devices()
    log_q: queue.Queue = queue.Queue()
    children: list[Child] = []
    state = {"mode": "off"}

    root = tk.Tk()
    root.title("Meridies")
    root.geometry("760x620")
    pad = {"padx": 8, "pady": 4}

    def log(msg):
        log_q.put(f"[launcher] {msg}")

    # Pi address
    top = ttk.Frame(root)
    top.pack(fill="x", **pad)
    ttk.Label(top, text="Pi address:").pack(side="left")
    pi_var = tk.StringVar(value=s["pi"])
    ttk.Entry(top, textvariable=pi_var, width=16).pack(side="left", padx=6)
    ttk.Button(top, text="Check Pi feeds", command=lambda: check_feeds()).pack(side="left", padx=4)
    ttk.Button(top, text="Open camera view",
               command=lambda: webbrowser.open(f"http://{pi_var.get().strip()}:8090/")).pack(side="left", padx=4)

    # mode toggle
    mode_box = ttk.LabelFrame(root, text="Mode")
    mode_box.pack(fill="x", **pad)
    mode_var = tk.StringVar(value="off")
    for key, label in MODES.items():
        ttk.Radiobutton(mode_box, text=label, value=key, variable=mode_var,
                        command=lambda: switch_mode(mode_var.get())).pack(side="left", padx=10, pady=6)

    # earphones
    ear = ttk.LabelFrame(root, text="Earphones")
    ear.pack(fill="x", **pad)
    ttk.Label(ear, text="Sound output:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
    out_var = tk.StringVar(value=s["out_device"] if s["out_device"] in outs else DEFAULT_DEVICE)
    ttk.Combobox(ear, textvariable=out_var, values=outs, width=48, state="readonly").grid(row=0, column=1, sticky="w")
    ttk.Label(ear, text="Microphone (Meri):").grid(row=1, column=0, sticky="w", padx=6, pady=3)
    mic_var = tk.StringVar(value=s["mic_device"] if s["mic_device"] in ins else DEFAULT_DEVICE)
    ttk.Combobox(ear, textvariable=mic_var, values=ins, width=48, state="readonly").grid(row=1, column=1, sticky="w")
    swap_var = tk.BooleanVar(value=bool(s["swap_lr"]))
    ttk.Checkbutton(ear, text="Swap left / right", variable=swap_var).grid(row=2, column=1, sticky="w", pady=3)
    btns = ttk.Frame(ear)
    btns.grid(row=0, column=2, rowspan=3, padx=10)
    ttk.Button(btns, text="Test left/right", command=lambda: test_lr()).pack(fill="x", pady=2)
    ttk.Button(btns, text="Apply (restart mode)", command=lambda: switch_mode(state["mode"], force=True)).pack(fill="x", pady=2)
    if len(outs) == 1:
        ttk.Label(ear, text="(sounddevice not available: only the system default can be used)",
                  foreground="gray").grid(row=3, column=1, sticky="w")

    # status + log
    status_var = tk.StringVar(value="Off")
    ttk.Label(root, textvariable=status_var, font=("TkDefaultFont", 11, "bold")).pack(anchor="w", **pad)
    logbox = scrolledtext.ScrolledText(root, height=20, state="disabled", font=("TkFixedFont", 9))
    logbox.pack(fill="both", expand=True, **pad)

    def settings_now() -> dict:
        cur = {"pi": pi_var.get().strip() or DEFAULT_PI, "out_device": out_var.get(),
               "mic_device": mic_var.get(), "swap_lr": bool(swap_var.get())}
        save_settings(cur)
        return cur

    def stop_all():
        for c in children:
            if c.running():
                log(f"stopping {c.name}")
            c.stop()
        children.clear()

    def start_mode(mode, cur):
        if mode != "off" and not (ROOT / "audio" / "sounds" / "manifest.json").exists():
            log("making the sounds once (audio/tone_render.py)...")
            r = subprocess.run([sys.executable, "tone_render.py"], cwd=ROOT / "audio", capture_output=True, text=True)
            log("sounds ready" if r.returncode == 0 else f"tone_render failed: {r.stdout[-300:]}{r.stderr[-300:]}")
        for msg in preflight(mode, cur):
            if "will run" not in msg:
                log("WARNING: " + msg)
        for name, argv, cwd in build_commands(mode, cur):
            log(f"starting {name}: {' '.join(Path(a).name if i == 1 else a for i, a in enumerate(argv[1:], 1))}")
            try:
                children.append(Child(name, argv, cwd, log_q))
            except OSError as e:
                log(f"could not start {name}: {e}")

    def switch_mode(mode, force=False):
        if mode == state["mode"] and not force:
            return
        cur = settings_now()
        stop_all()
        state["mode"] = mode
        mode_var.set(mode)
        status_var.set(f"Starting: {MODES[mode]}..." if mode != "off" else "Off")
        threading.Thread(target=lambda: start_mode(mode, cur), daemon=True).start()

    def check_feeds():
        pi = pi_var.get().strip() or DEFAULT_PI

        def work():
            for port, what in ((5557, "raw frames"), (5556, "warnings"), (5558, "object map")):
                log(f"{what:12s} {pi}:{port}  {'OK' if port_open(pi, port) else 'NOT reachable'}")
            log(f"camera       http://{pi}:8090  {'OK' if http_ok(f'http://{pi}:8090/health') else 'NOT reachable'}")
        threading.Thread(target=work, daemon=True).start()

    def test_lr():
        cur = settings_now()
        dev = None if cur["out_device"] == DEFAULT_DEVICE else cur["out_device"]

        def work():
            try:
                log(play_lr_test(dev, cur["swap_lr"]))
            except Exception as e:  # noqa: BLE001
                log(f"test tone failed: {e}")
        threading.Thread(target=work, daemon=True).start()

    def pump():
        lines = []
        while not log_q.empty():
            lines.append(log_q.get_nowait())
        if lines:
            logbox.configure(state="normal")
            logbox.insert("end", "\n".join(lines) + "\n")
            n = int(logbox.index("end-1c").split(".")[0])
            if n > 800:
                logbox.delete("1.0", f"{n - 800}.0")
            logbox.see("end")
            logbox.configure(state="disabled")
        if state["mode"] != "off":
            run = [c.name for c in children if c.running()]
            dead = [c.name for c in children if not c.running()]
            txt = f"{MODES[state['mode']]}: running {', '.join(run) or '-'}"
            if dead:
                txt += f"   |  stopped: {', '.join(dead)} (see log)"
            status_var.set(txt)
        root.after(200, pump)

    def on_close():
        stop_all()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    log(f"repo: {ROOT}")
    log("pick a mode above; the Pi is assumed to be streaming (sensing_pi/main.py)")
    pump()
    try:
        root.mainloop()
    finally:
        stop_all()


if __name__ == "__main__":
    run_gui()

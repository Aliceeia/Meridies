"""
Runtime audio engine. Subscribes to C's warning list on port 5556 and
drives the real-time mixer through a sounddevice output stream.

All mutation of Mixer state happens on the audio callback thread only; the
ZMQ thread just hands off the latest parsed warning list behind a lock, so
there's no contention inside the realtime callback.

Usage:
    python engine.py                      # connects to tcp://localhost:5556
    python engine.py --endpoint tcp://192.168.1.5:5556
    python engine.py --list-devices
    python engine.py --device "Jerry"     # substring match against device name,
                                            # instead of trusting the system default
"""
import argparse
import json
import threading
import time

import sounddevice as sd
import zmq

from mixer import Mixer, SoundBank

BLOCKSIZE = 256  # ~5.8ms at 44.1kHz; plan targets <200ms end-to-end, this is a small slice of that


class WarningReceiver:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self._lock = threading.Lock()
        self._latest = []
        self._last_msg_time = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.connect(self.endpoint)
        sub.setsockopt_string(zmq.SUBSCRIBE, "")
        sub.setsockopt(zmq.RCVTIMEO, 200)  # so we can check _stop periodically
        print(f"engine: subscribing to {self.endpoint}")
        while not self._stop.is_set():
            try:
                msg = sub.recv_string()
            except zmq.Again:
                continue
            try:
                warnings = json.loads(msg)
            except json.JSONDecodeError:
                continue
            with self._lock:
                self._latest = warnings
                self._last_msg_time = time.time()

    def latest(self):
        with self._lock:
            return self._latest, self._last_msg_time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="tcp://localhost:5556")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--device", default=None,
                         help="substring to match against an output device name "
                              "(e.g. 'Jerry') — explicit, doesn't depend on the "
                              "system default, which a long-running stream won't "
                              "follow if it changes after startup anyway")
    args = parser.parse_args()

    if args.list_devices:
        print(sd.query_devices())
        return

    device = None
    if args.device:
        matches = [d for d in sd.query_devices()
                   if args.device.lower() in d["name"].lower() and d["max_output_channels"] > 0]
        if not matches:
            print(f"engine: no output device matching '{args.device}' — run --list-devices")
            return
        device = matches[0]["name"]
        print(f"engine: using output device '{device}'")

    bank = SoundBank()
    mixer = Mixer(bank)
    receiver = WarningReceiver(args.endpoint)
    receiver.start()

    STALE_AFTER_S = 0.5  # if C's feed goes silent/dies, fail safe to no audio
    errors = [0]

    def callback(outdata, frames, time_info, status):
        if status:
            print(status)
        warnings, last_msg_time = receiver.latest()
        if warnings and (time.time() - last_msg_time) > STALE_AFTER_S:
            warnings = []  # fail safe: stop beeping about possibly-stale obstacle positions
        try:
            mixer.update_warnings(warnings)
            outdata[:] = mixer.render_block(frames)
        except Exception as e:  # never leave the buffer unfilled (= a click/noise burst)
            outdata.fill(0)
            errors[0] += 1
            if errors[0] == 1 or errors[0] % 100 == 0:
                print(f"engine: render error #{errors[0]} (block muted): {e!r}")

    print(f"engine: {mixer.sr} Hz, blocksize={BLOCKSIZE} ({BLOCKSIZE/mixer.sr*1000:.1f} ms/block)")
    with sd.OutputStream(
        samplerate=mixer.sr,
        blocksize=BLOCKSIZE,
        channels=2,
        dtype="float32",
        callback=callback,
        device=device,
    ):
        print("engine: running. Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nengine: stopped")

    receiver.stop()


if __name__ == "__main__":
    main()

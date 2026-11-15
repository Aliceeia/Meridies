"""Runs ON THE PI. Serves the latest forward-facing camera frame as JPEG.

    python camera_server.py                 # http://0.0.0.0:8090/snapshot.jpg
    python camera_server.py --source usb:/dev/video0 --rotate 180

Endpoints:
    GET /snapshot.jpg   newest frame (JPEG, ~640-1024 px wide)
    GET /health         {"ok": true, "source": ..., "age_s": ...}

Backends, tried in order unless --source is given:
    picam            Raspberry Pi Camera Module via picamera2 (CSI ribbon)
    usb:/dev/videoN  any UVC webcam via ffmpeg (v4l2)

Deliberately NOT on ports 5555-5558 (owned by sensing_pi).
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8090


class Camera:
    def __init__(self, source: str | None, width: int, rotate: int, quality: int):
        self.width, self.rotate, self.quality = width, rotate, quality
        self.lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.t = 0.0
        self.source = source or self._autodetect()
        threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _autodetect() -> str:
        try:
            from picamera2 import Picamera2
            if Picamera2.global_camera_info():
                return "picam"
        except Exception:
            pass
        # first v4l2 device that is a real capture device (the Pi's ISP nodes /dev/video1x are not)
        import glob
        for dev in sorted(glob.glob("/dev/video[0-9]")):
            r = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats"], capture_output=True, text=True)
            if "MJPG" in r.stdout or "YUYV" in r.stdout:
                return f"usb:{dev}"
        raise SystemExit("No camera found: attach a Pi Camera Module (CSI) or a USB webcam, or pass --source")

    def _encode(self, img) -> bytes:
        from PIL import Image
        if not isinstance(img, Image.Image):
            img = Image.fromarray(img)
        img = img.convert("RGB")
        if self.rotate:
            img = img.rotate(-self.rotate, expand=True)
        if img.width > self.width:
            img = img.resize((self.width, round(img.height * self.width / img.width)))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=self.quality)
        return out.getvalue()

    def _publish(self, jpeg: bytes):
        with self.lock:
            self.jpeg, self.t = jpeg, time.time()

    def _loop(self):
        while True:
            try:
                if self.source == "picam":
                    self._picam()
                elif self.source.startswith("usb:"):
                    self._usb(self.source[4:])
                else:
                    raise SystemExit(f"unknown source {self.source}")
            except Exception as e:  # camera unplugged etc: keep retrying, /health reports staleness
                print("camera error:", e, flush=True)
                time.sleep(2)

    def _picam(self):
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_still_configuration(main={"size": (1280, 960), "format": "RGB888"}))
        cam.start()
        print("picamera2 started", flush=True)
        try:
            while True:
                arr = cam.capture_array()[:, :, ::-1]  # picamera2 "RGB888" is BGR in memory
                self._publish(self._encode(arr))
                time.sleep(0.2)
        finally:
            cam.stop(); cam.close()

    def _usb(self, dev: str):
        # MJPEG stream from ffmpeg, split on JPEG SOI/EOI markers
        p = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-f", "v4l2", "-video_size", "1280x720", "-i", dev,
                              "-vf", "fps=5", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
                             stdout=subprocess.PIPE)
        print(f"ffmpeg capturing {dev}", flush=True)
        buf = b""
        try:
            while chunk := p.stdout.read(65536):
                buf += chunk
                while (s := buf.find(b"\xff\xd8")) >= 0 and (e := buf.find(b"\xff\xd9", s)) >= 0:
                    frame, buf = buf[s:e + 2], buf[e + 2:]
                    from PIL import Image
                    self._publish(self._encode(Image.open(io.BytesIO(frame))))
        finally:
            p.kill()
        raise RuntimeError("ffmpeg exited")


def make_handler(cam: Camera):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            with cam.lock:
                jpeg, t = cam.jpeg, cam.t
            if path == "/snapshot.jpg":
                if not jpeg:
                    return self._send(503, b'{"error":"no frame yet"}', "application/json")
                return self._send(200, jpeg, "image/jpeg")
            if path == "/health":
                body = {"ok": jpeg is not None and time.time() - t < 3, "source": cam.source,
                        "age_s": round(time.time() - t, 2) if jpeg else None}
                return self._send(200, json.dumps(body).encode(), "application/json")
            self._send(404, b'{"error":"not found"}', "application/json")
    return H


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="picam | usb:/dev/videoN (default: autodetect)")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--width", type=int, default=1024, help="max JPEG width sent to Gemini")
    ap.add_argument("--rotate", type=int, default=0, choices=(0, 90, 180, 270), help="if the camera is mounted rotated")
    ap.add_argument("--quality", type=int, default=82)
    a = ap.parse_args()
    if a.port in range(5555, 5559):
        raise SystemExit("ports 5555-5558 belong to sensing_pi")
    cam = Camera(a.source, a.width, a.rotate, a.quality)
    print(f"camera server ({cam.source}) on http://0.0.0.0:{a.port}/snapshot.jpg", flush=True)
    ThreadingHTTPServer(("0.0.0.0", a.port), make_handler(cam)).serve_forever()


if __name__ == "__main__":
    main()

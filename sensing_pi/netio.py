"""Tiny TCP fan-out of JSON lines (no zmq needed on the laptop). Used by the recorder and the object debug stream."""
import json
import socket


class JsonLineServer:
    """Every connected client gets one JSON line per send(). Slow or closed clients are dropped."""

    def __init__(self, port: int, name: str = "stream", log=print):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("0.0.0.0", port))
        self.srv.listen()
        self.srv.setblocking(False)
        self.clients = []
        self.log = log
        self.name = name
        log(f"{name} on tcp port {port}")

    def _accept(self):
        while True:
            try:
                c, addr = self.srv.accept()
            except BlockingIOError:
                return
            c.settimeout(0.2)
            c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.clients.append(c)
            self.log(f"{self.name}: client connected from {addr[0]}")

    def send(self, obj):
        self._accept()
        if not self.clients:
            return
        line = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        for c in self.clients[:]:
            try:
                c.sendall(line)
            except OSError:
                self.clients.remove(c)
                c.close()

    def close(self):
        for c in self.clients:
            c.close()
        self.srv.close()

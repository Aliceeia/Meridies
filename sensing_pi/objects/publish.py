"""Outputs: the audio warning list over ZMQ (audio/engine.py SUBs tcp://localhost:5556),
and a full debug stream (all objects + scan points) as TCP JSON lines for tools/objects_view.py."""
from __future__ import annotations

import json

from streams.netio import JsonLineServer


class AudioPublisher:
    """ZMQ PUB, one JSON list per message, exactly what audio/mock_warnings.py sends."""

    def __init__(self, endpoint: str = "tcp://*:5556"):
        import zmq
        self._ctx = zmq.Context.instance()
        self.sock = self._ctx.socket(zmq.PUB)
        self.sock.bind(endpoint)
        self.endpoint = endpoint

    def send(self, warnings: list[dict]) -> None:
        self.sock.send_string(json.dumps(warnings, separators=(",", ":")))

    def close(self) -> None:
        self.sock.close(linger=0)


class DebugPublisher:
    def __init__(self, port: int, log=print):
        self.server = JsonLineServer(port, "object debug stream", log)

    def send(self, t, objs, warnings, points) -> None:
        if not self.server.clients and not self._pending_accept():
            return
        self.server.send({
            "t": t,
            "objects": [o.to_dict() for o in objs],
            "warnings": warnings,
            "points": [[round(p.az, 1), round(p.dist, 3), round(p.el, 1), round(float(p.head[2]), 2)] for p in points],
        })

    def _pending_accept(self) -> bool:
        self.server._accept()
        return bool(self.server.clients)

    def close(self) -> None:
        self.server.close()

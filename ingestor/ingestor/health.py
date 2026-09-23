"""Tiny HTTP health endpoint (stdlib only).

``GET /health`` returns JSON with connection state and counters. Status code is
200 when both MQTT and the database are connected, otherwise 503.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class HealthState:
    started_at: float = field(default_factory=time.time)
    mqtt_connected: bool = False
    db_connected: bool = False
    received: int = 0
    invalid: int = 0
    written: int = 0
    dropped: int = 0
    buffered: int = 0
    threshold_breaches: int = 0
    last_flush_at: float | None = None
    last_error: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                setattr(self, k, v)

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + n)

    def snapshot(self) -> tuple[int, dict[str, Any]]:
        with self._lock:
            healthy = self.mqtt_connected and self.db_connected
            body = {
                "status": "ok" if healthy else "degraded",
                "mqtt_connected": self.mqtt_connected,
                "db_connected": self.db_connected,
                "uptime_s": round(time.time() - self.started_at, 1),
                "messages_received": self.received,
                "messages_invalid": self.invalid,
                "records_written": self.written,
                "records_dropped": self.dropped,
                "records_buffered": self.buffered,
                "threshold_breaches": self.threshold_breaches,
                "last_flush_age_s": None if self.last_flush_at is None else round(time.time() - self.last_flush_at, 1),
                "last_error": self.last_error,
            }
        return (200 if healthy else 503), body


def start_health_server(state: HealthState, port: int, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] not in ("/health", "/healthz"):
                self.send_error(404)
                return
            code, body = state.snapshot()
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format: str, *args: Any) -> None:  # silence access log
            return

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, name="health", daemon=True).start()
    return server

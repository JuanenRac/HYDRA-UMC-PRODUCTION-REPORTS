# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/fake_datalake.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""A real HTTP server implementing HYDRA-UMC-DATALAKE's actual GET
/query response contract (sourceId/kind/field/timestamp/value JSON
objects) - used by this project's own tests to prove datalake_client.py,
reports.py and api.py really parse and use what a real DATALAKE would
send back, over a real socket, not a mocked function call. Not a mock of
DatalakeClient - a real, if minimal, stand-in server for the real one.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class _Handler(BaseHTTPRequestHandler):
    server: "FakeDatalakeServer"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if parsed.path == "/query":
            points = self.server.points
            if "sourceId" in params:
                points = [p for p in points if p["sourceId"] == params["sourceId"]]
            if "kind" in params:
                points = [p for p in points if p["kind"] == params["kind"]]
            if "field" in params:
                points = [p for p in points if p["field"] == params["field"]]
            if "start" in params:
                points = [p for p in points if p["timestamp"] >= int(params["start"])]
            if "end" in params:
                points = [p for p in points if p["timestamp"] <= int(params["end"])]
            points = sorted(points, key=lambda p: p["timestamp"])
            if "limit" in params:
                # Real DATALAKE's own store.py applies LIMIT after its own
                # ASC timestamp ordering - matching that exactly here
                # matters for the truncation-detection tests in
                # test_reports.py, which rely on a limit-truncated result
                # keeping the EARLIEST points and dropping the latest.
                points = points[: int(params["limit"])]
            body = json.dumps(points).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/error":
            self.send_response(500)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


class FakeDatalakeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int]) -> None:
        super().__init__(address, _Handler)
        self.points: list[dict] = []


@contextmanager
def running_fake_datalake() -> Iterator[tuple[str, FakeDatalakeServer]]:
    server = FakeDatalakeServer(("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

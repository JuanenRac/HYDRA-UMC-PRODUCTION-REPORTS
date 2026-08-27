# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Plain JSON/HTTP surface (stdlib http.server) - same convention as
HYDRA-UMC-DATALAKE's and HYDRA-UMC-ANOMALY-DETECTOR's own api.py in this
family. Both endpoints below make a real live call to a running
HYDRA-UMC-DATALAKE instance per request - this project has no store of
its own, it is a real-time query+compute layer over its parent's data.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .availability import AvailabilityError
from .datalake_client import DatalakeClient, DatalakeError
from .oee import OEEError
from .reports import ReportError, availability_from_datalake, oee_from_datalake


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    body = json.dumps(payload, default=lambda o: asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _write_json(handler, status, {"error": message})


def _query_params(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    parsed = urlparse(handler.path)
    return {k: v[0] for k, v in parse_qs(parsed.query).items()}


class Handler(BaseHTTPRequestHandler):
    server: "ReportsServer"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        params = _query_params(self)
        if path == "/reports/oee":
            self._handle_oee(params)
        elif path == "/reports/availability":
            self._handle_availability(params)
        elif path == "/stats":
            _write_json(self, 200, {"datalakeUrl": self.server.client.base_url})
        else:
            _write_error(self, 404, "not found")

    def _handle_oee(self, params: dict[str, str]) -> None:
        required = {"sourceId", "start", "end", "plannedTimeS", "idealCycleTimeS"}
        missing = required - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return
        try:
            report = oee_from_datalake(
                self.server.client,
                source_id=params["sourceId"],
                start_ms=int(params["start"]),
                end_ms=int(params["end"]),
                planned_time_s=float(params["plannedTimeS"]),
                ideal_cycle_time_s=float(params["idealCycleTimeS"]),
            )
        except DatalakeError as e:
            _write_error(self, 502, f"could not read from DATALAKE: {e}")
            return
        except (ReportError, OEEError, ValueError) as e:
            _write_error(self, 400, str(e))
            return
        _write_json(self, 200, report)

    def _handle_availability(self, params: dict[str, str]) -> None:
        required = {"sourceId", "kind", "field", "start", "end", "expectedIntervalMs"}
        missing = required - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return
        try:
            report = availability_from_datalake(
                self.server.client,
                source_id=params["sourceId"],
                kind=params["kind"],
                field=params["field"],
                start_ms=int(params["start"]),
                end_ms=int(params["end"]),
                expected_interval_ms=float(params["expectedIntervalMs"]),
                gap_factor=float(params.get("gapFactor", 3.0)),
            )
        except DatalakeError as e:
            _write_error(self, 502, f"could not read from DATALAKE: {e}")
            return
        except (AvailabilityError, ValueError) as e:
            _write_error(self, 400, str(e))
            return
        _write_json(self, 200, report)


class ReportsServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], client: DatalakeClient) -> None:
        super().__init__(address, Handler)
        self.client = client

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

import hmac
import json
import math
import threading
from dataclasses import asdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .availability import AvailabilityError
from .datalake_client import DatalakeClient, DatalakeError
from .export import export_availability_csv, export_availability_html, export_oee_csv, export_oee_html
from .oee import OEEError
from .reports import ReportError, availability_from_datalake, fleet_oee_from_datalake, oee_from_datalake
from .shift import ShiftError, ShiftSchedule, day_window_ms, shift_window_ms

# How many reports may be built at the same time. Each one can bring up to
# two queries of 200,000 points into memory, so an unbounded number of
# concurrent requests is an out-of-memory risk for the process and a load
# spike for DATALAKE. A request that finds every slot busy answers 503.
DEFAULT_MAX_CONCURRENT_REPORTS = 4


def _json_default(o: object) -> object:
    return asdict(o) if hasattr(o, "__dataclass_fields__") else str(o)  # type: ignore[call-overload]


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: object) -> None:
    # allow_nan=False: a NaN or infinity must fail loudly here, not leave as
    # "NaN" - which is not JSON and breaks strict clients.
    body = json.dumps(payload, default=_json_default, allow_nan=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_csv(handler: BaseHTTPRequestHandler, status: int, csv_text: str) -> None:
    body = csv_text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/csv")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_html(handler: BaseHTTPRequestHandler, status: int, html_text: str) -> None:
    body = html_text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_error(handler: BaseHTTPRequestHandler, status: int, message: str) -> None:
    _write_json(handler, status, {"error": message})


def _query_params(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    parsed = urlparse(handler.path)
    values = parse_qs(parsed.query, keep_blank_values=True)
    repeated = sorted(key for key, value in values.items() if len(value) != 1)
    if repeated:
        raise ValueError(f"query parameters must occur exactly once: {repeated}")
    return {key: value[0] for key, value in values.items()}


class ParamError(ValueError):
    """A request parameter that is missing or has a value no report can use."""


def _number(params: dict[str, str], name: str, *, minimum: float | None = None, positive: bool = False) -> float:
    """One finite number parameter. `float("nan")` and `float("inf")` parse
    happily, and every comparison with NaN is false, so a bare `<= 0` check
    downstream lets them through into the report."""
    raw = params[name]
    try:
        value = float(raw)
    except ValueError:
        raise ParamError(f"{name} must be a number, got {raw!r}") from None
    if not math.isfinite(value):
        raise ParamError(f"{name} must be a finite number, got {raw!r}")
    if positive and value <= 0:
        raise ParamError(f"{name} must be greater than 0")
    if minimum is not None and value < minimum:
        raise ParamError(f"{name} must be at least {minimum}")
    return value


def _integer(params: dict[str, str], name: str) -> int:
    raw = params[name]
    try:
        return int(raw)
    except ValueError:
        raise ParamError(f"{name} must be a whole number, got {raw!r}") from None


def _window(params: dict[str, str]) -> tuple[int, int, float | None]:
    """The report window as (start_ms, end_ms, planned seconds the shift
    implies). Either `start` and `end` (epoch ms) are given, or `day`
    (YYYY-MM-DD) with an optional `shift` index: the window then comes from
    the shift schedule (`shiftLengthHours`, default 8; `firstShiftStartHour`,
    default 0; `tzOffsetMinutes`, default 0 - a fixed offset east of UTC), so
    every report of a plant agrees on where a shift begins and ends."""
    if "day" in params:
        if "start" in params or "end" in params:
            raise ParamError("give either start and end, or day (with an optional shift), not both")
        try:
            day = date.fromisoformat(params["day"])
        except ValueError:
            raise ParamError(f"day must be YYYY-MM-DD, got {params['day']!r}") from None
        offset = int(_number(params, "tzOffsetMinutes")) if "tzOffsetMinutes" in params else 0
        try:
            if "shift" in params:
                schedule = ShiftSchedule(
                    shift_length_hours=_number(params, "shiftLengthHours", positive=True) if "shiftLengthHours" in params else 8.0,
                    first_shift_start_hour=_number(params, "firstShiftStartHour", minimum=0) if "firstShiftStartHour" in params else 0.0,
                    timezone_offset_minutes=offset,
                )
                index = _integer(params, "shift")
                if not 0 <= index < schedule.shifts_per_day:
                    raise ParamError(f"shift must be between 0 and {schedule.shifts_per_day - 1}")
                start_ms, end_ms = shift_window_ms(schedule, day, index)
            else:
                start_ms, end_ms = day_window_ms(day, timezone_offset_minutes=offset)
        except ShiftError as e:
            raise ParamError(str(e)) from e
        return start_ms, end_ms, (end_ms - start_ms) / 1000
    for name in ("start", "end"):
        if name not in params:
            raise ParamError(f"missing required params: ['{name}'] (or 'day')")
    start_ms, end_ms = _integer(params, "start"), _integer(params, "end")
    if end_ms <= start_ms:
        raise ParamError("end must be after start")
    return start_ms, end_ms, None


class Handler(BaseHTTPRequestHandler):
    server: "ReportsServer"

    # Seconds a client may stay silent on its connection before the server
    # drops it, so a client that connects and sends nothing cannot hold a
    # handler thread forever. Socket I/O only; never the time a report takes.
    timeout = 15.0

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass  # quiet by default, same reasoning as this family's other api.py files

    def _authorized(self) -> bool:
        token = self.server.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):].strip().encode("utf-8"), token.encode("utf-8"))

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            # liveness, open so a watchdog can probe it without a credential
            _write_json(self, 200, {"status": "ok", "name": "HYDRA-UMC-PRODUCTION-REPORTS"})
            return
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer")
            body = b'{"error": "a valid Authorization: Bearer token is required"}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            self._route(path)
        except Exception as e:  # noqa: BLE001 - whatever went wrong, the caller gets an answer, not a closed socket
            try:
                _write_error(self, 500, f"internal error: {type(e).__name__}")
            except OSError:
                pass

    def _route(self, path: str) -> None:
        try:
            params = _query_params(self)
        except ValueError as error:
            _write_error(self, 400, str(error))
            return
        if path == "/stats":
            _write_json(self, 200, {"datalakeUrl": self.server.client.base_url})
            return
        routes = {
            "/reports/oee": self._handle_oee,
            "/reports/oee/export": self._handle_oee_export,
            "/reports/oee/fleet": self._handle_oee_fleet,
            "/reports/availability": self._handle_availability,
            "/reports/availability/export": self._handle_availability_export,
        }
        handler = routes.get(path)
        if handler is None:
            _write_error(self, 404, "not found")
            return
        # Each report can bring two big queries into memory: only a few at a time.
        if not self.server.slots.acquire(blocking=False):
            _write_error(self, 503, "too many reports are being built right now; retry shortly")
            return
        try:
            handler(params)
        finally:
            self.server.slots.release()

    def _build_oee_report(self, params: dict[str, str]):
        """Shared real query+compute for both the JSON and CSV-export OEE
        routes, so they can never disagree about what was actually asked
        for. Writes the real error response itself and returns None on
        any failure - callers must check for that before proceeding.
        Returns (report, start_ms, end_ms)."""
        required = {"sourceId", "idealCycleTimeS"}
        if "day" not in params:
            required |= {"plannedTimeS"}
        missing = required - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return None
        try:
            start_ms, end_ms, shift_planned_s = _window(params)
            if "plannedTimeS" in params:
                planned_time_s = _number(params, "plannedTimeS", positive=True)
            else:
                planned_time_s = shift_planned_s  # type: ignore[assignment]  # a shift or a day: the whole of it is planned
            ideal_cycle_time_s = _number(params, "idealCycleTimeS", positive=True)
            report = oee_from_datalake(
                self.server.client,
                source_id=params["sourceId"],
                start_ms=start_ms,
                end_ms=end_ms,
                planned_time_s=planned_time_s,
                ideal_cycle_time_s=ideal_cycle_time_s,
            )
            return report, start_ms, end_ms
        except DatalakeError as e:
            _write_error(self, 502, f"could not read from DATALAKE: {e}")
            return None
        except (ParamError, ReportError, OEEError, ValueError) as e:
            _write_error(self, 400, str(e))
            return None

    def _handle_oee(self, params: dict[str, str]) -> None:
        built = self._build_oee_report(params)
        if built is None:
            return
        _write_json(self, 200, built[0])

    def _handle_oee_fleet(self, params: dict[str, str]) -> None:
        """OEE of several sources over one window and of the group: `sourceIds` is a comma-separated list; the rest is as in /reports/oee."""
        required = {"sourceIds", "idealCycleTimeS"}
        if "day" not in params:
            required |= {"plannedTimeS"}
        missing = required - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return
        try:
            start_ms, end_ms, shift_planned_s = _window(params)
            planned_time_s = _number(params, "plannedTimeS", positive=True) if "plannedTimeS" in params else shift_planned_s
            if planned_time_s is None:
                raise ParamError("plannedTimeS is required")
            ideal_cycle_time_s = _number(params, "idealCycleTimeS", positive=True)
            source_ids = [name.strip() for name in params["sourceIds"].split(",") if name.strip()]
            report = fleet_oee_from_datalake(
                self.server.client,
                source_ids=source_ids,
                start_ms=start_ms,
                end_ms=end_ms,
                planned_time_s=planned_time_s,
                ideal_cycle_time_s=ideal_cycle_time_s,
            )
        except DatalakeError as e:
            _write_error(self, 502, f"could not read from DATALAKE: {e}")
            return
        except (ParamError, ReportError, OEEError, ValueError) as e:
            _write_error(self, 400, str(e))
            return
        _write_json(self, 200, report)

    def _handle_oee_export(self, params: dict[str, str]) -> None:
        # The format is checked before anything is queried: a wrong one is a
        # real, honest 400, not a silent fallback to CSV.
        fmt = params.get("format", "csv")
        if fmt not in ("csv", "html"):
            _write_error(self, 400, f"unknown format: {fmt!r} (expected 'csv' or 'html')")
            return
        built = self._build_oee_report(params)
        if built is None:
            return
        report, start_ms, end_ms = built
        # `format=csv` (the default) or `format=html` - a real,
        # self-contained HTML rendering (embedded SVG bar chart, no
        # external JS/CSS) for viewing straight in a browser.
        if fmt == "html":
            _write_html(self, 200, export_oee_html(report, source_id=params["sourceId"], start_ms=start_ms, end_ms=end_ms))
            return
        _write_csv(self, 200, export_oee_csv(report, source_id=params["sourceId"], start_ms=start_ms, end_ms=end_ms))

    def _build_availability_report(self, params: dict[str, str]):
        """Shared real query+compute for both the JSON and CSV-export
        availability routes - see _build_oee_report for the same
        reasoning. Writes the real error response itself and returns
        None on any failure. Returns (report, start_ms, end_ms)."""
        required = {"sourceId", "kind", "field", "expectedIntervalMs"}
        missing = required - params.keys()
        if missing:
            _write_error(self, 400, f"missing required params: {sorted(missing)}")
            return None
        try:
            start_ms, end_ms, _ = _window(params)
            gap_factor = _number(params, "gapFactor", minimum=1) if "gapFactor" in params else 3.0
            report = availability_from_datalake(
                self.server.client,
                source_id=params["sourceId"],
                kind=params["kind"],
                field=params["field"],
                start_ms=start_ms,
                end_ms=end_ms,
                expected_interval_ms=_number(params, "expectedIntervalMs", positive=True),
                gap_factor=gap_factor,
            )
            return report, start_ms, end_ms
        except DatalakeError as e:
            _write_error(self, 502, f"could not read from DATALAKE: {e}")
            return None
        except (ParamError, ReportError, AvailabilityError, ValueError) as e:
            _write_error(self, 400, str(e))
            return None

    def _handle_availability(self, params: dict[str, str]) -> None:
        built = self._build_availability_report(params)
        if built is None:
            return
        _write_json(self, 200, built[0])

    def _handle_availability_export(self, params: dict[str, str]) -> None:
        fmt = params.get("format", "csv")
        if fmt not in ("csv", "html"):
            _write_error(self, 400, f"unknown format: {fmt!r} (expected 'csv' or 'html')")
            return
        built = self._build_availability_report(params)
        if built is None:
            return
        report, start_ms, end_ms = built
        if fmt == "html":
            _write_html(
                self, 200, export_availability_html(report, source_id=params["sourceId"], start_ms=start_ms, end_ms=end_ms)
            )
            return
        _write_csv(self, 200, export_availability_csv(report, source_id=params["sourceId"], start_ms=start_ms, end_ms=end_ms))


class ReportsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        client: DatalakeClient,
        *,
        token: str | None = None,
        max_concurrent_reports: int = DEFAULT_MAX_CONCURRENT_REPORTS,
    ) -> None:
        super().__init__(address, Handler)
        self.client = client
        self.token = token or None
        self.slots = threading.BoundedSemaphore(max_concurrent_reports)

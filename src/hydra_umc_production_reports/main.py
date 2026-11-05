# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/main.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Entry point for HYDRA-UMC-PRODUCTION-REPORTS.

Real OEE/availability reporting built from a real, live
HYDRA-UMC-DATALAKE instance, no longer just an identity print:
datalake_client.py is a real HTTP client against DATALAKE's own API,
oee.py/availability.py compute the real industry-standard formulas, and
reports.py wires them together against real DATALAKE history. api.py
exposes GET /reports/oee and GET /reports/availability, each making a
real live query against DATALAKE per request.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
import sys

from . import __version__
from .api import DEFAULT_MAX_CONCURRENT_REPORTS, ReportsServer
from .datalake_client import MAX_CACHE_ENTRIES, DatalakeClient
from .event_intake import IntakeConfigError, ProductionEventStore, build_config, config_from_file
from .health_route import health_open_requested
from .server_identity import identity_key_from

PROJECT_NAME = "HYDRA-UMC-PRODUCTION-REPORTS"
ROLE = (
    "Production-Reports - automated KPI/OEE reporting engine, processes "
    "HYDRA-UMC-DATALAKE history into production efficiency reports."
)


def is_loopback_addr(addr: str) -> bool:
    """True for the addresses that cannot be reached from another machine."""
    if addr.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(addr.strip("[]")).is_loopback
    except ValueError:
        return False


def startup_problem(addr: str, token: str | None, allow_anonymous: bool) -> str | None:
    """Why this process must not start: the reports are commercially
    sensitive and each one can load hundreds of thousands of points, so an
    open API on a network address is refused unless that was asked for."""
    if is_loopback_addr(addr) or token or allow_anonymous:
        return None
    return (
        f"refusing to listen on {addr} without a token: set --token (or PRODUCTION_REPORTS_TOKEN), "
        "bind --addr 127.0.0.1, or pass --allow-anonymous to run an open API on purpose"
    )


def intake_problem(enabled: bool, token: str | None, identity_key: bytes | None) -> str | None:
    """Why the intake of production events must not start: it changes what the reports count, so it never runs without an identity."""
    if enabled and not token and identity_key is None:
        return (
            "refusing to accept production events without an identity: set --token (or PRODUCTION_REPORTS_TOKEN) or --identity-key-file "
            "(the signing key of HYDRA-UMC-SERVER)"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydra-umc-production-reports")
    parser.add_argument("--addr", default="127.0.0.1", help="address to bind the HTTP API to")
    parser.add_argument("--port", type=int, default=8099, help="port for the HTTP API")
    parser.add_argument(
        "--datalake-url",
        default="http://localhost:8095",
        help="base URL of the HYDRA-UMC-DATALAKE instance to build reports from",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("PRODUCTION_REPORTS_TOKEN", ""),
        help="bearer token every route requires (default: the PRODUCTION_REPORTS_TOKEN environment variable)",
    )
    parser.add_argument(
        "--health-open",
        action="store_true",
        help="answer GET /health (open to loopback, to other callers only with the bearer token); closed by default, or set HYDRA_UMC_PRODUCTION_REPORTS_HEALTH_OPEN=1",
    )
    parser.add_argument(
        "--allow-anonymous",
        action="store_true",
        help="allow an address other than loopback without a token (an open lab instance)",
    )
    parser.add_argument(
        "--max-concurrent-reports",
        type=int,
        default=DEFAULT_MAX_CONCURRENT_REPORTS,
        help="reports built at the same time; more requests get 503",
    )
    parser.add_argument(
        "--cache-ttl-seconds",
        type=float,
        default=None,
        help="seconds an aggregate of the data lake is reused without asking anything; after that the fingerprint of the series decides "
        "(default: the HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS environment variable, else 30; 0 revalidates every time)",
    )
    parser.add_argument(
        "--job-dispatcher-url",
        default=os.environ.get("PRODUCTION_REPORTS_JOB_DISPATCHER_URL", ""),
        help="base URL of HYDRA-UMC-JOB-DISPATCHER: its robot registry says which cell each robot is in, for /reports/oee/fleet?groupBy=cell "
             "(default: the PRODUCTION_REPORTS_JOB_DISPATCHER_URL environment variable; off when empty)",
    )
    parser.add_argument(
        "--job-dispatcher-token-file",
        default=os.environ.get("PRODUCTION_REPORTS_JOB_DISPATCHER_TOKEN_FILE", ""),
        help="a file whose first line is the bearer token of the job dispatcher (never put the token on the command line)",
    )
    parser.add_argument("--no-cache", action="store_true", help="do not keep the aggregates of the data lake")
    parser.add_argument(
        "--identity-key-file",
        default=None,
        help="file with the signing key of HYDRA-UMC-SERVER (its JWT_SECRET): a valid token of the SERVER is then accepted and nothing is open "
        "(default: the HYDRA_UMC_PRODUCTION_REPORTS_IDENTITY_KEY_FILE environment variable; off when empty)",
    )
    parser.add_argument(
        "--accept-production-events",
        action="store_true",
        help="turn on POST /events/production (the ProductionEvent of the shared contract, sent by the job dispatcher) and GET /reports/oee/events; "
        "off by default, and it needs --token or --identity-key-file",
    )
    parser.add_argument("--events-config", default=None, help="JSON file with acceptProductionEvents, maxBytes, ratePerMinute, capacity and storeFile; the options below win over it")
    parser.add_argument("--events-max-bytes", type=int, default=None, help="biggest body of one event (default 4096)")
    parser.add_argument("--events-rate-per-minute", type=float, default=None, help="events accepted per minute, 429 beyond that (default 600)")
    parser.add_argument("--events-capacity", type=int, default=None, help="events kept in memory, the oldest is dropped first (default 100000)")
    parser.add_argument("--events-file", default=None, help="file of JSON lines that keeps the accepted events across restarts (default: memory only)")
    args = parser.parse_args(argv)

    try:
        identity_key = identity_key_from(args.identity_key_file)
        intake = build_config(
            config_from_file(args.events_config) if args.events_config else {},
            enabled_flag=args.accept_production_events,
            max_bytes=args.events_max_bytes,
            rate=args.events_rate_per_minute,
            capacity=args.events_capacity,
            store_file=args.events_file,
        )
    except (ValueError, IntakeConfigError) as exc:
        print(f"[production-reports] fatal: {exc}", file=sys.stderr)
        return 2
    problem = startup_problem(args.addr, args.token, args.allow_anonymous) or intake_problem(intake.enabled, args.token, identity_key)
    if problem:
        print(f"[production-reports] fatal: {problem}", file=sys.stderr)
        return 2
    store = None
    if intake.enabled:
        try:
            from .contract_events import _sdk

            _sdk()
            store = ProductionEventStore(intake.capacity, intake.store_file)
        except (ValueError, OSError) as exc:
            print(f"[production-reports] fatal: the production events: {exc}", file=sys.stderr)
            return 2

    print(f"{PROJECT_NAME} v{__version__}")
    print(ROLE)

    client = DatalakeClient(args.datalake_url, cache_ttl_seconds=args.cache_ttl_seconds, cache_entries=0 if args.no_cache else MAX_CACHE_ENTRIES)
    dispatcher_token = ""
    if args.job_dispatcher_token_file:
        try:
            with open(args.job_dispatcher_token_file, encoding="utf-8") as handle:
                dispatcher_token = handle.readline().strip()
        except OSError as exc:
            print(f"[production-reports] fatal: --job-dispatcher-token-file: {exc.strerror or type(exc).__name__}", file=sys.stderr)
            return 2
    server = ReportsServer(
        (args.addr, args.port),
        client,
        token=args.token,
        max_concurrent_reports=max(1, args.max_concurrent_reports),
        job_dispatcher_url=args.job_dispatcher_url or None,
        job_dispatcher_token=dispatcher_token,
        identity_key=identity_key,
        events=store,
        event_max_bytes=intake.max_bytes,
        event_rate_per_minute=intake.rate_per_minute,
    )
    server.health_open = health_open_requested(args.health_open)
    print(f"[production-reports] HTTP API listening on {args.addr}:{args.port} (datalake={args.datalake_url})")
    print("[production-reports] GET /reports/oee, GET /reports/availability, their /export, GET /stats, GET /health (closed unless --health-open)")
    if args.token:
        print("[production-reports] every route requires a bearer token")
    if identity_key is not None:
        print("[production-reports] tokens of HYDRA-UMC-SERVER accepted, nothing is open")
    if store is not None:
        print(f"[production-reports] POST /events/production on ({store.count()} events kept, max {intake.max_bytes} bytes, {intake.rate_per_minute:g} per minute), GET /reports/oee/events")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("[production-reports] shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())

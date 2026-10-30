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
        help="bearer token every route except /health requires (default: the PRODUCTION_REPORTS_TOKEN environment variable)",
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
    parser.add_argument("--no-cache", action="store_true", help="do not keep the aggregates of the data lake")
    args = parser.parse_args(argv)

    problem = startup_problem(args.addr, args.token, args.allow_anonymous)
    if problem:
        print(f"[production-reports] fatal: {problem}", file=sys.stderr)
        return 2

    print(f"{PROJECT_NAME} v{__version__}")
    print(ROLE)

    client = DatalakeClient(args.datalake_url, cache_ttl_seconds=args.cache_ttl_seconds, cache_entries=0 if args.no_cache else MAX_CACHE_ENTRIES)
    server = ReportsServer(
        (args.addr, args.port), client, token=args.token, max_concurrent_reports=max(1, args.max_concurrent_reports)
    )
    print(f"[production-reports] HTTP API listening on {args.addr}:{args.port} (datalake={args.datalake_url})")
    print("[production-reports] GET /reports/oee, GET /reports/availability, their /export, GET /stats, GET /health")
    if args.token:
        print("[production-reports] every route except /health requires a bearer token")
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

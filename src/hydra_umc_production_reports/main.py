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
import sys

from . import __version__
from .api import ReportsServer
from .datalake_client import DatalakeClient

PROJECT_NAME = "HYDRA-UMC-PRODUCTION-REPORTS"
ROLE = (
    "Production-Reports - automated KPI/OEE reporting engine, processes "
    "HYDRA-UMC-DATALAKE history into production efficiency reports."
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
    args = parser.parse_args(argv)

    print(f"{PROJECT_NAME} v{__version__}")
    print(ROLE)

    client = DatalakeClient(args.datalake_url)
    server = ReportsServer((args.addr, args.port), client)
    print(f"[production-reports] HTTP API listening on {args.addr}:{args.port} (datalake={args.datalake_url})")
    print("[production-reports] GET /reports/oee, GET /reports/availability, GET /stats")
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

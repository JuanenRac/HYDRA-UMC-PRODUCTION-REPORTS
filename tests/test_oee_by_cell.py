# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_oee_by_cell.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""OEE of a fleet grouped by cell: the mapping, the robot registry of the job dispatcher and the report."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fake_datalake import running_fake_datalake
from test_api import _get
from test_fleet_oee import cycles, fleet

from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.cells import (
    UNASSIGNED,
    CellMapError,
    group_by_cell,
    parse_cell_map,
    robot_cells,
)
from hydra_umc_production_reports.datalake_client import DatalakeClient


def test_the_cell_map_is_read_and_its_mistakes_are_refused() -> None:
    assert parse_cell_map("r1:cell-a, r2:cell-a,r3:cell-b") == {"r1": "cell-a", "r2": "cell-a", "r3": "cell-b"}
    assert parse_cell_map("") == {}
    for bad in ("r1", "r1:", ":cell", "r1:a,r1:b", "r1:a/b", "x" * 129 + ":a"):
        with pytest.raises(CellMapError):
            parse_cell_map(bad)


@contextmanager
def fake_dispatcher(robots, expect_token: str = ""):
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("Authorization", ""))
            if expect_token and self.headers.get("Authorization") != f"Bearer {expect_token}":
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps(robots).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: ANN002
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", seen
    finally:
        server.shutdown()
        server.server_close()


def test_the_robot_registry_gives_the_cell_of_each_robot() -> None:
    robots = [
        {"ID": "r1", "Location": "cell-a", "Tool": "PnP"},
        {"id": "r2", "location": " cell-b "},
        {"ID": "r3", "Location": ""},
        {"ID": "r4"},
        "junk",
    ]
    with fake_dispatcher(robots, expect_token="tok") as (url, seen):
        assert robot_cells(url, "tok") == {"r1": "cell-a", "r2": "cell-b"}
        assert seen == ["Bearer tok"]
        with pytest.raises(CellMapError, match="could not be read"):
            robot_cells(url, "wrong")
    with pytest.raises(CellMapError):
        robot_cells("http://127.0.0.1:1")
    with fake_dispatcher({"not": "a list"}) as (url, _):
        with pytest.raises(CellMapError, match="not a list"):
            robot_cells(url)


def test_the_fleet_is_grouped_by_cell_and_nothing_is_dropped() -> None:
    with running_fake_datalake() as (url, fake):
        fake.points = cycles("r1", [True] * 5) + cycles("r2", [True, True, False, False, False]) + cycles("r3", [True] * 5)
        result = fleet(url, ["r1", "r2", "r3", "r4", "r5"])
    cells = group_by_cell(result, {"r1": "cell-a", "r2": "cell-a", "r3": "cell-b", "r4": "cell-b"}, planned_time_s=10.0, ideal_cycle_time_s=2.0)
    assert list(cells) == ["cell-a", "cell-b", UNASSIGNED]
    a = cells["cell-a"]
    assert a["sources"] == ["r1", "r2"] and a["combined"]["total_count"] == 10 and a["combined"]["good_count"] == 7
    assert a["bottleneck"]["sourceId"] == "r2"
    b = cells["cell-b"]
    assert b["sources"] == ["r3", "r4"] and "r4" in b["failed"] and b["combined"]["total_count"] == 5
    # r5 has no cell and no data: it is still in the report, in the cell nobody assigned
    assert cells[UNASSIGNED]["sources"] == ["r5"] and cells[UNASSIGNED]["combined"] is None


def _server(datalake_url: str, **kwargs) -> ReportsServer:
    server = ReportsServer(("127.0.0.1", 0), DatalakeClient(datalake_url), **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


QUERY = "start=0&end=5000&plannedTimeS=10&idealCycleTimeS=2"


def test_the_endpoint_groups_by_cell_from_the_parameter_and_from_the_dispatcher() -> None:
    robots = [{"ID": "r1", "Location": "cell-a"}, {"ID": "r2", "Location": "cell-b"}]
    with running_fake_datalake() as (datalake_url, fake), fake_dispatcher(robots) as (dispatcher_url, _):
        fake.points = cycles("r1", [True] * 5) + cycles("r2", [True, False, False, False, False])
        plain = _server(datalake_url)
        with_dispatcher = _server(datalake_url, job_dispatcher_url=dispatcher_url)
        try:
            base = f"http://127.0.0.1:{plain.server_address[1]}"
            # without groupBy the answer is the one it always was
            status, body = _get(f"{base}/reports/oee/fleet?sourceIds=r1,r2&{QUERY}")
            assert status == 200 and "byCell" not in body
            # the parameter wins and needs no dispatcher
            status, body = _get(f"{base}/reports/oee/fleet?sourceIds=r1,r2&{QUERY}&groupBy=cell&cells=r1:x,r2:x")
            assert status == 200 and list(body["byCell"]) == ["x"] and body["cellBottleneck"] is None
            # no parameter and no dispatcher: refused, not guessed
            assert _get(f"{base}/reports/oee/fleet?sourceIds=r1,r2&{QUERY}&groupBy=cell")[0] == 400
            assert _get(f"{base}/reports/oee/fleet?sourceIds=r1,r2&{QUERY}&groupBy=cell&cells=r1")[0] == 400
            # the registry of the dispatcher gives the cells
            other = f"http://127.0.0.1:{with_dispatcher.server_address[1]}"
            status, body = _get(f"{other}/reports/oee/fleet?sourceIds=r1,r2&{QUERY}&groupBy=cell")
            assert status == 200 and list(body["byCell"]) == ["cell-a", "cell-b"]
            assert body["cellBottleneck"]["cell"] == "cell-b"
            assert body["cellBottleneck"]["weakestFactor"] == "quality"
        finally:
            for server in (plain, with_dispatcher):
                server.shutdown()
                server.server_close()

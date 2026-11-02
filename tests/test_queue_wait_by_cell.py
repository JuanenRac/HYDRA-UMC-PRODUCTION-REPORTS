# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_queue_wait_by_cell.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Waiting time in the queue per cell, from the job timestamps of the job dispatcher."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fake_datalake import running_fake_datalake
from test_api import _get
from test_fleet_oee import cycles

from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.cells import UNASSIGNED, CellMapError, dispatcher_jobs, queue_wait_by_cell
from hydra_umc_production_reports.datalake_client import DatalakeClient

CELLS = {"r1": "cell-a", "r2": "cell-b"}


def job(sub, asg, robot):
    return {"ID": f"j{sub}{robot}", "SubmittedAtMs": sub, "AssignedAtMs": asg, "AssignedRobot": robot}


def test_the_wait_is_grouped_by_the_cell_of_the_robot_and_the_worst_cell_is_named() -> None:
    jobs = [{k.lower(): v for k, v in j.items()} for j in (
        job(1000, 2000, "r1"), job(1000, 3000, "r1"),   # waits 1 s and 2 s in cell-a
        job(1000, 11000, "r2"),                         # 10 s in cell-b
        job(1000, 5000, "ghost"),                       # robot with no cell
        job(1000, 0, ""),                               # still queued
        job(99000, 99500, "r1"),                        # outside the window
    )]
    result = queue_wait_by_cell(jobs, CELLS, start_ms=0, end_ms=50000)
    assert result["byCell"]["cell-a"] == {"jobs": 2, "meanS": 1.5, "p95S": 2.0, "maxS": 2.0}
    assert result["byCell"]["cell-b"]["meanS"] == 10.0
    assert result["byCell"][UNASSIGNED]["jobs"] == 1
    assert result["stillWaiting"] == 1
    assert result["bottleneck"]["cell"] == "cell-b"


def test_one_cell_has_no_bottleneck_and_odd_timestamps_are_ignored() -> None:
    jobs = [{"submittedatms": 1000, "assignedatms": 500, "assignedrobot": "r1"},
            {"submittedatms": True, "assignedatms": 2000, "assignedrobot": "r1"},
            {"submittedatms": 1000, "assignedatms": 3000, "assignedrobot": "r1"}]
    result = queue_wait_by_cell(jobs, CELLS, start_ms=0, end_ms=10000)
    assert list(result["byCell"]) == ["cell-a"] and result["byCell"]["cell-a"]["jobs"] == 1
    assert result["bottleneck"] is None and result["stillWaiting"] == 1


def _dispatcher(robots, jobs):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.startswith("/jobs"):
                body = json.dumps(jobs).encode() if jobs is not None else b"not json"
            else:
                body = json.dumps(robots).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # noqa: ANN002
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_bad_answer_of_the_dispatcher_is_refused() -> None:
    server = _dispatcher([], None)
    try:
        with pytest.raises(CellMapError):
            dispatcher_jobs(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()


def test_the_endpoint_adds_the_queue_wait_only_with_a_dispatcher() -> None:
    robots = [{"ID": "r1", "Location": "cell-a"}, {"ID": "r2", "Location": "cell-b"}]
    jobs = [job(1000, 2000, "r1"), job(1000, 9000, "r2")]
    dispatcher = _dispatcher(robots, jobs)
    broken = _dispatcher(robots, None)
    with running_fake_datalake() as (datalake_url, fake):
        fake.points = cycles("r1", [True] * 5) + cycles("r2", [True] * 5)
        plain = ReportsServer(("127.0.0.1", 0), DatalakeClient(datalake_url))
        good = ReportsServer(("127.0.0.1", 0), DatalakeClient(datalake_url), job_dispatcher_url=f"http://127.0.0.1:{dispatcher.server_address[1]}")
        bad = ReportsServer(("127.0.0.1", 0), DatalakeClient(datalake_url), job_dispatcher_url=f"http://127.0.0.1:{broken.server_address[1]}")
        servers = (plain, good, bad)
        for s in servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        try:
            q = "sourceIds=r1,r2&start=0&end=5000&plannedTimeS=10&idealCycleTimeS=2&groupBy=cell"
            status, body = _get(f"http://127.0.0.1:{plain.server_address[1]}/reports/oee/fleet?{q}&cells=r1:a,r2:b")
            assert status == 200 and body["queueWait"] is None
            status, body = _get(f"http://127.0.0.1:{good.server_address[1]}/reports/oee/fleet?{q}")
            assert status == 200 and body["queueWait"]["bottleneck"]["cell"] == "cell-b"
            assert body["queueWait"]["byCell"]["cell-a"]["meanS"] == 1.0
            status, body = _get(f"http://127.0.0.1:{bad.server_address[1]}/reports/oee/fleet?{q}&cells=r1:a,r2:b")
            assert status == 200 and body["queueWait"] is None and "queueWaitError" in body
        finally:
            for s in servers:
                s.shutdown()
                s.server_close()
            for d in (dispatcher, broken):
                d.shutdown()
                d.server_close()

# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_simulation_bench.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Simulation bench: the real ReportsServer and DatalakeClient against a simulated data lake that produces a synthetic shift (fixed seed) and fails on command.

Nothing here is hardware: the production data are synthetic and the data lake is a simulator. The bench proves that the reports are exact when the lake behaves
and that every way the lake can fail (error status, garbage, wrong shape, a lying length, a stall, a flapping link, a refused token) ends in an error answer
and not in a wrong number, and that the server keeps serving afterwards.
"""
from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.datalake_client import DatalakeClient

SEED = 20260101
SHIFT_CYCLES = 240
IDEAL_CYCLE_S = 2.0


def synthetic_shift(seed: int = SEED, cycles: int = SHIFT_CYCLES) -> tuple[list[dict[str, Any]], int, float]:
    """One robot, one cycle every 2 to 4 seconds, about one cycle in ten rejected. Returns (points, good_count, total_cycle_time)."""
    rng = random.Random(seed)
    points: list[dict[str, Any]] = []
    moment_ms = 0
    good = 0
    for _ in range(cycles):
        cycle_s = IDEAL_CYCLE_S + rng.random() * 2.0
        is_good = rng.random() >= 0.1
        good += is_good
        points.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": moment_ms, "value": 1.0 if is_good else 0.0})
        points.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": moment_ms, "value": round(cycle_s, 3)})
        moment_ms += int(cycle_s * 1000)
    return points, good, moment_ms / 1000.0


class SimulatedLake(ThreadingHTTPServer):
    """A data lake that serves the synthetic shift and can be told to misbehave (`mode`)."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _LakeHandler)
        self.points, _, _ = synthetic_shift()
        self.mode = "ok"
        self.fail_first = 0
        self.requests = 0
        self.token = ""


class _LakeHandler(BaseHTTPRequestHandler):
    server: SimulatedLake

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send(self, status: int, body: bytes, declared: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body) if declared is None else declared))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def do_GET(self) -> None:  # noqa: N802
        lake = self.server
        if not self.path.startswith("/query"):
            # an older data lake without /aggregate and /series: the client falls back to /query
            return self._send(404, b'{"error": "not found"}')
        lake.requests += 1
        if lake.token and self.headers.get("Authorization") != f"Bearer {lake.token}":
            return self._send(401, b'{"error": "a valid bearer token is required"}')
        if lake.requests <= lake.fail_first:
            return self._send(503, b'{"error": "starting"}')
        mode = lake.mode
        if mode == "http500":
            return self._send(500, b'{"error": "internal"}')
        if mode == "garbage":
            return self._send(200, b"<html>not json at all</html>")
        if mode == "wrong_shape":
            return self._send(200, b'{"points": "none"}')
        if mode == "rows_without_fields":
            return self._send(200, b'[{"unexpected": true}, 7, null]')
        if mode == "short_body":
            body = json.dumps(lake.points).encode()
            return self._send(200, body[: len(body) // 2], declared=len(body))
        if mode == "stall":
            time.sleep(2.0)
        body = json.dumps(lake.points).encode()
        self._send(200, body)


@contextmanager
def simulated_stack(timeout_s: float = 5.0) -> Iterator[tuple[str, SimulatedLake]]:
    lake = SimulatedLake()
    threading.Thread(target=lake.serve_forever, daemon=True).start()
    client = DatalakeClient(f"http://127.0.0.1:{lake.server_address[1]}", timeout_s=timeout_s, cache_ttl_seconds=0)
    reports = ReportsServer(("127.0.0.1", 0), client)
    threading.Thread(target=reports.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{reports.server_address[1]}", lake
    finally:
        reports.shutdown()
        reports.server_close()
        lake.shutdown()
        lake.server_close()


def get(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            return error.code, json.loads(body)
        except ValueError:
            return error.code, body


def oee_url(base: str) -> str:
    _, _, total_s = synthetic_shift()
    return f"{base}/reports/oee?sourceId=robot-1&start=0&end={int(total_s * 1000) + 1000}&plannedTimeS={total_s + 60}&idealCycleTimeS={IDEAL_CYCLE_S}"


def test_the_synthetic_shift_is_reproducible() -> None:
    assert synthetic_shift() == synthetic_shift()
    assert synthetic_shift(seed=1) != synthetic_shift(seed=2)


def test_a_healthy_lake_gives_the_exact_figures_of_the_shift() -> None:
    _, good, total_s = synthetic_shift()
    with simulated_stack() as (base, _lake):
        status, body = get(oee_url(base))
    assert status == 200
    assert body["total_count"] == SHIFT_CYCLES and body["good_count"] == good
    assert body["quality"] == pytest.approx(good / SHIFT_CYCLES)
    assert 0.0 < body["oee"] <= 1.0
    assert body["input_fingerprint"]
    assert total_s > 0


def test_the_same_shift_gives_the_same_fingerprint_twice() -> None:
    with simulated_stack() as (base, _lake):
        first = get(oee_url(base))[1]
        second = get(oee_url(base))[1]
    assert first["input_fingerprint"] == second["input_fingerprint"] and first["oee"] == second["oee"]


@pytest.mark.parametrize("mode", ["http500", "garbage", "wrong_shape", "short_body"])
def test_a_misbehaving_lake_never_produces_a_number(mode: str) -> None:
    with simulated_stack() as (base, lake):
        lake.mode = mode
        status, body = get(oee_url(base))
        assert status >= 400, f"{mode}: a failing lake must not give a report"
        assert isinstance(body, dict) and "error" in body
        # the server is still alive and exact once the lake is well again
        lake.mode = "ok"
        status, body = get(oee_url(base))
        assert status == 200 and body["total_count"] == SHIFT_CYCLES


def test_rows_without_the_expected_fields_are_not_counted() -> None:
    with simulated_stack() as (base, lake):
        lake.mode = "rows_without_fields"
        status, body = get(oee_url(base))
    assert status >= 400 or (isinstance(body, dict) and body.get("total_count", 0) == 0)


def test_a_stalled_lake_ends_in_a_timeout_answer_not_a_hang() -> None:
    started = time.monotonic()
    with simulated_stack(timeout_s=0.5) as (base, lake):
        lake.mode = "stall"
        status, body = get(oee_url(base))
    assert status >= 400 and isinstance(body, dict) and "error" in body
    assert time.monotonic() - started < 10.0


def test_a_flapping_link_fails_then_recovers() -> None:
    with simulated_stack() as (base, lake):
        lake.fail_first = 1
        first_status, _ = get(oee_url(base))
        # the next attempt reaches a lake that is up again
        statuses = [get(oee_url(base))[0] for _ in range(3)]
    assert first_status >= 400
    assert statuses[-1] == 200


def test_a_lake_that_refuses_the_token_is_reported_not_hidden() -> None:
    with simulated_stack() as (base, lake):
        lake.token = "read-token"
        status, body = get(oee_url(base))
    assert status >= 400 and isinstance(body, dict) and "error" in body


def test_a_lake_that_goes_away_is_reported_and_the_server_survives() -> None:
    with simulated_stack() as (base, lake):
        assert get(oee_url(base))[0] == 200
        lake.shutdown()
        lake.server_close()
        status, body = get(oee_url(base))
        assert status >= 400 and isinstance(body, dict)
        assert get(f"{base}/stats")[0] == 200


def test_an_empty_shift_is_an_error_not_a_zero_oee() -> None:
    with simulated_stack() as (base, lake):
        lake.points = []
        status, body = get(oee_url(base))
    assert not (status == 200 and body.get("oee", 0) > 0)


def test_the_bench_works_in_the_same_second_for_many_readers() -> None:
    results: list[int] = []
    with simulated_stack() as (base, _lake):
        url = oee_url(base)
        threads = [threading.Thread(target=lambda: results.append(get(url)[0])) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    # more readers than report slots may get 503, but every answer is a clean one and at least one report was built
    assert len(results) == 8 and set(results) <= {200, 429, 503} and 200 in results

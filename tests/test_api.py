# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_api.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real end-to-end HTTP tests: a real ReportsServer (ThreadingHTTPServer)
talking to a real (if fake) HYDRA-UMC-DATALAKE over a real socket, hit with
real urllib requests - proving the whole chain client -> API -> reports ->
datalake_client -> HTTP works, not just each module in isolation."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from fake_datalake import running_fake_datalake
from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.datalake_client import DatalakeClient


def _get(url: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get_raw(url: str) -> tuple[int, str, str]:
    """Same as _get but for a non-JSON (CSV) response - returns
    (status, content_type, body_text)."""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read().decode("utf-8")


@contextmanager
def running_reports_server(datalake_url: str) -> Iterator[str]:
    client = DatalakeClient(datalake_url)
    server = ReportsServer(("127.0.0.1", 0), client)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_stats_endpoint_reports_the_configured_datalake_url() -> None:
    with running_fake_datalake() as (datalake_url, _fake):
        with running_reports_server(datalake_url) as base_url:
            status, body = _get(f"{base_url}/stats")
            assert status == 200
            assert body["datalakeUrl"] == datalake_url


def test_oee_endpoint_real_round_trip() -> None:
    with running_fake_datalake() as (datalake_url, fake_server):
        points = []
        for i in range(5):
            ts = i * 1000
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0 if i < 4 else 0.0})
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": 2.0})
        fake_server.points = points

        with running_reports_server(datalake_url) as base_url:
            url = (
                f"{base_url}/reports/oee?sourceId=robot-1&start=0&end=5000"
                "&plannedTimeS=10.0&idealCycleTimeS=2.0"
            )
            status, body = _get(url)
            assert status == 200
            assert body["total_count"] == 5
            assert body["good_count"] == 4
            assert abs(body["quality"] - 0.8) < 1e-9
            assert abs(body["oee"] - 0.8) < 1e-9


def test_oee_endpoint_carries_real_formula_version_and_fingerprint() -> None:
    with running_fake_datalake() as (datalake_url, fake_server):
        points = []
        for i in range(5):
            ts = i * 1000
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0})
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": 2.0})
        fake_server.points = points

        with running_reports_server(datalake_url) as base_url:
            url = f"{base_url}/reports/oee?sourceId=robot-1&start=0&end=5000&plannedTimeS=10.0&idealCycleTimeS=2.0"
            status, body = _get(url)
            assert status == 200
            assert body["formula_version"] == "oee-v1"
            assert len(body["input_fingerprint"]) == 64  # real sha256 hex digest


def test_oee_export_endpoint_returns_a_reproducible_csv() -> None:
    with running_fake_datalake() as (datalake_url, fake_server):
        points = []
        for i in range(5):
            ts = i * 1000
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0 if i < 4 else 0.0})
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": 2.0})
        fake_server.points = points

        with running_reports_server(datalake_url) as base_url:
            url = f"{base_url}/reports/oee/export?sourceId=robot-1&start=0&end=5000&plannedTimeS=10.0&idealCycleTimeS=2.0"
            status_a, content_type, csv_a = _get_raw(url)
            status_b, _, csv_b = _get_raw(url)
            assert status_a == 200
            assert status_b == 200
            assert content_type.startswith("text/csv")
            assert csv_a == csv_b
            assert "sourceId,robot-1" in csv_a
            assert "oee-v1" in csv_a


def test_oee_export_endpoint_returns_400_for_missing_params() -> None:
    with running_fake_datalake() as (datalake_url, _fake):
        with running_reports_server(datalake_url) as base_url:
            status, body = _get(f"{base_url}/reports/oee/export?sourceId=robot-1")
            assert status == 400
            assert "error" in body


def test_oee_endpoint_returns_400_for_missing_params() -> None:
    with running_fake_datalake() as (datalake_url, _fake):
        with running_reports_server(datalake_url) as base_url:
            status, body = _get(f"{base_url}/reports/oee?sourceId=robot-1")
            assert status == 400
            assert "error" in body


def test_oee_endpoint_returns_502_when_datalake_unreachable() -> None:
    client = DatalakeClient("http://127.0.0.1:1", timeout_s=1.0)
    server = ReportsServer(("127.0.0.1", 0), client)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        url = (
            f"http://127.0.0.1:{port}/reports/oee?sourceId=robot-1&start=0&end=1000"
            "&plannedTimeS=10.0&idealCycleTimeS=1.0"
        )
        status, body = _get(url)
        assert status == 502
        assert "error" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_availability_endpoint_real_round_trip() -> None:
    with running_fake_datalake() as (datalake_url, fake_server):
        fake_server.points = [
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": ts, "value": 20.0}
            for ts in range(0, 5001, 1000)
        ]
        with running_reports_server(datalake_url) as base_url:
            url = (
                f"{base_url}/reports/availability?sourceId=robot-1&kind=motor_temp&field=value"
                "&start=0&end=10000&expectedIntervalMs=1000"
            )
            status, body = _get(url)
            assert status == 200
            assert body["downtime_ms"] == 5000
            assert abs(body["availability"] - 0.5) < 1e-9


def test_availability_export_endpoint_returns_a_reproducible_csv() -> None:
    with running_fake_datalake() as (datalake_url, fake_server):
        fake_server.points = [
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": ts, "value": 20.0}
            for ts in range(0, 5001, 1000)
        ]
        with running_reports_server(datalake_url) as base_url:
            url = (
                f"{base_url}/reports/availability/export?sourceId=robot-1&kind=motor_temp&field=value"
                "&start=0&end=10000&expectedIntervalMs=1000"
            )
            status_a, content_type, csv_a = _get_raw(url)
            status_b, _, csv_b = _get_raw(url)
            assert status_a == 200
            assert status_b == 200
            assert content_type.startswith("text/csv")
            assert csv_a == csv_b
            assert "availability-v1" in csv_a


def test_availability_endpoint_returns_400_for_missing_params() -> None:
    with running_fake_datalake() as (datalake_url, _fake):
        with running_reports_server(datalake_url) as base_url:
            status, body = _get(f"{base_url}/reports/availability?sourceId=robot-1")
            assert status == 400
            assert "error" in body


def test_unknown_path_returns_404() -> None:
    with running_fake_datalake() as (datalake_url, _fake):
        with running_reports_server(datalake_url) as base_url:
            status, _body = _get(f"{base_url}/nope")
            assert status == 404

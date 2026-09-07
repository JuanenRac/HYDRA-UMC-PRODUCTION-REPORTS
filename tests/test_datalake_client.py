# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_datalake_client.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import socket
import threading

import pytest

from fake_datalake import FakeDatalakeServer, running_fake_datalake
from hydra_umc_production_reports.datalake_client import DatalakeClient, DatalakeError


def test_query_real_round_trip() -> None:
    with running_fake_datalake() as (url, server):
        server.points = [
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": 1000, "value": 42.5},
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": 2000, "value": 43.0},
            {"sourceId": "robot-2", "kind": "motor_temp", "field": "value", "timestamp": 1500, "value": 99.0},
        ]
        client = DatalakeClient(url)
        points = client.query(source_id="robot-1")
        assert len(points) == 2
        assert points[0].timestamp == 1000
        assert points[0].value == 42.5


def test_query_filters_are_sent_and_applied() -> None:
    with running_fake_datalake() as (url, server):
        server.points = [
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": 1000, "value": 1.0},
            {"sourceId": "robot-1", "kind": "motor_current", "field": "value", "timestamp": 1000, "value": 2.0},
        ]
        client = DatalakeClient(url)
        points = client.query(source_id="robot-1", kind="motor_current")
        assert len(points) == 1
        assert points[0].kind == "motor_current"


def test_unreachable_datalake_raises_datalake_error() -> None:
    # A port nothing is listening on (real network failure, not simulated).
    # max_attempts=1 keeps this fast and focused on "unreachable raises" -
    # the retry behavior itself is covered by the tests below.
    client = DatalakeClient("http://127.0.0.1:1", timeout_s=1.0, max_attempts=1)
    with pytest.raises(DatalakeError):
        client.query(source_id="robot-1")


def test_http_error_from_datalake_raises_datalake_error() -> None:
    with running_fake_datalake() as (url, _server):
        client = DatalakeClient(url)
        with pytest.raises(DatalakeError):
            client._get("/error", {})


def test_http_error_is_never_retried() -> None:
    # Found in the same audit as the retry fix below: only a transient
    # network failure (URLError) should retry - a real HTTP response
    # DATALAKE itself sent (e.g. this fake server's own /error -> 500) is
    # never transient in that sense and must still raise on the first try.
    attempts = {"n": 0}

    def counting_sleep(_delay: float) -> None:
        attempts["n"] += 1

    with running_fake_datalake() as (url, _server):
        client = DatalakeClient(url, max_attempts=5, sleep=counting_sleep)
        with pytest.raises(DatalakeError):
            client._get("/error", {})
    assert attempts["n"] == 0


def test_transient_network_failure_is_retried_then_succeeds() -> None:
    # Found in an ecosystem-wide software-improvements audit: this client
    # had a timeout but no retry on transient network failure - one
    # network hiccup used to fail an entire report instead of retrying.
    # Proven against a real socket, not a mock: nothing listens on `port`
    # for the first attempt (a genuine ConnectionRefusedError), then the
    # injected `sleep` itself brings a real server up on that exact port
    # before the second attempt - the retry loop, not just the code path,
    # is what's under test.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # `probe` is closed now - nothing listens on `port` yet.

    state: dict[str, object] = {}

    def bring_server_up(_delay: float) -> None:
        if "server" in state:
            return  # only needed once, in case max_attempts ever grows
        server = FakeDatalakeServer(("127.0.0.1", port))
        server.points = [
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": 1000, "value": 42.5},
        ]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state["server"] = server
        state["thread"] = thread

    client = DatalakeClient(f"http://127.0.0.1:{port}", timeout_s=1.0, max_attempts=3, sleep=bring_server_up)
    try:
        points = client.query(source_id="robot-1")
        assert len(points) == 1
        assert points[0].value == 42.5
    finally:
        server = state.get("server")
        if server is not None:
            server.shutdown()
            server.server_close()
            state["thread"].join(timeout=2)


def test_gives_up_after_max_attempts_with_a_clear_error() -> None:
    client = DatalakeClient("http://127.0.0.1:1", timeout_s=1.0, max_attempts=3, sleep=lambda _delay: None)
    with pytest.raises(DatalakeError, match="after 3 attempts"):
        client.query(source_id="robot-1")

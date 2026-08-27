# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_datalake_client.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import pytest

from fake_datalake import running_fake_datalake
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
    client = DatalakeClient("http://127.0.0.1:1", timeout_s=1.0)
    with pytest.raises(DatalakeError):
        client.query(source_id="robot-1")


def test_http_error_from_datalake_raises_datalake_error() -> None:
    with running_fake_datalake() as (url, _server):
        client = DatalakeClient(url)
        with pytest.raises(DatalakeError):
            client._get("/error", {})

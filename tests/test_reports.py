# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_reports.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""The tests that matter most for this project's actual promise: real
OEE/availability reports built from data served by a real (if fake)
HYDRA-UMC-DATALAKE HTTP server - not computed from Python objects handed
directly to oee.py/availability.py, but round-tripped through a real
DatalakeClient exactly as production use would."""
from __future__ import annotations

import pytest

from fake_datalake import running_fake_datalake
from hydra_umc_production_reports.datalake_client import DatalakeClient
from hydra_umc_production_reports.reports import ReportError, availability_from_datalake, oee_from_datalake


def test_oee_from_datalake_real_round_trip() -> None:
    with running_fake_datalake() as (url, server):
        # 5 production_event cycles for robot-1: 4 good, 1 bad, each 2s,
        # ideal cycle time also 2s -> Performance=1.0, Quality=0.8.
        points = []
        for i in range(5):
            ts = i * 1000
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0 if i < 4 else 0.0})
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": 2.0})
        server.points = points

        client = DatalakeClient(url)
        report = oee_from_datalake(
            client,
            source_id="robot-1",
            start_ms=0,
            end_ms=5000,
            planned_time_s=10.0,  # 10s planned, 5*2=10s operating -> Availability=1.0
            ideal_cycle_time_s=2.0,
        )
        assert report.total_count == 5
        assert report.good_count == 4
        assert report.quality == pytest.approx(0.8)
        assert report.availability == pytest.approx(1.0)
        assert report.performance == pytest.approx(1.0)
        assert report.oee == pytest.approx(0.8)


def test_oee_from_datalake_ignores_source_with_no_data() -> None:
    with running_fake_datalake() as (url, server):
        server.points = []
        client = DatalakeClient(url)
        with pytest.raises(ReportError):
            oee_from_datalake(
                client, source_id="nonexistent", start_ms=0, end_ms=1000,
                planned_time_s=10.0, ideal_cycle_time_s=1.0,
            )


def test_oee_from_datalake_handles_unmatched_fields_honestly() -> None:
    with running_fake_datalake() as (url, server):
        # "good" written without a matching "cycleTimeS" at the same
        # timestamp - a real, malformed/partial write this project must
        # not silently paper over.
        server.points = [
            {"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": 1000, "value": 1.0},
        ]
        client = DatalakeClient(url)
        with pytest.raises(ReportError, match="no matching 'cycleTimeS'"):
            oee_from_datalake(
                client, source_id="robot-1", start_ms=0, end_ms=2000,
                planned_time_s=10.0, ideal_cycle_time_s=1.0,
            )


def test_availability_from_datalake_real_round_trip() -> None:
    with running_fake_datalake() as (url, server):
        # motor_temp samples every 1000ms for the first half of the
        # window, then nothing - real downtime in the second half.
        server.points = [
            {"sourceId": "robot-1", "kind": "motor_temp", "field": "value", "timestamp": ts, "value": 20.0}
            for ts in range(0, 5001, 1000)
        ]
        client = DatalakeClient(url)
        report = availability_from_datalake(
            client,
            source_id="robot-1",
            kind="motor_temp",
            field="value",
            start_ms=0,
            end_ms=10000,
            expected_interval_ms=1000.0,
        )
        # Trailing gap from 5000 to 10000 = 5000ms of downtime out of 10000ms window.
        assert report.downtime_ms == 5000
        assert report.availability == pytest.approx(0.5)

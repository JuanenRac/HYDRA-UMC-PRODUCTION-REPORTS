# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_fleet_oee.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""OEE of several sources at once: per source, combined, and the bottleneck."""
from __future__ import annotations

import pytest
from fake_datalake import running_fake_datalake
from test_api import _get, running_reports_server

from hydra_umc_production_reports.datalake_client import DatalakeClient
from hydra_umc_production_reports.oee import OEEReport
from hydra_umc_production_reports.reports import (
    MAX_FLEET_SOURCES,
    ReportError,
    combine_oee,
    find_bottleneck,
    fleet_oee_from_datalake,
)


def cycles(source: str, goods: list[bool], cycle_time_s: float = 2.0) -> list[dict]:
    points: list[dict] = []
    for i, good in enumerate(goods):
        ts = i * 1000
        points.append({"sourceId": source, "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0 if good else 0.0})
        points.append({"sourceId": source, "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": cycle_time_s})
    return points


def report(parts: tuple[float, float, float]) -> OEEReport:
    availability, performance, quality = parts
    return OEEReport(availability, performance, quality, availability * performance * quality, 10, 9, 20.0, "oee-v1", "f" * 64)


def fleet(url: str, names: list[str]):
    return fleet_oee_from_datalake(DatalakeClient(url), source_ids=names, start_ms=0, end_ms=5000, planned_time_s=10.0, ideal_cycle_time_s=2.0)


def test_fleet_reports_each_source_the_group_and_the_bottleneck() -> None:
    with running_fake_datalake() as (url, fake):
        fake.points = cycles("robot-1", [True] * 5) + cycles("robot-2", [True, True, False, False, False])
        result = fleet(url, ["robot-1", "robot-2"])
    assert set(result.sources) == {"robot-1", "robot-2"} and not result.failed
    assert result.sources["robot-1"].oee == pytest.approx(1.0)
    assert result.sources["robot-2"].oee == pytest.approx(0.4)
    assert result.combined is not None
    assert result.combined.total_count == 10 and result.combined.good_count == 7
    assert result.combined.quality == pytest.approx(0.7)
    assert result.combined.oee == pytest.approx(0.7)
    assert result.bottleneck["sourceId"] == "robot-2"
    assert result.bottleneck["weakestFactor"] == "quality"


def test_a_source_without_data_is_listed_as_failed_and_does_not_hide_the_others() -> None:
    with running_fake_datalake() as (url, fake):
        fake.points = cycles("robot-1", [True] * 5)
        result = fleet(url, ["robot-1", "robot-9"])
    assert list(result.sources) == ["robot-1"]
    assert "no production_event data" in result.failed["robot-9"]
    assert result.combined is not None and result.bottleneck is None


def test_every_source_failing_leaves_no_combined_report() -> None:
    with running_fake_datalake() as (url, _fake):
        result = fleet(url, ["a", "b"])
    assert result.sources == {} and set(result.failed) == {"a", "b"} and result.combined is None


def test_repeated_names_count_once_and_the_list_is_bounded() -> None:
    with running_fake_datalake() as (url, fake):
        fake.points = cycles("robot-1", [True] * 5)
        assert len(fleet(url, ["robot-1", "robot-1"]).sources) == 1
        with pytest.raises(ReportError):
            fleet(url, [])
        with pytest.raises(ReportError):
            fleet(url, [f"r{i}" for i in range(MAX_FLEET_SOURCES + 1)])


def test_the_bottleneck_names_the_weakest_factor() -> None:
    assert find_bottleneck({"a": report((0.9, 0.9, 0.9))}) is None
    found = find_bottleneck({"a": report((0.9, 0.9, 0.9)), "b": report((0.5, 0.9, 0.95))})
    assert found["sourceId"] == "b" and found["weakestFactor"] == "availability"


def test_combined_availability_is_total_operating_time_over_total_planned_time() -> None:
    combined = combine_oee({"a": report((1.0, 1.0, 1.0)), "b": report((0.5, 1.0, 1.0))}, planned_time_s=20.0, ideal_cycle_time_s=2.0)
    assert combined.operating_time_s == 40.0
    assert combined.availability == 1.0  # 40 s of operating time against 2 x 20 s planned
    with pytest.raises(ReportError):
        combine_oee({}, planned_time_s=20.0, ideal_cycle_time_s=2.0)


def test_fleet_endpoint_round_trip_and_its_errors() -> None:
    with running_fake_datalake() as (datalake_url, fake):
        fake.points = cycles("robot-1", [True] * 5) + cycles("robot-2", [True, False, False, False, False])
        with running_reports_server(datalake_url) as base:
            status, body = _get(f"{base}/reports/oee/fleet?sourceIds=robot-1,robot-2,robot-3&start=0&end=5000&plannedTimeS=10&idealCycleTimeS=2")
            assert status == 200
            assert set(body["sources"]) == {"robot-1", "robot-2"}
            assert "robot-3" in body["failed"]
            assert body["bottleneck"]["sourceId"] == "robot-2"
            assert body["combined"]["total_count"] == 10
            assert _get(f"{base}/reports/oee/fleet?start=0&end=5000&plannedTimeS=10&idealCycleTimeS=2")[0] == 400
            assert _get(f"{base}/reports/oee/fleet?sourceIds=,&start=0&end=5000&plannedTimeS=10&idealCycleTimeS=2")[0] == 400

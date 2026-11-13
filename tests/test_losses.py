# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_losses.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""The breakdown of the planned time into availability, speed and quality losses and productive time."""
from __future__ import annotations

import pytest

from fake_datalake import running_fake_datalake
from hydra_umc_production_reports.losses import compute_loss_breakdown
from hydra_umc_production_reports.oee import OEEError, ProductionEvent, compute_oee
from test_api import _get, running_reports_server


def _events(count: int, good: int, cycle_s: float) -> list[ProductionEvent]:
    return [ProductionEvent(timestamp_ms=i * 1000, good=i < good, cycle_time_s=cycle_s) for i in range(count)]


def test_the_four_parts_add_up_to_the_planned_time_and_productive_equals_planned_times_oee() -> None:
    report = compute_oee(_events(8, 6, 3.0), planned_time_s=40.0, ideal_cycle_time_s=2.0)
    losses = compute_loss_breakdown(report, planned_time_s=40.0, ideal_cycle_time_s=2.0)
    total = losses.availability_loss_s + losses.speed_loss_s + losses.quality_loss_s + losses.productive_s
    assert total == pytest.approx(40.0)
    assert losses.productive_s == pytest.approx(40.0 * report.oee)
    assert losses.availability_loss_s == pytest.approx(16.0)  # 40 planned - 24 operating
    assert losses.speed_loss_s == pytest.approx(8.0)  # 24 operating - 16 ideal
    assert losses.quality_loss_s == pytest.approx(4.0)  # 2 bad parts x 2 s ideal
    assert losses.consistent is True


def test_no_loss_at_all_when_everything_is_ideal() -> None:
    report = compute_oee(_events(5, 5, 2.0), planned_time_s=10.0, ideal_cycle_time_s=2.0)
    losses = compute_loss_breakdown(report, planned_time_s=10.0, ideal_cycle_time_s=2.0)
    assert (losses.availability_loss_s, losses.speed_loss_s, losses.quality_loss_s) == (0.0, 0.0, 0.0)
    assert losses.productive_s == pytest.approx(10.0)
    assert losses.largest_loss() is None


def test_cycles_faster_than_ideal_have_no_speed_loss() -> None:
    report = compute_oee(_events(5, 5, 1.0), planned_time_s=10.0, ideal_cycle_time_s=2.0)
    losses = compute_loss_breakdown(report, planned_time_s=10.0, ideal_cycle_time_s=2.0)
    assert losses.speed_loss_s == 0.0
    assert losses.productive_s == pytest.approx(5.0)


def test_more_operating_time_than_planned_is_capped_and_flagged() -> None:
    report = compute_oee(_events(10, 10, 2.0), planned_time_s=10.0, ideal_cycle_time_s=2.0)
    losses = compute_loss_breakdown(report, planned_time_s=10.0, ideal_cycle_time_s=2.0)
    assert report.inconsistent_input is True
    assert losses.consistent is False
    assert losses.availability_loss_s == 0.0
    assert losses.availability_loss_s + losses.speed_loss_s + losses.quality_loss_s + losses.productive_s == pytest.approx(10.0)


def test_the_largest_loss_and_the_fractions() -> None:
    report = compute_oee(_events(8, 6, 3.0), planned_time_s=40.0, ideal_cycle_time_s=2.0)
    losses = compute_loss_breakdown(report, planned_time_s=40.0, ideal_cycle_time_s=2.0)
    assert losses.largest_loss() == "availability"
    assert sum(losses.fractions().values()) == pytest.approx(1.0)
    as_dict = losses.to_dict()
    assert as_dict["largest_loss"] == "availability" and "fractions" in as_dict


@pytest.mark.parametrize("planned,ideal", [(0.0, 2.0), (-1.0, 2.0), (float("nan"), 2.0), (10.0, 0.0), (10.0, float("inf"))])
def test_bad_inputs_are_refused(planned: float, ideal: float) -> None:
    report = compute_oee(_events(5, 5, 2.0), planned_time_s=10.0, ideal_cycle_time_s=2.0)
    with pytest.raises(OEEError):
        compute_loss_breakdown(report, planned_time_s=planned, ideal_cycle_time_s=ideal)


def test_the_losses_route_answers_with_the_report_and_its_breakdown() -> None:
    with running_fake_datalake() as (datalake_url, fake_server):
        points = []
        for i in range(5):
            ts = i * 1000
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0 if i < 4 else 0.0})
            points.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": 2.0})
        fake_server.points = points
        with running_reports_server(datalake_url) as base_url:
            query = "sourceId=robot-1&start=0&end=5000&plannedTimeS=20.0&idealCycleTimeS=2.0"
            status, body = _get(f"{base_url}/reports/oee/losses?{query}")
            assert status == 200
            assert body["report"] == _get(f"{base_url}/reports/oee?{query}")[1]
            losses = body["losses"]
            assert losses["availability_loss_s"] == pytest.approx(10.0)
            assert losses["quality_loss_s"] == pytest.approx(2.0)
            assert losses["productive_s"] == pytest.approx(8.0)
            assert losses["largest_loss"] == "availability"
            assert _get(f"{base_url}/reports/oee/losses?start=0&end=5000")[0] == 400

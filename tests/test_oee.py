# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_oee.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import pytest

from hydra_umc_production_reports.oee import OEEError, ProductionEvent, compute_oee


def test_compute_oee_hand_checkable_example() -> None:
    # 10 cycles, 8 good, each taking 5s real (ideal is 4s) -> 50s operating
    # time out of a 100s planned window.
    # Availability = 50/100 = 0.5
    # Performance = (4*10)/50 = 40/50 = 0.8
    # Quality = 8/10 = 0.8
    # OEE = 0.5 * 0.8 * 0.8 = 0.32
    events = [ProductionEvent(timestamp_ms=i * 1000, good=(i < 8), cycle_time_s=5.0) for i in range(10)]
    report = compute_oee(events, planned_time_s=100.0, ideal_cycle_time_s=4.0)

    assert report.availability == pytest.approx(0.5)
    assert report.performance == pytest.approx(0.8)
    assert report.quality == pytest.approx(0.8)
    assert report.oee == pytest.approx(0.32)
    assert report.total_count == 10
    assert report.good_count == 8
    assert report.operating_time_s == pytest.approx(50.0)


def test_compute_oee_perfect_run_is_100_percent() -> None:
    events = [ProductionEvent(timestamp_ms=i, good=True, cycle_time_s=2.0) for i in range(50)]
    report = compute_oee(events, planned_time_s=100.0, ideal_cycle_time_s=2.0)
    assert report.oee == pytest.approx(1.0)


def test_compute_oee_performance_is_clamped_not_over_100_percent() -> None:
    # Real cycles running FASTER than the "ideal" figure (e.g. an
    # optimistic ideal_cycle_time_s) must not report >100% performance.
    events = [ProductionEvent(timestamp_ms=i, good=True, cycle_time_s=1.0) for i in range(10)]
    report = compute_oee(events, planned_time_s=100.0, ideal_cycle_time_s=5.0)
    assert report.performance == 1.0


def test_compute_oee_availability_is_clamped_not_over_100_percent() -> None:
    # Operating time exceeding the (mis-set) planned time must not report
    # over 100% availability.
    events = [ProductionEvent(timestamp_ms=i, good=True, cycle_time_s=20.0) for i in range(10)]
    report = compute_oee(events, planned_time_s=50.0, ideal_cycle_time_s=20.0)
    assert report.availability == 1.0


def test_compute_oee_rejects_empty_events() -> None:
    with pytest.raises(OEEError):
        compute_oee([], planned_time_s=100.0, ideal_cycle_time_s=1.0)


def test_compute_oee_rejects_non_positive_planned_time() -> None:
    events = [ProductionEvent(timestamp_ms=1, good=True, cycle_time_s=1.0)]
    with pytest.raises(OEEError):
        compute_oee(events, planned_time_s=0.0, ideal_cycle_time_s=1.0)


def test_compute_oee_rejects_non_positive_ideal_cycle_time() -> None:
    events = [ProductionEvent(timestamp_ms=1, good=True, cycle_time_s=1.0)]
    with pytest.raises(OEEError):
        compute_oee(events, planned_time_s=100.0, ideal_cycle_time_s=0.0)


def test_compute_oee_all_defective_gives_zero_quality_and_oee() -> None:
    events = [ProductionEvent(timestamp_ms=i, good=False, cycle_time_s=1.0) for i in range(5)]
    report = compute_oee(events, planned_time_s=10.0, ideal_cycle_time_s=1.0)
    assert report.quality == 0.0
    assert report.oee == 0.0

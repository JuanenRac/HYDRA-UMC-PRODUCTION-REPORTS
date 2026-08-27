# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_availability.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import pytest

from hydra_umc_production_reports.availability import AvailabilityError, compute_availability


def test_continuous_stream_is_fully_available() -> None:
    # A sample every 1000ms across a 10000ms window, expected_interval=1000ms.
    timestamps = list(range(0, 10001, 1000))
    report = compute_availability(
        timestamps, window_start_ms=0, window_end_ms=10000, expected_interval_ms=1000.0
    )
    assert report.availability == pytest.approx(1.0)
    assert report.downtime_ms == 0


def test_hand_checkable_gap_in_the_middle() -> None:
    # Samples at 0, 1000, then a real gap until 8000, then 9000, 10000.
    # Window is 0-10000 (10000ms total). Gap 1000->8000 = 7000ms, which
    # is > 1000*3=3000ms threshold, so it counts as downtime.
    timestamps = [0, 1000, 8000, 9000, 10000]
    report = compute_availability(
        timestamps, window_start_ms=0, window_end_ms=10000, expected_interval_ms=1000.0, gap_factor=3.0
    )
    assert report.downtime_ms == 7000
    assert report.availability == pytest.approx(1.0 - 7000 / 10000)
    assert len(report.downtime_periods) == 1
    assert report.downtime_periods[0].start_ms == 1000
    assert report.downtime_periods[0].end_ms == 8000


def test_jitter_within_gap_factor_is_not_counted_as_downtime() -> None:
    # Samples arriving up to 2x late (within the default 3x gap_factor)
    # must NOT be flagged as downtime - real jitter, not an outage.
    timestamps = [0, 2000, 4000, 6000, 8000, 10000]  # 2000ms spacing, expected 1000ms
    report = compute_availability(
        timestamps, window_start_ms=0, window_end_ms=10000, expected_interval_ms=1000.0, gap_factor=3.0
    )
    assert report.downtime_ms == 0


def test_leading_and_trailing_gaps_count_as_downtime() -> None:
    # No samples until 5000ms, then nothing after 6000ms, in a 0-10000 window.
    timestamps = [5000, 6000]
    report = compute_availability(
        timestamps, window_start_ms=0, window_end_ms=10000, expected_interval_ms=1000.0, gap_factor=3.0
    )
    # Leading gap 0->5000 (5000ms) + trailing gap 6000->10000 (4000ms) = 9000ms
    assert report.downtime_ms == 9000
    assert len(report.downtime_periods) == 2


def test_no_samples_at_all_is_100_percent_downtime() -> None:
    report = compute_availability(
        [], window_start_ms=0, window_end_ms=10000, expected_interval_ms=1000.0
    )
    assert report.availability == 0.0
    assert report.downtime_ms == 10000


def test_rejects_invalid_window() -> None:
    with pytest.raises(AvailabilityError):
        compute_availability([1, 2], window_start_ms=100, window_end_ms=100, expected_interval_ms=10.0)


def test_rejects_non_positive_expected_interval() -> None:
    with pytest.raises(AvailabilityError):
        compute_availability([1, 2], window_start_ms=0, window_end_ms=100, expected_interval_ms=0.0)

# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/availability.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real machine-availability estimation from ANY existing telemetry
stream already sitting in HYDRA-UMC-DATALAKE - unlike oee.py (which needs
this project's own "production event" convention), this works against
whatever HYDRA-UMC-TELEMETRY-COLLECTOR already fed the lake (e.g.
motor_temp samples arriving roughly every N seconds while a robot is
running): a gap in that stream noticeably larger than its normal sampling
interval is real, honest evidence the source was down, not just quiet.
"""
from __future__ import annotations

from dataclasses import dataclass


class AvailabilityError(ValueError):
    pass


@dataclass(frozen=True)
class DowntimePeriod:
    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass(frozen=True)
class AvailabilityReport:
    window_start_ms: int
    window_end_ms: int
    downtime_periods: list[DowntimePeriod]
    downtime_ms: int
    availability: float  # 0.0-1.0


def compute_availability(
    timestamps_ms: list[int],
    *,
    window_start_ms: int,
    window_end_ms: int,
    expected_interval_ms: float,
    gap_factor: float = 3.0,
) -> AvailabilityReport:
    """Computes real availability from a sorted-or-not list of sample
    timestamps (typically ``[p.timestamp for p in datalake_client.query(...)]``).

    A gap between two consecutive samples longer than
    ``expected_interval_ms * gap_factor`` is counted as real downtime -
    ``gap_factor`` defaults to 3x so ordinary jitter in a real telemetry
    stream (a slightly late sample) doesn't get misclassified as an
    outage; tune it down for a stream with tighter real timing
    guarantees. The window's own edges count too: a gap between
    ``window_start_ms`` and the first sample, or between the last sample
    and ``window_end_ms``, is real downtime the same way.
    """
    if window_end_ms <= window_start_ms:
        raise AvailabilityError("window_end_ms must be after window_start_ms")
    if expected_interval_ms <= 0:
        raise AvailabilityError("expected_interval_ms must be positive")

    threshold_ms = expected_interval_ms * gap_factor
    points = sorted(t for t in timestamps_ms if window_start_ms <= t <= window_end_ms)

    downtime_periods: list[DowntimePeriod] = []
    cursor = window_start_ms
    for t in points:
        gap = t - cursor
        if gap > threshold_ms:
            downtime_periods.append(DowntimePeriod(start_ms=cursor, end_ms=t))
        cursor = t
    # Trailing gap up to the end of the window.
    trailing_gap = window_end_ms - cursor
    if trailing_gap > threshold_ms:
        downtime_periods.append(DowntimePeriod(start_ms=cursor, end_ms=window_end_ms))

    downtime_ms = sum(p.duration_ms for p in downtime_periods)
    window_ms = window_end_ms - window_start_ms
    availability = max(0.0, min(1.0, 1.0 - (downtime_ms / window_ms)))

    return AvailabilityReport(
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        downtime_periods=downtime_periods,
        downtime_ms=downtime_ms,
        availability=availability,
    )

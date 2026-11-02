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

import hashlib
import math
from dataclasses import dataclass

from .provenance import Provenance

# Bumped only if the availability formula itself changes - see
# oee.FORMULA_VERSION for the same reasoning (see the diagnosis notes).
FORMULA_VERSION = "availability-v1"


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
    formula_version: str
    input_fingerprint: str
    provenance: Provenance | None = None
    # "points": gaps between the real timestamps. "aggregate": built from the buckets that hold points (GET /aggregate of the data
    # lake) because the window holds too many points to bring in; a gap is then the run of empty buckets, so it is measured to
    # the resolution of the bucket.
    method: str = "points"
    # "state": the part of the window in which no up/down event says what the state was. It is counted as downtime (an
    # availability is never better than what the events prove); the figure is kept apart so the report shows how much of it was guessed.
    unknown_ms: int = 0


def _fingerprint_timestamps(timestamps_ms: list[int]) -> str:
    """A real, deterministic fingerprint of the exact (window-filtered)
    sample timestamps that produced this report - see oee's own
    _fingerprint_events for the same reasoning."""
    canonical = "\n".join(str(t) for t in sorted(timestamps_ms))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    if not math.isfinite(expected_interval_ms) or expected_interval_ms <= 0:
        raise AvailabilityError("expected_interval_ms must be a finite, positive number")
    if not math.isfinite(gap_factor) or gap_factor < 1:
        raise AvailabilityError("gap_factor must be a finite number of at least 1")

    threshold_ms = expected_interval_ms * gap_factor
    # The window is half open, [start, end): a sample exactly at the end
    # belongs to the next window, so two consecutive reports never count it
    # twice.
    points = sorted(t for t in timestamps_ms if window_start_ms <= t < window_end_ms)

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
        formula_version=FORMULA_VERSION,
        input_fingerprint=_fingerprint_timestamps(points),
    )


def compute_availability_from_buckets(
    bucket_starts_ms: list[int],
    *,
    bucket_ms: int,
    window_start_ms: int,
    window_end_ms: int,
    expected_interval_ms: float,
    gap_factor: float = 3.0,
) -> AvailabilityReport:
    """Availability from the buckets of GET /aggregate that hold at least one point (an empty bucket is not returned).

    Every bucket is `bucket_ms` wide and aligned to `window_start_ms`. A run of empty buckets (or the empty edge of the window)
    longer than `expected_interval_ms * gap_factor` is downtime. Compared with the real timestamps this is exact to one
    bucket: a gap is measured from the end of the last bucket that has a point to the start of the next one, so it can be
    shorter than the real gap by up to two buckets and never longer."""
    if window_end_ms <= window_start_ms:
        raise AvailabilityError("window_end_ms must be after window_start_ms")
    if not math.isfinite(expected_interval_ms) or expected_interval_ms <= 0:
        raise AvailabilityError("expected_interval_ms must be a finite, positive number")
    if not math.isfinite(gap_factor) or gap_factor < 1:
        raise AvailabilityError("gap_factor must be a finite number of at least 1")
    if bucket_ms <= 0:
        raise AvailabilityError("bucket_ms must be positive")
    threshold_ms = expected_interval_ms * gap_factor
    starts = sorted({s for s in bucket_starts_ms if window_start_ms <= s < window_end_ms})
    periods: list[DowntimePeriod] = []
    cursor = window_start_ms
    for start in starts:
        if start - cursor > threshold_ms:
            periods.append(DowntimePeriod(start_ms=cursor, end_ms=start))
        cursor = min(start + bucket_ms, window_end_ms)
    if window_end_ms - cursor > threshold_ms:
        periods.append(DowntimePeriod(start_ms=cursor, end_ms=window_end_ms))
    downtime_ms = sum(p.duration_ms for p in periods)
    availability = max(0.0, min(1.0, 1.0 - downtime_ms / (window_end_ms - window_start_ms)))
    canonical = f"buckets|{bucket_ms}|" + ";".join(str(s) for s in starts)
    return AvailabilityReport(
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        downtime_periods=periods,
        downtime_ms=downtime_ms,
        availability=availability,
        formula_version=FORMULA_VERSION,
        input_fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        method="aggregate",
    )


def compute_availability_from_states(
    events: list[tuple[int, bool]],
    *,
    window_start_ms: int,
    window_end_ms: int,
    initial_up: bool | None = None,
) -> AvailabilityReport:
    """Availability from explicit up/down events instead of gaps in a telemetry stream.

    `events` are (timestamp_ms, up) pairs, as sent by a node supervisor or by the heartbeats of a dispatcher; the order does not
    matter, and when two events share a millisecond the last one given wins. `initial_up` is the state in force when the window
    opens (the last event before it); when it is None, the time from the start of the window to the first event is `unknown_ms`
    and counts as downtime, because nothing proves the source was up. The window is half open, [start, end)."""
    if window_end_ms <= window_start_ms:
        raise AvailabilityError("window_end_ms must be after window_start_ms")
    inside: dict[int, bool] = {}
    for stamp, up in events:
        if window_start_ms <= stamp < window_end_ms:
            inside[stamp] = bool(up)
    periods: list[DowntimePeriod] = []
    unknown_ms = 0
    cursor = window_start_ms
    state: bool | None = initial_up
    for stamp in sorted(inside):
        if state is not True and stamp > cursor:
            periods.append(DowntimePeriod(start_ms=cursor, end_ms=stamp))
            if state is None:
                unknown_ms += stamp - cursor
        cursor, state = stamp, inside[stamp]
    if state is not True and window_end_ms > cursor:
        periods.append(DowntimePeriod(start_ms=cursor, end_ms=window_end_ms))
        if state is None:
            unknown_ms += window_end_ms - cursor
    # consecutive down periods (an explicit down followed by another down) are merged so the list stays minimal
    merged: list[DowntimePeriod] = []
    for period in periods:
        if merged and merged[-1].end_ms == period.start_ms:
            merged[-1] = DowntimePeriod(start_ms=merged[-1].start_ms, end_ms=period.end_ms)
        else:
            merged.append(period)
    downtime_ms = sum(p.duration_ms for p in merged)
    availability = max(0.0, min(1.0, 1.0 - downtime_ms / (window_end_ms - window_start_ms)))
    canonical = f"states|{initial_up}|" + ";".join(f"{t}:{int(inside[t])}" for t in sorted(inside))
    return AvailabilityReport(
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
        downtime_periods=merged,
        downtime_ms=downtime_ms,
        availability=availability,
        formula_version=FORMULA_VERSION,
        input_fingerprint=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        method="state",
        unknown_ms=unknown_ms,
    )

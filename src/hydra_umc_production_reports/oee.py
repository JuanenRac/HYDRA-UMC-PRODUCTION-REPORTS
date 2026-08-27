# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/oee.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real OEE (Overall Equipment Effectiveness) calculation - the standard
industrial formula (Availability x Performance x Quality), not a
placeholder metric. See ``from_production_events`` in this module for
how this project's own v0 "production event" telemetry convention feeds
it from real HYDRA-UMC-DATALAKE history.
"""
from __future__ import annotations

from dataclasses import dataclass


class OEEError(ValueError):
    """Raised for a real, honest reason a report can't be computed - a
    caller with zero completed cycles, or a caller time-budget of zero -
    rather than silently returning a misleading 0% or 100%."""


@dataclass(frozen=True)
class ProductionEvent:
    """One real production cycle: did it produce a good part, and how
    long did it take. This project's own v0 convention for what a
    "production event" telemetry sample looks like in
    HYDRA-UMC-DATALAKE - see datalake_client.py / from_datalake() for how
    it's read back from real stored history."""

    timestamp_ms: int
    good: bool
    cycle_time_s: float


@dataclass(frozen=True)
class OEEReport:
    availability: float  # 0.0-1.0: Operating Time / Planned Production Time
    performance: float  # 0.0-1.0 (clamped): (Ideal Cycle Time x Total Count) / Operating Time
    quality: float  # 0.0-1.0: Good Count / Total Count
    oee: float  # availability * performance * quality
    total_count: int
    good_count: int
    operating_time_s: float


def compute_oee(
    events: list[ProductionEvent],
    *,
    planned_time_s: float,
    ideal_cycle_time_s: float,
) -> OEEReport:
    """Computes a real OEE report from a real list of production events.

    - Availability = sum(actual cycle times) / planned_time_s - the
      fraction of the planned window actually spent producing (the rest
      being real downtime, whatever its cause).
    - Performance = (ideal_cycle_time_s * total_count) / operating_time_s,
      clamped to [0, 1] - a real cycle can run faster than "ideal" due to
      measurement noise or a conservative ideal figure; clamping avoids
      reporting performance above 100%, which would be misleading even
      if arithmetically "correct".
    - Quality = good_count / total_count.
    - OEE = Availability x Performance x Quality (the real, standard
      industrial formula - not re-derived or approximated).

    Raises OEEError for real, unrepresentable inputs (no events,
    non-positive planned time) rather than returning a misleading number.
    """
    if not events:
        raise OEEError("cannot compute OEE from zero production events")
    if planned_time_s <= 0:
        raise OEEError("planned_time_s must be positive")
    if ideal_cycle_time_s <= 0:
        raise OEEError("ideal_cycle_time_s must be positive")

    total_count = len(events)
    good_count = sum(1 for e in events if e.good)
    operating_time_s = sum(e.cycle_time_s for e in events)

    availability = min(operating_time_s / planned_time_s, 1.0)
    performance = 0.0
    if operating_time_s > 0:
        performance = min((ideal_cycle_time_s * total_count) / operating_time_s, 1.0)
    quality = good_count / total_count

    return OEEReport(
        availability=availability,
        performance=performance,
        quality=quality,
        oee=availability * performance * quality,
        total_count=total_count,
        good_count=good_count,
        operating_time_s=operating_time_s,
    )

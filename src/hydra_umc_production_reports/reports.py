# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/reports.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""The real orchestration layer that ties datalake_client.py to oee.py
and availability.py - this module is what actually makes this project's
own README claim ("built from HYDRA-UMC-DATALAKE history") true, not
just query()/compute() functions that happen to sit in the same
codebase without ever being wired together against a real DATALAKE
instance.
"""
from __future__ import annotations

from .availability import AvailabilityReport, compute_availability
from .datalake_client import DatalakeClient
from .oee import OEEError, OEEReport, ProductionEvent, compute_oee

# This project's own v0 convention for a "production event" telemetry
# sample stored in HYDRA-UMC-DATALAKE: one Sample per completed cycle,
# kind="production_event", with two fields written together at the same
# timestamp - "good" (1.0/0.0) and "cycleTimeS" (seconds). No other
# project in the ecosystem writes this kind yet (HYDRA-UMC-JOB-DISPATCHER
# would be the real real-world source once it's wired to report
# completions here - see mejoras_futuras.txt) - documented explicitly as
# this project's own convention rather than presented as an established
# ecosystem-wide schema.
PRODUCTION_EVENT_KIND = "production_event"
GOOD_FIELD = "good"
CYCLE_TIME_FIELD = "cycleTimeS"


class ReportError(RuntimeError):
    """Wraps a real, specific reason a from_datalake() report couldn't be
    built - which DATALAKE call failed, or why the data that came back
    wasn't usable - rather than a bare exception."""


def oee_from_datalake(
    client: DatalakeClient,
    *,
    source_id: str,
    start_ms: int,
    end_ms: int,
    planned_time_s: float,
    ideal_cycle_time_s: float,
) -> OEEReport:
    """The real integration: queries a live HYDRA-UMC-DATALAKE instance
    for this source's `production_event` history (both fields of it),
    joins them by timestamp into real ProductionEvent records, and
    computes a real OEE report from what actually came back - not from
    synthetic or assumed data.
    """
    good_points = client.query(source_id=source_id, kind=PRODUCTION_EVENT_KIND, field=GOOD_FIELD, start=start_ms, end=end_ms)
    cycle_points = client.query(source_id=source_id, kind=PRODUCTION_EVENT_KIND, field=CYCLE_TIME_FIELD, start=start_ms, end=end_ms)

    cycle_by_ts = {p.timestamp: p.value for p in cycle_points}
    events: list[ProductionEvent] = []
    unmatched = 0
    for gp in good_points:
        cycle_time = cycle_by_ts.get(gp.timestamp)
        if cycle_time is None:
            # A "good" field with no matching "cycleTimeS" at the exact
            # same timestamp - a real, honest gap (the two fields are
            # meant to be written together in one Sample, see the
            # convention above) rather than silently defaulting to 0s,
            # which would corrupt the Performance calculation.
            unmatched += 1
            continue
        events.append(ProductionEvent(timestamp_ms=gp.timestamp, good=gp.value != 0.0, cycle_time_s=cycle_time))

    if not events:
        reason = "no production_event data found for this source/window"
        if unmatched:
            reason += f" ({unmatched} 'good' readings had no matching 'cycleTimeS' at the same timestamp)"
        raise ReportError(reason)

    try:
        return compute_oee(events, planned_time_s=planned_time_s, ideal_cycle_time_s=ideal_cycle_time_s)
    except OEEError as e:
        raise ReportError(str(e)) from e


def availability_from_datalake(
    client: DatalakeClient,
    *,
    source_id: str,
    kind: str,
    field: str,
    start_ms: int,
    end_ms: int,
    expected_interval_ms: float,
    gap_factor: float = 3.0,
) -> AvailabilityReport:
    """The real integration for availability: queries ANY existing
    telemetry series for this source (e.g. the motor_temp
    HYDRA-UMC-TELEMETRY-COLLECTOR already feeds HYDRA-UMC-DATALAKE) and
    computes real downtime from real gaps in when it actually arrived.
    """
    points = client.query(source_id=source_id, kind=kind, field=field, start=start_ms, end=end_ms)
    timestamps = [p.timestamp for p in points]
    return compute_availability(
        timestamps,
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        expected_interval_ms=expected_interval_ms,
        gap_factor=gap_factor,
    )

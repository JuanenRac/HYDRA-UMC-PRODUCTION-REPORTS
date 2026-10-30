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

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field, replace

from .availability import AvailabilityReport, compute_availability
from .datalake_client import DatalakeClient, Point
from . import __version__
from .oee import OEEError, OEEReport, ProductionEvent, compute_oee
from .provenance import Provenance

# This project's own v0 convention for a "production event" telemetry
# sample stored in HYDRA-UMC-DATALAKE: one Sample per completed cycle,
# kind="production_event", with two fields written together at the same
# timestamp - "good" (1.0/0.0) and "cycleTimeS" (seconds). No other
# project in the ecosystem writes this kind yet - documented explicitly as
# this project's own convention rather than presented as an established
# ecosystem-wide schema. The natural producers are whatever knows when a
# cycle starts and ends (the orchestrator, or the robot itself):
# HYDRA-UMC-JOB-DISPATCHER keeps no timestamps and cannot compute a cycle
# time.
PRODUCTION_EVENT_KIND = "production_event"
GOOD_FIELD = "good"
CYCLE_TIME_FIELD = "cycleTimeS"

# Real bug found while auditing the code: both report functions below
# used to call client.query() with its own default limit (10000) and
# never checked whether that limit was actually hit. DATALAKE's own
# store.py orders every query ASC by timestamp, so a truncated result
# silently keeps only the EARLIEST rows in the window and drops
# everything after - a report built from that looks like a real,
# complete OEE/Availability number for the requested window, when it
# actually only covers however much of the window fit in the first
# MAX_POINTS_PER_QUERY samples (e.g. a source reporting once a second
# blows through 10000 in well under 3 hours of a real 24h shift).
# Queried with a real headroom above what any query.py caller here asks
# for; a result that reaches this cap is treated as truncated and
# reported as a real, honest failure rather than a wrong-but-plausible
# number - see _query_all_or_raise() below.
MAX_POINTS_PER_QUERY = 200_000


class ReportError(RuntimeError):
    """Wraps a real, specific reason a from_datalake() report couldn't be
    built - which DATALAKE call failed, or why the data that came back
    wasn't usable - rather than a bare exception."""


def _check_window(start_ms: int, end_ms: int) -> None:
    if end_ms <= start_ms:
        raise ReportError("end must be after start")


def _query_all_or_raise(client: DatalakeClient, *, source_id: str, kind: str, field: str, start_ms: int, end_ms: int) -> list[Point]:
    """Same real query() call every caller below needs, with real
    truncation detection: if the result count reaches MAX_POINTS_PER_QUERY,
    more data almost certainly exists beyond it (DATALAKE's own ASC
    ordering means it would be the MOST RECENT data in the window, the
    part missing here) - raising a clear ReportError is the honest
    outcome, not silently computing a report from an incomplete window
    that looks complete."""
    # DATALAKE's end is inclusive and a report window is [start, end): asking
    # for end - 1 keeps a sample exactly on the boundary in the next window
    # only, instead of in both.
    points = client.query(source_id=source_id, kind=kind, field=field, start=start_ms, end=end_ms - 1, limit=MAX_POINTS_PER_QUERY)
    if len(points) >= MAX_POINTS_PER_QUERY:
        raise ReportError(
            f"query for source={source_id!r} kind={kind!r} field={field!r} returned "
            f"{len(points)} points, at or beyond the {MAX_POINTS_PER_QUERY}-point query cap - "
            "the real result is truncated (DATALAKE orders ascending by timestamp, so the most "
            "recent part of this window is the part missing) and would silently under-report a "
            "real OEE/Availability window rather than fail honestly. Narrow the time window and "
            "run this report in smaller pieces instead."
        )
    return points


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
    _check_window(start_ms, end_ms)
    good_points = _query_all_or_raise(client, source_id=source_id, kind=PRODUCTION_EVENT_KIND, field=GOOD_FIELD, start_ms=start_ms, end_ms=end_ms)
    cycle_points = _query_all_or_raise(client, source_id=source_id, kind=PRODUCTION_EVENT_KIND, field=CYCLE_TIME_FIELD, start_ms=start_ms, end_ms=end_ms)

    # Pair the two fields of a cycle by timestamp, in order when several
    # cycles share a millisecond (a dict keyed by timestamp lost all but the
    # last cycle time and paired every "good" of that millisecond with it).
    # A reading with no partner on either side, or whose "good" is not
    # exactly 0 or 1, cannot be used and is counted in `unmatched`.
    cycles_at: dict[int, list[float]] = defaultdict(list)
    for p in cycle_points:
        cycles_at[p.timestamp].append(p.value)
    events: list[ProductionEvent] = []
    unmatched = 0
    for gp in good_points:
        waiting = cycles_at.get(gp.timestamp)
        if not waiting:
            # A "good" field with no matching "cycleTimeS" at the exact
            # same timestamp - a real, honest gap (the two fields are
            # meant to be written together in one Sample, see the
            # convention above) rather than silently defaulting to 0s,
            # which would corrupt the Performance calculation.
            unmatched += 1
            continue
        cycle_time = waiting.pop(0)
        if gp.value not in (0.0, 1.0):
            unmatched += 1  # NaN, or any other value, is neither a good nor a bad part
            continue
        events.append(ProductionEvent(timestamp_ms=gp.timestamp, good=gp.value == 1.0, cycle_time_s=cycle_time))
    unmatched += sum(len(left) for left in cycles_at.values())  # cycle times nobody reported a result for

    if not events:
        reason = "no production_event data found for this source/window"
        if unmatched:
            reason += f" ({unmatched} 'good' readings had no matching 'cycleTimeS' at the same timestamp)"
        raise ReportError(reason)

    try:
        report = compute_oee(events, planned_time_s=planned_time_s, ideal_cycle_time_s=ideal_cycle_time_s)
    except OEEError as e:
        raise ReportError(str(e)) from e
    # `unmatched` used to only ever surface in the ReportError
    # message above - reachable only when EVERY good/cycleTimeS pair
    # failed to line up. A PARTIAL mismatch (some cycles complete, some
    # not) silently computed a real OEEReport from fewer events than
    # actually exist, with no trace anywhere that any were dropped - an
    # incomplete cycle just disappeared from the report. Attached to
    # every successful report now, not only the all-failed case.
    # A planned time longer than the window itself cannot be right either.
    planned_exceeds_window = planned_time_s > (end_ms - start_ms) / 1000
    return replace(
        report,
        unmatched_count=unmatched,
        inconsistent_input=report.inconsistent_input or planned_exceeds_window,
        provenance=Provenance(
            source_id=source_id,
            kind=PRODUCTION_EVENT_KIND,
            fields=(GOOD_FIELD, CYCLE_TIME_FIELD),
            window_start_ms=start_ms,
            window_end_ms=end_ms,
            points_used=len(good_points) + len(cycle_points),
            generator_version=__version__,
        ),
    )


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
    _check_window(start_ms, end_ms)
    points = _query_all_or_raise(client, source_id=source_id, kind=kind, field=field, start_ms=start_ms, end_ms=end_ms)
    timestamps = [p.timestamp for p in points]
    report = compute_availability(
        timestamps,
        window_start_ms=start_ms,
        window_end_ms=end_ms,
        expected_interval_ms=expected_interval_ms,
        gap_factor=gap_factor,
    )
    return replace(
        report,
        provenance=Provenance(
            source_id=source_id,
            kind=kind,
            fields=(field,),
            window_start_ms=start_ms,
            window_end_ms=end_ms,
            points_used=len(points),
            generator_version=__version__,
        ),
    )


# The most sources one fleet report takes: each one is two queries.
MAX_FLEET_SOURCES = 50


@dataclass(frozen=True)
class FleetOEEReport:
    """OEE of several sources (robots, cells) over the same window.

    `sources` holds the report of every source that could be built and
    `failed` the reason of every one that could not, so one robot without data
    does not hide the others. `combined` weights each source by its own
    counts and times: availability is all the operating time over all the
    planned time, performance all the ideal time over all the operating time
    (clamped), quality all the good parts over all the parts. `bottleneck` is
    the source with the lowest OEE and the factor (availability, performance
    or quality) that pulls it down; it is None with fewer than two sources."""

    sources: dict[str, OEEReport]
    failed: dict[str, str]
    combined: OEEReport | None
    bottleneck: dict[str, object] | None = None
    formula_version: str = field(default="oee-v1")


def combine_oee(reports: dict[str, OEEReport], *, planned_time_s: float, ideal_cycle_time_s: float) -> OEEReport:
    """The OEE of the whole group from the report of each source (see FleetOEEReport)."""
    if not reports:
        raise ReportError("no source has a report to combine")
    total = sum(r.total_count for r in reports.values())
    good = sum(r.good_count for r in reports.values())
    operating = sum(r.operating_time_s for r in reports.values())
    planned = planned_time_s * len(reports)
    availability = min(operating / planned, 1.0)
    performance = min(ideal_cycle_time_s * total / operating, 1.0) if operating > 0 else 0.0
    quality = good / total
    joined = "\n".join(f"{name}|{r.input_fingerprint}" for name, r in sorted(reports.items()))
    return OEEReport(
        availability=availability,
        performance=performance,
        quality=quality,
        oee=availability * performance * quality,
        total_count=total,
        good_count=good,
        operating_time_s=operating,
        formula_version=next(iter(reports.values())).formula_version,
        input_fingerprint=hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        unmatched_count=sum(r.unmatched_count for r in reports.values()),
        inconsistent_input=any(r.inconsistent_input for r in reports.values()) or operating > planned,
    )


def find_bottleneck(reports: dict[str, OEEReport]) -> dict[str, object] | None:
    """The source with the lowest OEE and its weakest factor; None with fewer than two sources."""
    if len(reports) < 2:
        return None
    name, report = min(reports.items(), key=lambda item: (item[1].oee, item[0]))
    factors = {"availability": report.availability, "performance": report.performance, "quality": report.quality}
    weakest = min(factors, key=lambda f: (factors[f], f))
    return {"sourceId": name, "oee": report.oee, "weakestFactor": weakest, "weakestValue": factors[weakest]}


def fleet_oee_from_datalake(
    client: DatalakeClient,
    *,
    source_ids: list[str],
    start_ms: int,
    end_ms: int,
    planned_time_s: float,
    ideal_cycle_time_s: float,
) -> FleetOEEReport:
    """OEE of each source and of the group (see FleetOEEReport). A source that
    has no usable data goes to `failed` with its reason; a DATALAKE that cannot
    be reached at all raises its own DatalakeError, because then no source can
    be judged."""
    names = list(dict.fromkeys(source_ids))
    if not names:
        raise ReportError("give at least one source")
    if len(names) > MAX_FLEET_SOURCES:
        raise ReportError(f"at most {MAX_FLEET_SOURCES} sources per report")
    sources: dict[str, OEEReport] = {}
    failed: dict[str, str] = {}
    for name in names:
        try:
            sources[name] = oee_from_datalake(
                client,
                source_id=name,
                start_ms=start_ms,
                end_ms=end_ms,
                planned_time_s=planned_time_s,
                ideal_cycle_time_s=ideal_cycle_time_s,
            )
        except ReportError as error:
            failed[name] = str(error)
    combined = combine_oee(sources, planned_time_s=planned_time_s, ideal_cycle_time_s=ideal_cycle_time_s) if sources else None
    return FleetOEEReport(sources=sources, failed=failed, combined=combined, bottleneck=find_bottleneck(sources))

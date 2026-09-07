# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/export.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Real, reproducible report export - a fixed field order and fixed float
formatting so the exact same report + range/filters always serializes to
the exact same bytes, plus the range/filters/formula_version/
input_fingerprint every real export needs to be traceable (promotion
audit line 653-654), not just today's report fields with no provenance.
"""
from __future__ import annotations

import csv
import io
from dataclasses import asdict

from .availability import AvailabilityReport
from .oee import OEEReport

OEE_FIELD_ORDER = [
    "formula_version",
    "input_fingerprint",
    "availability",
    "performance",
    "quality",
    "oee",
    "total_count",
    "good_count",
    "operating_time_s",
]

AVAILABILITY_FIELD_ORDER = [
    "formula_version",
    "input_fingerprint",
    "window_start_ms",
    "window_end_ms",
    "downtime_ms",
    "availability",
]


def _format_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _render_csv(
    field_order: list[str],
    row: dict[str, object],
    *,
    source_id: str,
    start_ms: int,
    end_ms: int,
    filters: dict[str, str] | None,
) -> str:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["sourceId", source_id])
    writer.writerow(["startMs", start_ms])
    writer.writerow(["endMs", end_ms])
    for key in sorted((filters or {}).keys()):
        writer.writerow([f"filter:{key}", filters[key]])
    writer.writerow([])
    writer.writerow(field_order)
    writer.writerow([_format_value(row[field]) for field in field_order])
    return buf.getvalue()


def export_oee_csv(
    report: OEEReport,
    *,
    source_id: str,
    start_ms: int,
    end_ms: int,
    filters: dict[str, str] | None = None,
) -> str:
    """A real, reproducible CSV rendering of one OEEReport - calling this
    twice with the same report and range/filters always returns the exact
    same string, byte for byte."""
    return _render_csv(OEE_FIELD_ORDER, asdict(report), source_id=source_id, start_ms=start_ms, end_ms=end_ms, filters=filters)


def export_availability_csv(
    report: AvailabilityReport,
    *,
    source_id: str,
    start_ms: int,
    end_ms: int,
    filters: dict[str, str] | None = None,
) -> str:
    """A real, reproducible CSV rendering of one AvailabilityReport - see
    export_oee_csv for the reproducibility guarantee."""
    return _render_csv(
        AVAILABILITY_FIELD_ORDER, asdict(report), source_id=source_id, start_ms=start_ms, end_ms=end_ms, filters=filters
    )

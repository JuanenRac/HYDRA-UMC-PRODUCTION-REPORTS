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
    provenance = row.get("provenance")
    if provenance:
        writer.writerow(["pointsUsed", provenance["points_used"]])
        writer.writerow(["generatorVersion", provenance["generator_version"]])
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


# =============================================================================
# Real HTML export with embedded, hand-generated SVG charts.
#
# No charting library, no external JS/CSS (every asset - the <style> block
# and the <svg> markup - is inline in the one returned string, same
# "no heavy dependencies" discipline this project's own README already
# states for Pandas) - a bar chart of 3-4 values and a single-row
# downtime timeline are both simple enough to hand-emit as plain SVG
# shapes (<rect>/<text>) directly, matching this project's own existing
# hand-rolled CSV renderer just above rather than reaching for a plotting
# dependency to draw four rectangles.
# =============================================================================

_HTML_ESCAPE = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}


def _escape_html(value: object) -> str:
    text = str(value)
    for char, escaped in _HTML_ESCAPE.items():
        text = text.replace(char, escaped)
    return text


def _svg_percent_bar_chart(items: list[tuple[str, float]], *, width: int = 480) -> str:
    """A real, plain horizontal bar chart for a handful of 0.0-1.0
    fractions (OEE's own Availability/Performance/Quality/OEE) - each row
    is one hand-emitted <rect> scaled by the real value, plus its label
    and percentage, nothing computed by a charting library."""
    row_height = 34
    bar_height = 18
    label_width = 110
    bar_max_width = width - label_width - 60
    height = row_height * len(items) + 10
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="report chart">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="none"/>')
    for index, (label, value) in enumerate(items):
        clamped = max(0.0, min(1.0, value))
        y = index * row_height + 6
        bar_width = round(bar_max_width * clamped, 2)
        parts.append(f'<text x="0" y="{y + bar_height - 4}" font-family="sans-serif" font-size="13" fill="#111827">{_escape_html(label)}</text>')
        parts.append(f'<rect x="{label_width}" y="{y}" width="{bar_max_width}" height="{bar_height}" fill="#e5e7eb"/>')
        parts.append(f'<rect x="{label_width}" y="{y}" width="{bar_width}" height="{bar_height}" fill="#2563eb"/>')
        parts.append(
            f'<text x="{label_width + bar_max_width + 8}" y="{y + bar_height - 4}" font-family="sans-serif" font-size="13" fill="#111827">{clamped * 100:.1f}%</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _svg_availability_timeline(report: AvailabilityReport, *, width: int = 760) -> str:
    """A real single-row timeline: the whole report window as one green
    bar, with every real DowntimePeriod drawn as a red segment at its own
    proportional position/width - the same downtime_periods a caller would
    otherwise only see as raw start/end millisecond pairs in the JSON
    response, made visible at a glance."""
    height = 70
    margin = 20
    track_width = width - 2 * margin
    window_span = max(report.window_end_ms - report.window_start_ms, 1)

    def x_for(ms: int) -> float:
        fraction = (ms - report.window_start_ms) / window_span
        return margin + max(0.0, min(1.0, fraction)) * track_width

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="availability timeline">']
    parts.append(f'<rect x="{margin}" y="20" width="{track_width}" height="24" fill="#16a34a"/>')
    for period in report.downtime_periods:
        x_start = x_for(period.start_ms)
        x_end = x_for(period.end_ms)
        seg_width = max(x_end - x_start, 1.0)
        parts.append(f'<rect x="{x_start:.2f}" y="20" width="{seg_width:.2f}" height="24" fill="#dc2626"/>')
    parts.append(f'<text x="{margin}" y="60" font-family="sans-serif" font-size="12" fill="#374151">{report.window_start_ms}</text>')
    end_label = str(report.window_end_ms)
    parts.append(
        f'<text x="{margin + track_width}" y="60" font-family="sans-serif" font-size="12" fill="#374151" text-anchor="end">{end_label}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _render_html(
    *,
    title: str,
    field_order: list[str],
    row: dict[str, object],
    chart_svg: str,
    source_id: str,
    start_ms: int,
    end_ms: int,
    filters: dict[str, str] | None,
) -> str:
    """Shared real HTML document shell for both report kinds - one
    self-contained page (inline <style>, inline <svg>, no external
    request of any kind) that renders correctly opened directly from
    disk, exactly like this project's own CSV export needs no external
    tool to read."""
    filter_rows = "".join(
        f"<tr><th>filter:{_escape_html(key)}</th><td>{_escape_html((filters or {})[key])}</td></tr>"
        for key in sorted((filters or {}).keys())
    )
    field_rows = "".join(
        f"<tr><th>{_escape_html(field)}</th><td>{_escape_html(_format_value(row[field]))}</td></tr>" for field in field_order
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_escape_html(title)}</title>
<style>
body {{ font-family: sans-serif; margin: 2rem; color: #111827; background: #ffffff; }}
h1 {{ font-size: 1.25rem; }}
table {{ border-collapse: collapse; margin: 1rem 0; }}
th, td {{ text-align: left; padding: 0.25rem 0.75rem 0.25rem 0; border-bottom: 1px solid #e5e7eb; }}
th {{ color: #6b7280; font-weight: 600; }}
.chart {{ margin: 1.5rem 0; }}
</style>
</head>
<body>
<h1>{_escape_html(title)}</h1>
<table>
<tr><th>sourceId</th><td>{_escape_html(source_id)}</td></tr>
<tr><th>startMs</th><td>{start_ms}</td></tr>
<tr><th>endMs</th><td>{end_ms}</td></tr>
{filter_rows}
</table>
<div class="chart">
{chart_svg}
</div>
<table>
{field_rows}
</table>
</body>
</html>
"""


def export_oee_html(
    report: OEEReport,
    *,
    source_id: str,
    start_ms: int,
    end_ms: int,
    filters: dict[str, str] | None = None,
) -> str:
    """A real, reproducible, self-contained HTML rendering of one
    OEEReport for viewing in a browser - a bar chart of its own real
    Availability/Performance/Quality/OEE fractions, plus the exact same
    field data export_oee_csv already carries. Calling this twice with
    the same report and range/filters always returns the exact same
    string, byte for byte, same guarantee as export_oee_csv."""
    chart = _svg_percent_bar_chart(
        [
            ("Availability", report.availability),
            ("Performance", report.performance),
            ("Quality", report.quality),
            ("OEE", report.oee),
        ]
    )
    return _render_html(
        title=f"OEE report - {source_id}",
        field_order=OEE_FIELD_ORDER,
        row=asdict(report),
        chart_svg=chart,
        source_id=source_id,
        start_ms=start_ms,
        end_ms=end_ms,
        filters=filters,
    )


def export_availability_html(
    report: AvailabilityReport,
    *,
    source_id: str,
    start_ms: int,
    end_ms: int,
    filters: dict[str, str] | None = None,
) -> str:
    """A real, reproducible, self-contained HTML rendering of one
    AvailabilityReport for viewing in a browser - a single-row timeline of
    its own real downtime_periods against the report window, plus the
    exact same field data export_availability_csv already carries. Same
    byte-for-byte reproducibility guarantee as export_availability_csv."""
    chart = _svg_availability_timeline(report)
    return _render_html(
        title=f"Availability report - {source_id}",
        field_order=AVAILABILITY_FIELD_ORDER,
        row=asdict(report),
        chart_svg=chart,
        source_id=source_id,
        start_ms=start_ms,
        end_ms=end_ms,
        filters=filters,
    )

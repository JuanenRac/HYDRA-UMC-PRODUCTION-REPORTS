# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_export.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

from hydra_umc_production_reports.availability import compute_availability
from hydra_umc_production_reports.export import (
    export_availability_csv,
    export_availability_html,
    export_oee_csv,
    export_oee_html,
)
from hydra_umc_production_reports.oee import ProductionEvent, compute_oee


def _real_oee_report():
    events = [ProductionEvent(timestamp_ms=i * 1000, good=(i < 8), cycle_time_s=5.0) for i in range(10)]
    return compute_oee(events, planned_time_s=100.0, ideal_cycle_time_s=4.0)


def _real_availability_report():
    timestamps = [0, 1000, 8000, 9000, 10000]
    return compute_availability(timestamps, window_start_ms=0, window_end_ms=10000, expected_interval_ms=1000.0)


def test_export_oee_csv_is_byte_for_byte_reproducible() -> None:
    report = _real_oee_report()
    csv_a = export_oee_csv(report, source_id="robot-1", start_ms=0, end_ms=10000)
    csv_b = export_oee_csv(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert csv_a == csv_b


def test_export_oee_csv_carries_range_and_formula_version() -> None:
    report = _real_oee_report()
    csv_text = export_oee_csv(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert "sourceId,robot-1" in csv_text
    assert "startMs,0" in csv_text
    assert "endMs,10000" in csv_text
    assert report.formula_version in csv_text
    assert report.input_fingerprint in csv_text


def test_export_oee_csv_includes_sorted_filters() -> None:
    report = _real_oee_report()
    csv_text = export_oee_csv(report, source_id="robot-1", start_ms=0, end_ms=10000, filters={"cell": "A", "plant": "Turin"})
    lines = csv_text.splitlines()
    filter_lines = [line for line in lines if line.startswith("filter:")]
    # sorted() puts "cell" before "plant" alphabetically - a real,
    # deterministic order, not dict insertion order.
    assert filter_lines == ["filter:cell,A", "filter:plant,Turin"]


def test_export_oee_csv_differs_for_a_real_different_report() -> None:
    report_a = _real_oee_report()
    events_b = [ProductionEvent(timestamp_ms=i * 1000, good=True, cycle_time_s=5.0) for i in range(10)]
    report_b = compute_oee(events_b, planned_time_s=100.0, ideal_cycle_time_s=4.0)
    csv_a = export_oee_csv(report_a, source_id="robot-1", start_ms=0, end_ms=10000)
    csv_b = export_oee_csv(report_b, source_id="robot-1", start_ms=0, end_ms=10000)
    assert csv_a != csv_b


def test_export_availability_csv_is_byte_for_byte_reproducible() -> None:
    report = _real_availability_report()
    csv_a = export_availability_csv(report, source_id="robot-1", start_ms=0, end_ms=10000)
    csv_b = export_availability_csv(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert csv_a == csv_b


def test_export_availability_csv_carries_range_and_formula_version() -> None:
    report = _real_availability_report()
    csv_text = export_availability_csv(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert "sourceId,robot-1" in csv_text
    assert report.formula_version in csv_text
    assert report.input_fingerprint in csv_text


# ---------------------------------------------------------------------------
# Real HTML export with embedded SVG charts.
# ---------------------------------------------------------------------------


def test_export_oee_html_is_byte_for_byte_reproducible() -> None:
    report = _real_oee_report()
    html_a = export_oee_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    html_b = export_oee_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert html_a == html_b


def test_export_oee_html_is_self_contained_with_no_external_assets() -> None:
    report = _real_oee_report()
    html_text = export_oee_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert "<!DOCTYPE html>" in html_text
    assert "<svg" in html_text
    # No external script/stylesheet/font/CDN reference of any kind - the
    # whole point of hand-emitting SVG instead of a charting library.
    assert "<script" not in html_text
    # "http://www.w3.org/2000/svg" is the SVG XML namespace URI, declared
    # inline on every real <svg> element - not a network fetch. The real
    # thing this asserts against is an external request for an asset (a
    # stylesheet/script/font/CDN reference), which xmlns is not.
    assert "<link" not in html_text
    assert 'rel="stylesheet"' not in html_text
    assert "cdn." not in html_text


def test_export_oee_html_carries_the_real_report_fields_and_range() -> None:
    report = _real_oee_report()
    html_text = export_oee_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert "robot-1" in html_text
    assert report.formula_version in html_text
    assert report.input_fingerprint in html_text
    # The real Availability/Performance/Quality/OEE bar chart rows.
    assert "Availability" in html_text
    assert "Performance" in html_text
    assert "Quality" in html_text
    assert "OEE" in html_text


def test_export_oee_html_escapes_a_real_hostile_source_id() -> None:
    report = _real_oee_report()
    html_text = export_oee_html(report, source_id="<script>alert(1)</script>", start_ms=0, end_ms=10000)
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text


def test_export_availability_html_is_byte_for_byte_reproducible() -> None:
    report = _real_availability_report()
    html_a = export_availability_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    html_b = export_availability_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    assert html_a == html_b


def test_export_availability_html_draws_one_svg_segment_per_real_downtime_period() -> None:
    report = _real_availability_report()
    assert len(report.downtime_periods) > 0, "the fixture must actually exercise a real gap"
    html_text = export_availability_html(report, source_id="robot-1", start_ms=0, end_ms=10000)
    # Track bar (green) + one red <rect> per real downtime period.
    assert html_text.count('fill="#dc2626"') == len(report.downtime_periods)
    assert 'fill="#16a34a"' in html_text

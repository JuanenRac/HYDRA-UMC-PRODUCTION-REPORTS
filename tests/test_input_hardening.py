# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_input_hardening.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Inputs that used to produce a wrong or missing answer: non-finite numbers,
network failures in the middle of an answer, window edges, repeated
timestamps, shift windows, access control and the bound on concurrent work."""
from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date

import pytest

from fake_datalake import running_fake_datalake
from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.availability import AvailabilityError, compute_availability
from hydra_umc_production_reports.datalake_client import DatalakeClient, DatalakeError
from hydra_umc_production_reports.export import export_oee_csv, export_oee_html
from hydra_umc_production_reports.main import startup_problem
from hydra_umc_production_reports.oee import OEEError, ProductionEvent, compute_oee
from hydra_umc_production_reports.reports import oee_from_datalake
from hydra_umc_production_reports.shift import ShiftSchedule, shift_window_ms


def _get(url: str, token: str | None = None) -> tuple[int, str]:
    request = urllib.request.Request(url)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


@contextmanager
def reports_server(datalake_url: str, **options):
    server = ReportsServer(("127.0.0.1", 0), DatalakeClient(datalake_url, retry_delay_seconds=0, **{}), **options)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _events(fake, count: int, *, step_ms: int = 1000, cycle_s: float = 2.0) -> None:
    points = []
    for i in range(count):
        ts = i * step_ms
        points.append({"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0})
        points.append({"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": cycle_s})
    fake.points = points


# ---------------------------------------------------------------- non-finite numbers
@pytest.mark.parametrize("bad", ["nan", "inf", "-inf", "abc", "0", "-5"])
def test_a_planned_time_or_ideal_cycle_that_is_not_a_positive_finite_number_is_a_400(bad: str) -> None:
    with running_fake_datalake() as (url, fake), reports_server(url) as (base, _):
        _events(fake, 3)
        for param in ("plannedTimeS", "idealCycleTimeS"):
            q = {"plannedTimeS": "10", "idealCycleTimeS": "2", param: bad}
            status, body = _get(f"{base}/reports/oee?sourceId=r&start=0&end=5000&plannedTimeS={q['plannedTimeS']}&idealCycleTimeS={q['idealCycleTimeS']}")
            assert status == 400, (param, bad, body)
            assert "NaN" not in body and "Infinity" not in body


@pytest.mark.parametrize(
    "extra",
    ["&expectedIntervalMs=nan", "&expectedIntervalMs=inf", "&expectedIntervalMs=0", "&gapFactor=nan", "&gapFactor=-1", "&gapFactor=0.5"],
)
def test_availability_refuses_non_finite_or_nonsensical_numbers_instead_of_reporting_100_percent(extra: str) -> None:
    with running_fake_datalake() as (url, _fake), reports_server(url) as (base, _):
        query = "sourceId=r&kind=k&field=f&start=0&end=60000"
        if "expectedIntervalMs" not in extra:
            query += "&expectedIntervalMs=1000"
        status, body = _get(f"{base}/reports/availability?{query}{extra}")
        assert status == 400, body


def test_the_library_functions_refuse_non_finite_numbers_too() -> None:
    events = [ProductionEvent(timestamp_ms=0, good=True, cycle_time_s=1.0)]
    for bad in (float("nan"), float("inf")):
        with pytest.raises(OEEError):
            compute_oee(events, planned_time_s=bad, ideal_cycle_time_s=1.0)
        with pytest.raises(OEEError):
            compute_oee(events, planned_time_s=10.0, ideal_cycle_time_s=bad)
        with pytest.raises(AvailabilityError):
            compute_availability([], window_start_ms=0, window_end_ms=1000, expected_interval_ms=bad)
        with pytest.raises(AvailabilityError):
            compute_availability([], window_start_ms=0, window_end_ms=1000, expected_interval_ms=10.0, gap_factor=bad)
    with pytest.raises(AvailabilityError):
        compute_availability([], window_start_ms=0, window_end_ms=1000, expected_interval_ms=10.0, gap_factor=0.0)


# ---------------------------------------------------------------- network failures
def _silent_server(behaviour: str) -> tuple[str, threading.Thread, socket.socket]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)

    def serve() -> None:
        listener.settimeout(5)
        try:
            while True:
                conn, _ = listener.accept()
                conn.recv(4096)
                if behaviour == "garbage":
                    body = b"this is not json"
                    conn.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                        % (len(body), body)
                    )
                # "close": say nothing and hang up
                conn.close()
        except OSError:
            pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{listener.getsockname()[1]}", thread, listener


def test_a_datalake_that_hangs_up_without_answering_is_a_502_not_a_closed_socket() -> None:
    url, _thread, listener = _silent_server("close")
    try:
        with reports_server(url) as (base, _):
            status, body = _get(f"{base}/reports/oee?sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1")
            assert status == 502, body
    finally:
        listener.close()


def test_a_datalake_answer_that_is_not_json_is_a_502() -> None:
    url, _thread, listener = _silent_server("garbage")
    try:
        with reports_server(url) as (base, _):
            status, body = _get(f"{base}/reports/oee?sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1")
            assert status == 502, body
    finally:
        listener.close()


def test_a_timeout_while_the_answer_is_read_is_retried_and_then_reported() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)
    held: list[socket.socket] = []

    def serve() -> None:
        try:
            while True:
                conn, _ = listener.accept()
                held.append(conn)  # accepts and never answers
        except OSError:
            pass

    threading.Thread(target=serve, daemon=True).start()
    sleeps: list[float] = []
    client = DatalakeClient(
        f"http://127.0.0.1:{listener.getsockname()[1]}", timeout_s=0.3, max_attempts=2, sleep=sleeps.append
    )
    try:
        with pytest.raises(DatalakeError):
            client.query(source_id="r")
        assert sleeps, "a timeout is transient and must be retried"
    finally:
        listener.close()
        for conn in held:
            conn.close()


# ---------------------------------------------------------------- windows are [start, end)
def test_a_sample_on_the_window_edge_belongs_to_one_report_only() -> None:
    with running_fake_datalake() as (url, fake):
        # one cycle exactly at 5000: the end of the first window, the start of the second
        fake.points = [
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": 5000, "value": 1.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 5000, "value": 2.0},
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": 1000, "value": 1.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 1000, "value": 2.0},
        ]
        client = DatalakeClient(url)
        first = oee_from_datalake(client, source_id="r", start_ms=0, end_ms=5000, planned_time_s=5.0, ideal_cycle_time_s=2.0)
        second = oee_from_datalake(client, source_id="r", start_ms=5000, end_ms=10000, planned_time_s=5.0, ideal_cycle_time_s=2.0)
        assert first.total_count == 1
        assert second.total_count == 1


def test_availability_does_not_count_a_sample_at_the_end_of_the_window() -> None:
    report = compute_availability([0, 1000, 2000], window_start_ms=0, window_end_ms=2000, expected_interval_ms=1000.0)
    assert report.input_fingerprint == compute_availability(
        [0, 1000], window_start_ms=0, window_end_ms=2000, expected_interval_ms=1000.0
    ).input_fingerprint


def test_a_window_that_ends_before_it_starts_is_a_clear_400() -> None:
    with running_fake_datalake() as (url, _fake), reports_server(url) as (base, _):
        status, body = _get(f"{base}/reports/oee?sourceId=r&start=9000&end=1000&plannedTimeS=5&idealCycleTimeS=1")
        assert status == 400 and "end must be after start" in body


# ---------------------------------------------------------------- consistency of the figures
def test_inconsistent_input_is_flagged_when_the_cycles_add_up_to_more_than_the_planned_time() -> None:
    events = [ProductionEvent(timestamp_ms=i, good=True, cycle_time_s=10.0) for i in range(20)]
    report = compute_oee(events, planned_time_s=100.0, ideal_cycle_time_s=5.0)
    assert report.inconsistent_input is True
    ok = compute_oee(events[:5], planned_time_s=100.0, ideal_cycle_time_s=5.0)
    assert ok.inconsistent_input is False


def test_a_planned_time_longer_than_the_window_is_flagged() -> None:
    with running_fake_datalake() as (url, fake):
        _events(fake, 3)
        report = oee_from_datalake(DatalakeClient(url), source_id="r", start_ms=0, end_ms=5000, planned_time_s=600.0, ideal_cycle_time_s=2.0)
        assert report.inconsistent_input is True


def test_the_fingerprint_does_not_depend_on_the_order_of_events_that_share_a_timestamp() -> None:
    a = ProductionEvent(timestamp_ms=1000, good=True, cycle_time_s=2.0)
    b = ProductionEvent(timestamp_ms=1000, good=False, cycle_time_s=3.0)
    one = compute_oee([a, b], planned_time_s=100.0, ideal_cycle_time_s=2.0)
    other = compute_oee([b, a], planned_time_s=100.0, ideal_cycle_time_s=2.0)
    assert one.input_fingerprint == other.input_fingerprint


# ---------------------------------------------------------------- joining the two fields
def test_cycles_sharing_a_millisecond_are_paired_in_order_and_orphans_are_counted() -> None:
    with running_fake_datalake() as (url, fake):
        fake.points = [
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": 1000, "value": 1.0},
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": 1000, "value": 0.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 1000, "value": 2.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 1000, "value": 4.0},
            # a cycle time nobody reported a result for
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 3000, "value": 9.0},
        ]
        report = oee_from_datalake(DatalakeClient(url), source_id="r", start_ms=0, end_ms=10000, planned_time_s=10.0, ideal_cycle_time_s=2.0)
        assert report.total_count == 2
        assert report.operating_time_s == pytest.approx(6.0), "both cycle times must be used, not the last one twice"
        assert report.unmatched_count == 1


def test_a_good_value_that_is_neither_zero_nor_one_is_not_a_good_part() -> None:
    with running_fake_datalake() as (url, fake):
        fake.points = [
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": 1000, "value": 1.0},
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": 2000, "value": 7.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 1000, "value": 2.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": 2000, "value": 2.0},
        ]
        report = oee_from_datalake(DatalakeClient(url), source_id="r", start_ms=0, end_ms=10000, planned_time_s=10.0, ideal_cycle_time_s=2.0)
        assert (report.total_count, report.good_count, report.unmatched_count) == (1, 1, 1)


# ---------------------------------------------------------------- exports
def test_the_exports_carry_the_discarded_count_the_consistency_flag_and_the_provenance() -> None:
    with running_fake_datalake() as (url, fake):
        _events(fake, 3)
        report = oee_from_datalake(DatalakeClient(url), source_id="r", start_ms=0, end_ms=10000, planned_time_s=10.0, ideal_cycle_time_s=2.0)
        csv_text = export_oee_csv(report, source_id="r", start_ms=0, end_ms=10000)
        assert "unmatched_count" in csv_text and "inconsistent_input" in csv_text and "exportVersion,2" in csv_text
        html = export_oee_html(report, source_id="r", start_ms=0, end_ms=10000)
        assert "unmatched_count" in html and "pointsUsed" in html and "generatorVersion" in html


def test_a_formula_hidden_behind_a_space_is_neutralised_in_the_csv() -> None:
    with running_fake_datalake() as (url, fake):
        _events(fake, 2)
        report = oee_from_datalake(DatalakeClient(url), source_id="r", start_ms=0, end_ms=10000, planned_time_s=10.0, ideal_cycle_time_s=2.0)
        csv_text = export_oee_csv(report, source_id="  =cmd()", start_ms=0, end_ms=10000, filters={"a": "|x", "b": "%y"})
        assert "'  =cmd()" in csv_text and "'|x" in csv_text and "'%y" in csv_text


def test_an_unknown_export_format_is_refused_before_anything_is_queried() -> None:
    with running_fake_datalake() as (url, _fake), reports_server(url) as (base, _):
        status, body = _get(f"{base}/reports/oee/export?format=pdf&sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1")
        assert status == 400 and "unknown format" in body


# ---------------------------------------------------------------- shifts
def test_a_schedule_of_five_4_8_hour_shifts_is_valid() -> None:
    schedule = ShiftSchedule(shift_length_hours=4.8, first_shift_start_hour=0.0)
    assert schedule.shifts_per_day == 5
    start, end = shift_window_ms(schedule, date(2026, 1, 2), 4)
    assert end - start == 4 * 3_600_000 + 48 * 60_000


def test_a_report_can_be_asked_for_by_day_and_shift() -> None:
    with running_fake_datalake() as (url, fake), reports_server(url) as (base, _):
        # second 8-hour shift of 1970-01-02, UTC: 08:00 to 16:00 of that day
        day_start = 86_400_000
        shift_start = day_start + 8 * 3_600_000
        fake.points = [
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": shift_start + 5000, "value": 1.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": shift_start + 5000, "value": 2.0},
            {"sourceId": "r", "kind": "production_event", "field": "good", "timestamp": day_start + 1000, "value": 1.0},
            {"sourceId": "r", "kind": "production_event", "field": "cycleTimeS", "timestamp": day_start + 1000, "value": 2.0},
        ]
        status, body = _get(f"{base}/reports/oee?sourceId=r&day=1970-01-02&shift=1&idealCycleTimeS=2")
        assert status == 200, body
        assert json.loads(body)["total_count"] == 1
        assert _get(f"{base}/reports/oee?sourceId=r&day=1970-01-02&shift=9&idealCycleTimeS=2")[0] == 400
        assert _get(f"{base}/reports/oee?sourceId=r&day=nope&idealCycleTimeS=2")[0] == 400
        assert _get(f"{base}/reports/oee?sourceId=r&day=1970-01-02&start=0&end=5&idealCycleTimeS=2")[0] == 400
        whole_day = _get(f"{base}/reports/oee?sourceId=r&day=1970-01-02&idealCycleTimeS=2")
        assert whole_day[0] == 200 and json.loads(whole_day[1])["total_count"] == 2
        avail = _get(f"{base}/reports/availability?sourceId=r&kind=production_event&field=good&day=1970-01-02&shift=1&expectedIntervalMs=60000")
        assert avail[0] == 200, avail


# ---------------------------------------------------------------- access and load
def test_a_token_is_required_for_reports_but_not_for_health() -> None:
    with running_fake_datalake() as (url, fake), reports_server(url, token="s3cret") as (base, _):
        _events(fake, 2)
        query = "sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1"
        assert _get(f"{base}/health")[0] == 200
        assert _get(f"{base}/reports/oee?{query}")[0] == 401
        assert _get(f"{base}/reports/oee?{query}", token="wrong")[0] == 401
        assert _get(f"{base}/stats")[0] == 401
        assert _get(f"{base}/reports/oee?{query}", token="s3cret")[0] == 200


def test_the_process_refuses_to_serve_the_network_without_a_token() -> None:
    assert startup_problem("127.0.0.1", None, False) is None
    assert startup_problem("localhost", None, False) is None
    assert startup_problem("::1", None, False) is None
    assert startup_problem("0.0.0.0", None, False) is not None
    assert startup_problem("192.168.0.5", "", False) is not None
    assert startup_problem("0.0.0.0", "token", False) is None
    assert startup_problem("0.0.0.0", None, True) is None


def test_only_so_many_reports_are_built_at_once() -> None:
    with running_fake_datalake() as (url, fake), reports_server(url, max_concurrent_reports=1) as (base, server):
        _events(fake, 2)
        assert server.slots.acquire(blocking=False)  # a report is "being built"
        try:
            status, body = _get(f"{base}/reports/oee?sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1")
            assert status == 503, body
            assert _get(f"{base}/health")[0] == 200, "liveness never waits for a slot"
        finally:
            server.slots.release()
        assert _get(f"{base}/reports/oee?sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1")[0] == 200


def test_an_unexpected_failure_still_answers_with_a_500() -> None:
    with running_fake_datalake() as (url, _fake), reports_server(url) as (base, server):
        def boom(**_kwargs):
            raise RuntimeError("boom")

        server.client.query = boom  # type: ignore[method-assign]
        status, body = _get(f"{base}/reports/oee?sourceId=r&start=0&end=5000&plannedTimeS=5&idealCycleTimeS=1")
        assert status == 500 and "internal error" in body

# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_aggregate.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""GET /aggregate of the data lake: the client with its cache, and the reports of a window too big to bring in.

Both sides are tested: the cache and the buckets against a stand-in server, and the sums and the gaps against the REAL
HYDRA-UMC-DATALAKE package (a sibling checkout; skipped when it is not there), comparing the answer from totals with the answer
from every point.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from hydra_umc_production_reports import reports
from hydra_umc_production_reports.availability import compute_availability_from_buckets
from hydra_umc_production_reports.datalake_client import Bucket, DatalakeClient, DatalakeError, cache_ttl_from_environment
from hydra_umc_production_reports.oee import OEEError, compute_oee, compute_oee_from_totals, ProductionEvent
from hydra_umc_production_reports.reports import ReportError, availability_from_datalake, oee_from_datalake

THIS_REPO = Path(__file__).resolve().parents[1]
DATALAKE_ROOT = Path(os.environ.get("HYDRA_UMC_DATALAKE_ROOT", str(THIS_REPO.parent / "HYDRA-UMC-DATALAKE")))
BASE_MS = (int(time.time()) - 7200) * 1000


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def datalake():
    src = DATALAKE_ROOT / "src"
    if not src.is_dir():
        pytest.skip(f"sibling HYDRA-UMC-DATALAKE checkout not found at {DATALAKE_ROOT}")
    sys.path.insert(0, str(src))
    from hydra_umc_datalake.api import DatalakeServer
    from hydra_umc_datalake.store import TimeSeriesStore

    server = DatalakeServer(("127.0.0.1", 0), TimeSeriesStore(":memory:"))
    counter = {"aggregate": 0, "series": 0}
    seen = server.RequestHandlerClass.do_GET

    def counting(handler):
        path = handler.path.split("?")[0].lstrip("/")
        if path in counter:
            counter[path] += 1
        return seen(handler)

    server.RequestHandlerClass.do_GET = counting
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base, counter
    finally:
        server.RequestHandlerClass.do_GET = seen
        server.shutdown()
        server.server_close()


def ingest(base: str, samples: list[dict]) -> None:
    for start in range(0, len(samples), 500):
        body = json.dumps({"samples": samples[start : start + 500]}).encode()
        request = urllib.request.Request(f"{base}/ingest/batch", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 202


def production(source: str, cycles: int, *, bad_every: int = 10, cycle_s: float = 2.0) -> list[dict]:
    return [
        {"sourceId": source, "kind": "production_event", "timestamp": BASE_MS + index * 1000, "fields": {"good": 0.0 if index % bad_every == 0 else 1.0, "cycleTimeS": cycle_s}}
        for index in range(cycles)
    ]


def test_cache_ttl_comes_from_the_environment():
    assert cache_ttl_from_environment({}) == 30.0
    assert cache_ttl_from_environment({"HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS": "5"}) == 5.0
    assert cache_ttl_from_environment({"HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS": "0"}) == 0.0
    assert cache_ttl_from_environment({"HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS": "-4"}) == 30.0
    assert cache_ttl_from_environment({"HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS": "soon"}) == 30.0


def test_aggregate_is_cached_until_it_expires_and_the_series_changes(datalake):
    base, counter = datalake
    ingest(base, production("arm-1", 20))
    clock = Clock()
    client = DatalakeClient(base, cache_ttl_seconds=10, clock=clock)
    ask = lambda: client.aggregate(source_id="arm-1", kind="production_event", field="good", start=BASE_MS, before=BASE_MS + 20_000, bucket_ms=20_000, agg="sum")

    first = ask()
    assert first == [Bucket(BASE_MS, 18.0, 20)]
    assert counter["aggregate"] == 1
    assert ask() == first  # inside the lifetime: nothing is asked
    assert (counter["aggregate"], counter["series"]) == (1, 1)

    clock.now += 11  # expired, but the series is the same: the entry is refreshed without asking for the buckets
    assert ask() == first
    assert (counter["aggregate"], counter["series"]) == (1, 2)
    assert client.cache_hits == 2 and client.cache_misses == 1

    ingest(base, [{"sourceId": "arm-1", "kind": "production_event", "timestamp": BASE_MS + 5_500, "fields": {"good": 1.0, "cycleTimeS": 2.0}}])
    clock.now += 11  # expired and the series changed: asked again
    assert ask() == [Bucket(BASE_MS, 19.0, 21)]
    assert counter["aggregate"] == 2


def test_ttl_zero_revalidates_every_time_and_no_cache_never_keeps(datalake):
    base, counter = datalake
    ingest(base, production("arm-1", 5))
    ask = lambda c: c.aggregate(source_id="arm-1", kind="production_event", field="good", start=BASE_MS, before=BASE_MS + 5_000, bucket_ms=5_000, agg="sum")
    strict = DatalakeClient(base, cache_ttl_seconds=0, clock=Clock())
    ask(strict), ask(strict), ask(strict)
    assert counter["aggregate"] == 1 and counter["series"] == 3
    off = DatalakeClient(base, cache_entries=0)
    before = counter["aggregate"]
    ask(off), ask(off)
    assert counter["aggregate"] == before + 2


def test_a_different_window_or_reducer_is_a_different_entry(datalake):
    base, counter = datalake
    ingest(base, production("arm-1", 20))
    client = DatalakeClient(base, cache_ttl_seconds=60)
    kw = dict(source_id="arm-1", kind="production_event", field="good", start=BASE_MS, bucket_ms=10_000)
    assert client.aggregate(before=BASE_MS + 10_000, agg="sum", **kw)[0].count == 10
    assert client.aggregate(before=BASE_MS + 20_000, agg="sum", **kw)[1].count == 10
    assert client.aggregate(before=BASE_MS + 10_000, agg="max", **kw)[0].value == 1.0
    assert counter["aggregate"] == 3


def test_oee_from_totals_equals_oee_from_every_event(datalake, monkeypatch):
    base, _ = datalake
    ingest(base, production("arm-1", 120))
    client = DatalakeClient(base)
    args = dict(source_id="arm-1", start_ms=BASE_MS, end_ms=BASE_MS + 120_000, planned_time_s=300.0, ideal_cycle_time_s=1.5)
    from_events = oee_from_datalake(client, **args)
    assert from_events.method == "events"

    monkeypatch.setattr(reports, "MAX_POINTS_PER_QUERY", 50)  # the window now holds "too many" events
    from_totals = oee_from_datalake(client, **args)
    assert from_totals.method == "aggregate"
    for name in ("availability", "performance", "quality", "oee", "total_count", "good_count", "operating_time_s", "unmatched_count"):
        assert getattr(from_totals, name) == pytest.approx(getattr(from_events, name)), name
    assert from_totals.provenance.points_used == 240
    assert from_totals.input_fingerprint != from_events.input_fingerprint


def test_oee_from_totals_reports_unpaired_cycles_and_refuses_what_is_not_a_count(datalake, monkeypatch):
    base, _ = datalake
    ingest(base, production("arm-1", 60))
    ingest(base, [{"sourceId": "arm-1", "kind": "production_event", "timestamp": BASE_MS + 70_000, "fields": {"cycleTimeS": 2.0}}])
    ingest(base, [{"sourceId": "arm-2", "kind": "production_event", "timestamp": BASE_MS + index * 1000, "fields": {"good": 2.0, "cycleTimeS": 1.0}} for index in range(60)])
    monkeypatch.setattr(reports, "MAX_POINTS_PER_QUERY", 50)
    client = DatalakeClient(base)
    report = oee_from_datalake(client, source_id="arm-1", start_ms=BASE_MS, end_ms=BASE_MS + 120_000, planned_time_s=300.0, ideal_cycle_time_s=1.5)
    assert report.unmatched_count == 1
    with pytest.raises(ReportError, match="not all 0 or 1"):
        oee_from_datalake(client, source_id="arm-2", start_ms=BASE_MS, end_ms=BASE_MS + 120_000, planned_time_s=300.0, ideal_cycle_time_s=1.5)


def test_availability_from_buckets_finds_the_same_gap_as_the_points(datalake, monkeypatch):
    base, _ = datalake
    samples = [
        {"sourceId": "cm5", "kind": "motor_temp", "timestamp": BASE_MS + t, "fields": {"value": 40.0}}
        for t in list(range(0, 20_001, 1000)) + list(range(60_000, 100_001, 1000))
    ]
    ingest(base, samples)
    client = DatalakeClient(base)
    args = dict(source_id="cm5", kind="motor_temp", field="value", start_ms=BASE_MS, end_ms=BASE_MS + 100_000, expected_interval_ms=1000.0)
    exact = availability_from_datalake(client, **args)
    assert exact.method == "points" and exact.downtime_ms == 40_000

    monkeypatch.setattr(reports, "MAX_POINTS_PER_QUERY", 30)
    bucketed = availability_from_datalake(client, **args)
    assert bucketed.method == "aggregate"
    assert len(bucketed.downtime_periods) == 1
    # exact to one bucket (1 s here): the real gap is 20 s to 60 s, the bucketed one never longer than it
    assert 38_000 <= bucketed.downtime_ms <= 40_000
    assert bucketed.availability == pytest.approx(exact.availability, abs=0.02)
    assert bucketed.provenance.points_used == 61


def test_availability_from_buckets_refuses_a_resolution_coarser_than_the_threshold(datalake, monkeypatch):
    base, _ = datalake
    ingest(base, [{"sourceId": "cm5", "kind": "motor_temp", "timestamp": BASE_MS + t, "fields": {"value": 1.0}} for t in range(0, 60_000, 1000)])
    monkeypatch.setattr(reports, "MAX_POINTS_PER_QUERY", 10)
    monkeypatch.setattr(reports, "MAX_AGGREGATE_BUCKETS", 10)  # a window that would need 6 s buckets with a 1 s interval
    with pytest.raises(ReportError, match="coarser than the gap threshold"):
        availability_from_datalake(
            DatalakeClient(base), source_id="cm5", kind="motor_temp", field="value",
            start_ms=BASE_MS, end_ms=BASE_MS + 60_000, expected_interval_ms=1000.0, gap_factor=3.0,
        )


def test_a_data_lake_without_aggregate_falls_back_to_the_points():
    from fake_datalake import running_fake_datalake

    with running_fake_datalake() as (url, server):
        server.points = [{"sourceId": "arm", "kind": "production_event", "field": f, "timestamp": BASE_MS + i * 1000, "value": v} for i in range(10) for f, v in (("good", 1.0), ("cycleTimeS", 2.0))]
        report = oee_from_datalake(DatalakeClient(url), source_id="arm", start_ms=BASE_MS, end_ms=BASE_MS + 10_000, planned_time_s=30.0, ideal_cycle_time_s=2.0)
        assert report.method == "events" and report.total_count == 10


def test_an_unreachable_data_lake_is_one_failure_not_three():
    waits = []
    client = DatalakeClient("http://127.0.0.1:1", max_attempts=2, retry_delay_seconds=0.01, sleep=waits.append)
    with pytest.raises(DatalakeError):
        oee_from_datalake(client, source_id="arm", start_ms=BASE_MS, end_ms=BASE_MS + 10_000, planned_time_s=30.0, ideal_cycle_time_s=2.0)
    assert len(waits) == 1


def test_compute_from_totals_validates_and_matches_compute_oee():
    events = [ProductionEvent(i, i % 4 != 0, 2.0) for i in range(40)]
    exact = compute_oee(events, planned_time_s=200.0, ideal_cycle_time_s=1.8)
    totals = compute_oee_from_totals(total_count=40, good_count=30, operating_time_s=80.0, planned_time_s=200.0, ideal_cycle_time_s=1.8)
    assert (totals.availability, totals.performance, totals.quality, totals.oee) == pytest.approx((exact.availability, exact.performance, exact.quality, exact.oee))
    for bad in (dict(total_count=0), dict(good_count=41), dict(operating_time_s=float("nan")), dict(planned_time_s=0.0), dict(ideal_cycle_time_s=float("inf"))):
        arguments = dict(total_count=40, good_count=30, operating_time_s=80.0, planned_time_s=200.0, ideal_cycle_time_s=1.8) | bad
        with pytest.raises(OEEError):
            compute_oee_from_totals(**arguments)


def test_bucket_availability_edges_and_inputs():
    report = compute_availability_from_buckets([0, 1000, 2000], bucket_ms=1000, window_start_ms=0, window_end_ms=10_000, expected_interval_ms=1000.0)
    assert [(p.start_ms, p.end_ms) for p in report.downtime_periods] == [(3000, 10_000)]
    full = compute_availability_from_buckets(list(range(0, 10_000, 1000)), bucket_ms=1000, window_start_ms=0, window_end_ms=10_000, expected_interval_ms=1000.0)
    assert full.downtime_periods == [] and full.availability == 1.0
    with pytest.raises(Exception):  # noqa: B017
        compute_availability_from_buckets([], bucket_ms=0, window_start_ms=0, window_end_ms=10, expected_interval_ms=1.0)

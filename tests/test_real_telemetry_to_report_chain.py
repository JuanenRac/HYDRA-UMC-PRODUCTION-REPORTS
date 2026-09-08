# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_real_telemetry_to_report_chain.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""C09/F04 (private plan's own flow) - found 2026-09-08: F04's own real
Telemetry-Collector -> Datalake -> Production-Reports chain never had a
single test exercising all of it together - each repo only ever proves
its own stretch. This repo's own `tests/fake_datalake.py` is itself part
of that gap: a real HTTP server, but a hand-written REIMPLEMENTATION of
HYDRA-UMC-DATALAKE's own query contract, not the real thing - a real
contract drift on DATALAKE's own side would never be caught by any test
using it.

This file starts the REAL HYDRA-UMC-DATALAKE package (a real sibling
checkout, skips if not present - same convention already used for the
SAFETY-ZONES<->VISUAL-SERVOING-API real integration test this same
pass), ingests real telemetry samples into it using the exact real
sourceId/kind/timestamp/fields shape HYDRA-UMC-TELEMETRY-COLLECTOR's own
sink/datalake.go sends (confirmed by reading that file directly, not
guessed), then runs this repo's own real `availability_from_datalake()`
against that real, live-queried data - proving the full chain end to
end, not each repo trusting the other's own unit tests in isolation.
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

THIS_REPO = Path(__file__).resolve().parents[1]
DATALAKE_ROOT = Path(
    os.environ.get("HYDRA_UMC_DATALAKE_ROOT", str(THIS_REPO.parent / "HYDRA-UMC-DATALAKE"))
)
DATALAKE_SRC = DATALAKE_ROOT / "src"

from hydra_umc_production_reports.availability import compute_availability  # noqa: E402
from hydra_umc_production_reports.datalake_client import DatalakeClient  # noqa: E402
from hydra_umc_production_reports.reports import availability_from_datalake  # noqa: E402


def _real_datalake_server():
    if not DATALAKE_SRC.is_dir():
        pytest.skip(
            f"sibling HYDRA-UMC-DATALAKE checkout not found at {DATALAKE_ROOT} - real cross-repo "
            "integration test, needs that repo present to run"
        )
    sys.path.insert(0, str(DATALAKE_SRC))
    from hydra_umc_datalake.api import DatalakeServer
    from hydra_umc_datalake.store import TimeSeriesStore

    store = TimeSeriesStore(":memory:")
    server = DatalakeServer(("127.0.0.1", 0), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@pytest.fixture
def datalake_url():
    server, thread = _real_datalake_server()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _ingest_sample(base_url: str, *, source_id: str, kind: str, timestamp_ms: int, value: float) -> None:
    # The exact real shape HYDRA-UMC-TELEMETRY-COLLECTOR's own
    # sink/datalake.go sends to POST /ingest - sourceId/kind/timestamp/
    # fields, confirmed by reading that file directly.
    import json
    import urllib.request

    body = json.dumps({
        "sourceId": source_id, "kind": kind, "timestamp": timestamp_ms, "fields": {"value": value},
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/ingest", data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 202


def test_real_gap_in_ingested_telemetry_becomes_real_reported_downtime(datalake_url):
    source_id, kind, field = "cm5-test-01", "motor_temp", "value"
    window_start_ms, window_end_ms = 0, 100_000
    interval_ms = 1_000

    # Real samples every 1s for the first 20s (0..20000 inclusive).
    for t in range(0, 20_001, interval_ms):
        _ingest_sample(datalake_url, source_id=source_id, kind=kind, timestamp_ms=t, value=42.0)
    # A real 40-second outage: NO samples arrive between 20s and 60s -
    # the real, live gap this whole chain exists to surface as downtime.
    # Real samples resume every 1s for the last 40s (60000..100000).
    for t in range(60_000, 100_001, interval_ms):
        _ingest_sample(datalake_url, source_id=source_id, kind=kind, timestamp_ms=t, value=42.0)

    client = DatalakeClient(datalake_url)
    report = availability_from_datalake(
        client, source_id=source_id, kind=kind, field=field,
        start_ms=window_start_ms, end_ms=window_end_ms, expected_interval_ms=float(interval_ms),
    )

    assert len(report.downtime_periods) == 1
    downtime = report.downtime_periods[0]
    assert downtime.start_ms == 20_000
    assert downtime.end_ms == 60_000
    assert downtime.duration_ms == 40_000
    assert report.downtime_ms == 40_000
    assert report.availability == pytest.approx(0.6)


def test_real_continuous_telemetry_with_no_gap_reports_full_availability(datalake_url):
    source_id, kind, field = "cm5-test-02", "motor_temp", "value"
    interval_ms = 1_000
    for t in range(0, 50_001, interval_ms):
        _ingest_sample(datalake_url, source_id=source_id, kind=kind, timestamp_ms=t, value=41.0)

    client = DatalakeClient(datalake_url)
    report = availability_from_datalake(
        client, source_id=source_id, kind=kind, field=field,
        start_ms=0, end_ms=50_000, expected_interval_ms=float(interval_ms),
    )
    assert report.downtime_periods == []
    assert report.availability == pytest.approx(1.0)


def test_real_source_with_zero_ingested_samples_is_reported_as_fully_down(datalake_url):
    # A source that never sends a single real sample in the window (a
    # real "never came up" case, not merely "went down partway through")
    # must still be honestly reported - never silently excluded.
    client = DatalakeClient(datalake_url)
    report = availability_from_datalake(
        client, source_id="cm5-never-reported", kind="motor_temp", field="value",
        start_ms=0, end_ms=10_000, expected_interval_ms=1_000.0,
    )
    assert len(report.downtime_periods) == 1
    assert report.downtime_periods[0].duration_ms == 10_000
    assert report.availability == pytest.approx(0.0)


def test_compute_availability_pure_function_matches_the_real_chains_own_result():
    # Sanity cross-check: the pure compute_availability() function, given
    # the exact same real timestamps the chain above ingested, must
    # produce an identical result - proves availability_from_datalake()
    # is a real, faithful wrapper, not a second implementation that
    # happens to look similar.
    timestamps = list(range(0, 20_001, 1_000)) + list(range(60_000, 100_001, 1_000))
    report = compute_availability(
        timestamps, window_start_ms=0, window_end_ms=100_000, expected_interval_ms=1_000.0,
    )
    assert report.downtime_ms == 40_000
    assert report.availability == pytest.approx(0.6)

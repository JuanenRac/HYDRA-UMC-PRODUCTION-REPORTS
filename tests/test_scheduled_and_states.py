# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_scheduled_and_states.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest
from fake_datalake import running_fake_datalake

from hydra_umc_production_reports.api import ReportsServer
from hydra_umc_production_reports.availability import AvailabilityError, compute_availability_from_states
from hydra_umc_production_reports.datalake_client import DatalakeClient
from hydra_umc_production_reports.scheduled import (
    ScheduleError,
    main,
    parse_config,
    period_window,
    run_schedule,
    verify_folder,
)


def test_state_events_exact_downtime() -> None:
    # up from before the window, down 2000-5000, up again, down at 9000 until the end
    report = compute_availability_from_states(
        [(2000, False), (5000, True), (9000, False)], window_start_ms=0, window_end_ms=10000, initial_up=True
    )
    assert [(p.start_ms, p.end_ms) for p in report.downtime_periods] == [(2000, 5000), (9000, 10000)]
    assert report.downtime_ms == 4000
    assert report.availability == pytest.approx(0.6)
    assert report.unknown_ms == 0
    assert report.method == "state"


def test_unknown_start_counts_as_downtime_and_is_reported() -> None:
    report = compute_availability_from_states([(4000, True)], window_start_ms=0, window_end_ms=10000, initial_up=None)
    assert report.downtime_ms == 4000
    assert report.unknown_ms == 4000
    assert report.availability == pytest.approx(0.6)


def test_state_events_outside_window_and_repeats() -> None:
    report = compute_availability_from_states(
        [(-5, True), (3000, False), (4000, False), (10000, True)], window_start_ms=0, window_end_ms=10000, initial_up=True
    )
    assert [(p.start_ms, p.end_ms) for p in report.downtime_periods] == [(3000, 10000)]
    assert compute_availability_from_states([], window_start_ms=0, window_end_ms=10, initial_up=True).availability == 1.0


def test_state_bad_window_refused() -> None:
    with pytest.raises(AvailabilityError):
        compute_availability_from_states([], window_start_ms=5, window_end_ms=5)


def test_period_windows() -> None:
    start, end = period_window("daily", date(2026, 1, 2))
    assert end - start == 86_400_000
    start, end = period_window("weekly", date(2026, 1, 8))
    assert end - start == 7 * 86_400_000
    with pytest.raises(ScheduleError):
        period_window("monthly", date(2026, 1, 2))


def test_config_validation() -> None:
    assert parse_config({}).enabled is False
    with pytest.raises(ScheduleError):
        parse_config({"enabled": "yes"})
    with pytest.raises(ScheduleError):
        parse_config({"surprise": 1})
    with pytest.raises(ScheduleError):
        parse_config({"sources": [{"id": "../x", "availability": {"mode": "off"}}]})
    with pytest.raises(ScheduleError):
        parse_config({"sources": [{"id": "a", "availability": {"mode": "state"}}]})
    with pytest.raises(ScheduleError):
        parse_config({"planned_time_s_per_day": True})


def _points(day_start: int) -> list[dict]:
    out = []
    for i in range(10):
        ts = day_start + 1000 * i
        out.append({"sourceId": "robot-1", "kind": "production_event", "field": "good", "timestamp": ts, "value": 1.0})
        out.append({"sourceId": "robot-1", "kind": "production_event", "field": "cycleTimeS", "timestamp": ts, "value": 5.0})
    out.append({"sourceId": "robot-1", "kind": "node_state", "field": "up", "timestamp": day_start - 1000, "value": 1.0})
    out.append({"sourceId": "robot-1", "kind": "node_state", "field": "up", "timestamp": day_start + 43_200_000, "value": 0.0})
    return out


def _config(tmp_path: Path, enabled: bool) -> dict:
    return {
        "enabled": enabled,
        "output_dir": str(tmp_path / "out"),
        "planned_time_s_per_day": 100,
        "ideal_cycle_time_s": 4,
        "sources": [{"id": "robot-1", "availability": {"mode": "state", "kind": "node_state", "field": "up"}}],
    }


def test_disabled_writes_nothing(tmp_path: Path) -> None:
    config = parse_config(_config(tmp_path, False))
    code, manifest = run_schedule(config, DatalakeClient("http://127.0.0.1:1"), period="daily", until=date(2026, 1, 2))
    assert (code, manifest) == (0, None)
    assert not (tmp_path / "out").exists()


def test_daily_run_writes_files_and_manifest_that_verifies(tmp_path: Path) -> None:
    start, _ = period_window("daily", date(2026, 1, 2))
    with running_fake_datalake() as (url, server):
        server.points = _points(start)
        cfg = parse_config(_config(tmp_path, True))
        code, manifest = run_schedule(cfg, DatalakeClient(url), period="daily", until=date(2026, 1, 2))
    assert code == 0 and manifest is not None
    assert manifest["errors"] == []
    names = {f["name"] for f in manifest["files"]}
    assert names == {"robot-1-oee.csv", "robot-1-oee.html", "robot-1-availability.csv", "robot-1-availability.html"}
    folder = tmp_path / "out" / "daily" / "2026-01-02"
    assert verify_folder(str(folder)) == []
    avail = next(r for r in manifest["reports"] if r["report"] == "availability")
    assert avail["method"] == "state"
    csv_text = (folder / "robot-1-availability.csv").read_text(encoding="utf-8")
    assert "downtime_ms" in csv_text and "43200000" in csv_text
    # tampering is detected
    (folder / "robot-1-oee.csv").write_text("changed", encoding="utf-8")
    assert verify_folder(str(folder)) == ["robot-1-oee.csv: sha256 differs from the manifest"]


def test_one_failing_source_does_not_stop_the_others(tmp_path: Path) -> None:
    start, _ = period_window("daily", date(2026, 1, 2))
    raw = _config(tmp_path, True)
    raw["sources"].append({"id": "ghost", "availability": {"mode": "off"}})
    with running_fake_datalake() as (url, server):
        server.points = _points(start)
        code, manifest = run_schedule(parse_config(raw), DatalakeClient(url), period="daily", until=date(2026, 1, 2))
    assert code == 1 and manifest is not None
    assert [e["sourceId"] for e in manifest["errors"]] == ["ghost"]
    assert any(f["name"] == "robot-1-oee.csv" for f in manifest["files"])


def test_cli_disabled_and_missing_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    assert main(["--config", str(cfg), "--period", "daily"]) == 0
    assert "disabled" in capsys.readouterr().out
    assert main(["--config", str(tmp_path / "none.json"), "--period", "weekly"]) == 2


def test_api_state_mode_and_unknown_mode() -> None:
    with running_fake_datalake() as (url, server):
        server.points = [
            {"sourceId": "m", "kind": "node_state", "field": "up", "timestamp": 0, "value": 1.0},
            {"sourceId": "m", "kind": "node_state", "field": "up", "timestamp": 4000, "value": 0.0},
        ]
        api = ReportsServer(("127.0.0.1", 0), DatalakeClient(url))
        thread = threading.Thread(target=api.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{api.server_address[1]}"
        try:
            query = "sourceId=m&kind=node_state&field=up&start=1000&end=11000&mode=state"
            with urllib.request.urlopen(f"{base}/reports/availability?{query}") as r:
                body = json.loads(r.read())
            assert body["method"] == "state" and body["downtime_ms"] == 7000
            with pytest.raises(urllib.error.HTTPError) as err:
                urllib.request.urlopen(f"{base}/reports/availability?sourceId=m&kind=k&field=f&start=0&end=10&mode=magic")
            assert err.value.code == 400
        finally:
            api.shutdown()
            api.server_close()
            thread.join(timeout=2)

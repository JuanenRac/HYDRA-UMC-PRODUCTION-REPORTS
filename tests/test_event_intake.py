# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_event_intake.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""The intake of production events: off by default, an identity for every request, a size limit, a rate limit, the contract of the SDK, and the OEE of what was received.

The events are the ones the job dispatcher hands over (`ProductionEvent` of the shared contract), sent through a real socket to a real `ReportsServer`.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

pytest.importorskip("hydra_umc_sdk.production_event", reason="the SDK 0.4.2 or later is not installed")

from hydra_umc_production_reports import main as main_module  # noqa: E402
from hydra_umc_production_reports.api import ReportsServer  # noqa: E402
from hydra_umc_production_reports.datalake_client import DatalakeClient  # noqa: E402
from hydra_umc_production_reports.event_intake import (  # noqa: E402
    IntakeConfigError,
    ProductionEventStore,
    RateLimiter,
    build_config,
    config_from_file,
)
from hydra_umc_production_reports.server_identity import sign_token  # noqa: E402

KEY = b"0123456789abcdef0123456789abcdef"
TOKEN = "service-token-1234"


def event(second: int, good: bool = True, cycle: float = 5.0, **extra: object) -> dict[str, object]:
    """An event in the form the dispatcher writes: a UTC time with milliseconds, the robot as the source, the job it finished."""
    return {
        "schema_version": "1.0",
        "source_id": "robot-1",
        "at_utc": f"2026-01-01T00:00:{second:02d}.250Z",
        "good": good,
        "cycle_time_s": cycle,
        "job_id": f"J-{second}",
        **extra,
    }


@contextmanager
def running(*, events: ProductionEventStore | None, token: str | None = None, identity_key: bytes | None = None, max_bytes: int = 4096, rate: float = 600.0) -> Iterator[str]:
    server = ReportsServer(
        ("127.0.0.1", 0),
        DatalakeClient("http://127.0.0.1:1"),
        token=token,
        identity_key=identity_key,
        events=events,
        event_max_bytes=max_bytes,
        event_rate_per_minute=rate,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def post(base: str, payload: object, *, bearer: str | None = None, content_type: str | None = "application/json", raw: bytes | None = None, path: str = "/events/production") -> tuple[int, dict]:
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(base + path, data=body, method="POST")
    if content_type:
        request.add_header("Content-Type", content_type)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def get(base: str, path: str, *, bearer: str | None = None) -> tuple[int, dict]:
    request = urllib.request.Request(base + path)
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_route_does_not_exist_unless_the_operator_turned_it_on() -> None:
    with running(events=None, token=TOKEN) as base:
        assert post(base, event(1), bearer=TOKEN)[0] == 404
        assert get(base, "/reports/oee/events?sourceId=robot-1&day=2026-01-01&idealCycleTimeS=5", bearer=TOKEN)[0] == 404


def test_an_event_of_the_contract_is_accepted_and_counted_once() -> None:
    store = ProductionEventStore()
    with running(events=store, token=TOKEN) as base:
        assert post(base, event(1), bearer=TOKEN) == (202, {"accepted": True, "duplicate": False})
        assert post(base, event(1), bearer=TOKEN) == (202, {"accepted": True, "duplicate": True})  # a producer that retries
        assert get(base, "/stats", bearer=TOKEN)[1]["productionEvents"] == {"stored": 1, "sources": 1}
    assert store.count() == 1


def test_a_request_without_an_identity_is_refused() -> None:
    with running(events=ProductionEventStore(), token=TOKEN) as base:
        assert post(base, event(1))[0] == 401
        assert post(base, event(1), bearer="wrong")[0] == 401


def test_the_signed_token_of_the_server_is_the_identity() -> None:
    store = ProductionEventStore()
    with running(events=store, identity_key=KEY) as base:
        assert post(base, event(1))[0] == 401
        assert post(base, event(1), bearer=sign_token(KEY, "ana", "operator"))[0] == 202
        assert post(base, event(2), bearer=sign_token(KEY, "ana", "viewer"))[0] == 401  # a viewer reads, it does not write
        assert post(base, event(3), bearer=sign_token(b"another-key-another-key-another-key", "ana", "admin"))[0] == 401
        assert post(base, event(4), bearer=sign_token(KEY, "ana", "operator", ttl_seconds=-5))[0] == 401  # expired
    assert store.count() == 1


@pytest.mark.parametrize(
    "bad",
    [
        {**event(1), "extra": 1},
        {**event(1), "good": 1},
        {**event(1), "cycle_time_s": 0},
        {**event(1), "cycle_time_s": 604801},
        {**event(1), "schema_version": "2.0"},
        {**event(1), "at_utc": "yesterday"},
        {**event(1), "source_id": "has space"},
        {k: v for k, v in event(1).items() if k != "good"},
    ],
)
def test_a_message_the_contract_refuses_is_a_400_and_is_not_kept(bad: dict[str, object]) -> None:
    store = ProductionEventStore()
    with running(events=store, token=TOKEN) as base:
        status, body = post(base, bad, bearer=TOKEN)
    assert status == 400 and "contract" in body["error"]
    assert store.count() == 0


def test_a_body_that_is_not_one_json_object_is_refused() -> None:
    with running(events=ProductionEventStore(), token=TOKEN) as base:
        assert post(base, None, bearer=TOKEN, raw=b"{not json")[0] == 400
        assert post(base, None, bearer=TOKEN, raw=b'{"cycle_time_s": NaN}')[0] == 400
        assert post(base, [event(1)], bearer=TOKEN)[0] == 400
        assert post(base, event(1), bearer=TOKEN, content_type="text/plain")[0] == 415
        assert post(base, event(1), bearer=TOKEN, content_type=None)[0] == 415


def test_a_body_over_the_limit_is_a_413() -> None:
    store = ProductionEventStore()
    with running(events=store, token=TOKEN, max_bytes=300) as base:
        assert post(base, event(1), bearer=TOKEN)[0] == 202
        status, body = post(base, event(2, reject_reason="x" * 400), bearer=TOKEN)
        assert status == 413 and "300" in body["error"]
    assert store.count() == 1


def test_the_rate_limit_answers_429_with_retry_after() -> None:
    with running(events=ProductionEventStore(), token=TOKEN, rate=6.0) as base:  # a burst of 5, then 6 per minute
        statuses = [post(base, event(second), bearer=TOKEN)[0] for second in range(1, 9)]
        assert statuses[:5] == [202] * 5
        assert 429 in statuses[5:]
        request = urllib.request.Request(base + "/events/production", data=json.dumps(event(20)).encode(), method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {TOKEN}")
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=5)
        assert caught.value.code == 429 and int(caught.value.headers["Retry-After"]) >= 1


def test_the_rate_limiter_refills_with_time() -> None:
    now = [0.0]
    limiter = RateLimiter(60.0, clock=lambda: now[0])  # 1 per second, a burst of 6
    assert [limiter.take() for _ in range(6)] == [0.0] * 6
    assert limiter.take() > 0
    now[0] += 2.0
    assert limiter.take() == 0.0


def test_the_dispatcher_events_become_the_oee_of_the_robot() -> None:
    """End to end with the contract: three good cycles and one bad one of 5 s in a 60 s plan, ideal cycle 5 s -> availability 1/3, performance 1, quality 3/4."""
    with running(events=ProductionEventStore(), token=TOKEN) as base:
        for second, good in ((1, True), (10, True), (20, False), (30, True)):
            assert post(base, event(second, good=good), bearer=TOKEN)[0] == 202
        assert post(base, {**event(5), "source_id": "robot-2"}, bearer=TOKEN)[0] == 202
        start = 1767225600000
        query = f"/reports/oee/events?sourceId=robot-1&start={start}&end={start + 60000}&plannedTimeS=60&idealCycleTimeS=5"
        status, body = get(base, query, bearer=TOKEN)
        assert status == 200
        report = body["report"]
        assert body["events"] == 4 and report["total_count"] == 4 and report["good_count"] == 3
        assert report["quality"] == pytest.approx(0.75)
        assert report["availability"] == pytest.approx(20 / 60)
        assert report["performance"] == pytest.approx(1.0)
        assert get(base, f"/reports/oee/events?sourceId=robot-9&start={start}&end={start + 60000}&plannedTimeS=60&idealCycleTimeS=5", bearer=TOKEN)[0] == 400  # no cycles
        assert get(base, "/reports/oee/events?sourceId=robot-1", bearer=TOKEN)[0] == 400


def test_the_store_is_bounded_and_survives_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "events.jsonl")
    store = ProductionEventStore(capacity=3, path=path)
    for second in range(1, 6):
        assert store.add(event(second)) is True
    assert store.count() == 3  # the oldest two were dropped
    again = ProductionEventStore(capacity=3, path=path)
    assert again.count() == 3 and again.refused_on_load == 0
    assert [e.timestamp_ms for e in again.events("robot-1", 0, 2 ** 62)] == [1767225603250, 1767225604250, 1767225605250]


def test_a_damaged_line_of_the_file_is_skipped_and_counted(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps(event(1)) + "\nnot json\n" + json.dumps({**event(2), "extra": 1}) + "\n", encoding="utf-8")
    store = ProductionEventStore(path=str(path))
    assert store.count() == 1 and store.refused_on_load == 2


def test_the_configuration_is_off_by_default_and_the_command_line_wins(tmp_path: Path) -> None:
    off = build_config({}, enabled_flag=False, max_bytes=None, rate=None, capacity=None, store_file=None)
    assert off.enabled is False and off.max_bytes == 4096 and off.rate_per_minute == 600.0
    file = tmp_path / "events.json"
    file.write_text(json.dumps({"acceptProductionEvents": True, "maxBytes": 2048, "ratePerMinute": 30, "capacity": 10}), encoding="utf-8")
    on = build_config(config_from_file(str(file)), enabled_flag=False, max_bytes=1024, rate=None, capacity=None, store_file=None)
    assert on.enabled is True and on.max_bytes == 1024 and on.rate_per_minute == 30.0 and on.capacity == 10
    file.write_text(json.dumps({"acceptProductionEvent": True}), encoding="utf-8")
    with pytest.raises(IntakeConfigError):
        config_from_file(str(file))
    with pytest.raises(IntakeConfigError):
        build_config({}, enabled_flag=True, max_bytes=10, rate=None, capacity=None, store_file=None)


def test_the_service_refuses_to_accept_events_without_an_identity(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--accept-production-events"]) == 2
    assert "without an identity" in capsys.readouterr().err
    assert main_module.intake_problem(False, None, None) is None
    assert main_module.intake_problem(True, "t", None) is None
    assert main_module.intake_problem(True, None, KEY) is None

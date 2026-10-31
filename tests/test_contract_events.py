# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_contract_events.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
from __future__ import annotations

import pytest

pytest.importorskip("hydra_umc_sdk.production_event", reason="the SDK 0.4.2 or later is not installed")

from hydra_umc_production_reports.contract_events import (  # noqa: E402
    ContractEventError,
    event_from_contract,
    oee_from_contract_events,
    split_contract_events,
)
from hydra_umc_production_reports.oee import OEEError, compute_oee  # noqa: E402


def event(second: int, good: bool = True, cycle: float = 5.0, **extra: object) -> dict[str, object]:
    return {"schema_version": "1.0", "source_id": "robot-1", "at_utc": f"2026-01-01T00:00:{second:02d}Z", "good": good, "cycle_time_s": cycle, **extra}


def test_a_valid_event_becomes_an_oee_event_in_epoch_milliseconds() -> None:
    converted = event_from_contract(event(1, good=False, cycle=2.5, job_id="J-1", reject_reason="burr"))
    assert converted.timestamp_ms == 1767225601000
    assert converted.good is False
    assert converted.cycle_time_s == 2.5


def test_an_offset_other_than_utc_is_converted() -> None:
    payload = event(0)
    payload["at_utc"] = "2026-01-01T01:00:00+01:00"
    assert event_from_contract(payload).timestamp_ms == 1767225600000


@pytest.mark.parametrize(
    "bad",
    [
        {**event(0), "extra": 1},
        {**event(0), "good": 1},
        {**event(0), "cycle_time_s": 0},
        {**event(0), "cycle_time_s": 604801},
        {**event(0), "cycle_time_s": float("nan")},
        {**event(0), "schema_version": "2.0"},
        {**event(0), "at_utc": "yesterday"},
        {**event(0), "source_id": "has space"},
        {k: v for k, v in event(0).items() if k != "good"},
        "not an object",
    ],
)
def test_the_contract_refuses_what_it_does_not_define(bad: object) -> None:
    with pytest.raises(ContractEventError):
        event_from_contract(bad)  # type: ignore[arg-type]


def test_a_batch_reports_each_refused_payload_with_its_position() -> None:
    events, refused = split_contract_events([event(0), {**event(1), "good": "yes"}, event(2)])
    assert len(events) == 2
    assert [index for index, _ in refused] == [1]
    assert "good" in refused[0][1]


def test_the_oee_of_contract_events_equals_the_oee_of_the_same_events() -> None:
    payloads = [event(i, good=(i < 8), cycle=5.0) for i in range(10)]
    report = oee_from_contract_events(payloads, planned_time_s=100.0, ideal_cycle_time_s=4.0)
    direct = compute_oee([event_from_contract(p) for p in payloads], planned_time_s=100.0, ideal_cycle_time_s=4.0)
    assert report == direct
    assert report.oee == pytest.approx(0.32)


def test_one_refused_event_refuses_the_whole_report() -> None:
    payloads = [event(0), {**event(1), "cycle_time_s": -1}]
    with pytest.raises(OEEError, match="event 1"):
        oee_from_contract_events(payloads, planned_time_s=100.0, ideal_cycle_time_s=4.0)


def test_no_events_is_the_error_of_the_oee() -> None:
    with pytest.raises(OEEError):
        oee_from_contract_events([], planned_time_s=100.0, ideal_cycle_time_s=4.0)

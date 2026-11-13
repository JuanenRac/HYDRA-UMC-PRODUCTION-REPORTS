# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - tests/test_startup_intake_and_identity_edges.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Branches that need no SDK and no data lake: the start-up refusals, the intake configuration and store, the contract reader (with a stand-in contract) and
the identity token edge cases. Everything is deterministic: a fixed clock and fixed keys, no network."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from hydra_umc_production_reports import main as main_module
from hydra_umc_production_reports.contract_events import (
    ContractEventError,
    event_from_contract,
    oee_from_contract_events,
    split_contract_events,
)
from hydra_umc_production_reports.event_intake import (
    DEFAULT_CAPACITY,
    DEFAULT_MAX_BYTES,
    DEFAULT_RATE_PER_MINUTE,
    IntakeConfig,
    IntakeConfigError,
    ProductionEventStore,
    RateLimiter,
    build_config,
    config_from_file,
)
from hydra_umc_production_reports.oee import OEEError
from hydra_umc_production_reports.server_identity import (
    IdentityError,
    identity_key_from,
    load_identity_key,
    sign_token,
    verify_token,
)
from hydra_umc_production_reports.server_identity import main as identity_main

KEY = b"0123456789abcdef0123456789abcdef"
NOW = 1_800_000_000.0


# ---------------------------------------------------------------- start-up

@pytest.mark.parametrize("addr", ["127.0.0.1", "localhost", "LOCALHOST", "::1", "[::1]"])
def test_loopback_addresses_are_recognised(addr: str) -> None:
    assert main_module.is_loopback_addr(addr)


@pytest.mark.parametrize("addr", ["0.0.0.0", "192.168.0.10", "example.org", "", "not an address"])
def test_other_addresses_are_not_loopback(addr: str) -> None:
    assert not main_module.is_loopback_addr(addr)


def test_network_address_without_token_is_refused_by_default() -> None:
    problem = main_module.startup_problem("0.0.0.0", "", False)
    assert problem is not None and "--token" in problem and "--allow-anonymous" in problem


def test_network_address_is_allowed_with_a_token_or_on_purpose() -> None:
    assert main_module.startup_problem("0.0.0.0", "secret", False) is None
    assert main_module.startup_problem("0.0.0.0", None, True) is None
    assert main_module.startup_problem("127.0.0.1", None, False) is None


def test_intake_needs_an_identity() -> None:
    assert main_module.intake_problem(True, "", None) is not None
    assert main_module.intake_problem(True, "t", None) is None
    assert main_module.intake_problem(True, "", KEY) is None
    assert main_module.intake_problem(False, "", None) is None


def test_main_refuses_an_open_network_api(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--addr", "0.0.0.0", "--token", ""]) == 2
    assert "refusing to listen" in capsys.readouterr().err


def test_main_refuses_events_without_identity(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--accept-production-events", "--token", ""]) == 2
    assert "production events" in capsys.readouterr().err


def test_main_reports_a_bad_events_config_without_leaking(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "events.json"
    bad.write_text('{"unknownKey": 1}', encoding="utf-8")
    assert main_module.main(["--events-config", str(bad)]) == 2
    assert "unknown keys" in capsys.readouterr().err


def test_main_reports_an_unreadable_identity_key(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--identity-key-file", str(tmp_path / "missing.key")]) == 2
    assert "cannot read the identity key file" in capsys.readouterr().err


def test_main_reports_an_unreadable_dispatcher_token_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main_module.main(["--job-dispatcher-token-file", str(tmp_path / "missing.txt")]) == 2
    assert "--job-dispatcher-token-file" in capsys.readouterr().err


class _FakeServer:
    """Stands in for ReportsServer: records the options and returns from serve_forever as a Ctrl+C would."""

    instances: list["_FakeServer"] = []

    def __init__(self, address: tuple[str, int], client: Any, **options: Any) -> None:
        self.address = address
        self.client = client
        self.options = options
        self.closed = False
        self.health_open = False
        _FakeServer.instances.append(self)

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def test_main_starts_and_shuts_down_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _FakeServer.instances.clear()
    monkeypatch.setattr(main_module, "ReportsServer", _FakeServer)
    token_file = tmp_path / "dispatcher.token"
    token_file.write_text("dispatcher-secret\nignored second line\n", encoding="utf-8")
    code = main_module.main(
        ["--port", "0", "--token", "t", "--max-concurrent-reports", "0", "--no-cache", "--job-dispatcher-url", "http://127.0.0.1:1", "--job-dispatcher-token-file", str(token_file)]
    )
    out = capsys.readouterr().out
    server = _FakeServer.instances[0]
    assert code == 0 and server.closed
    assert server.options["max_concurrent_reports"] == 1  # never below one
    assert server.options["job_dispatcher_token"] == "dispatcher-secret"  # only the first line
    assert "dispatcher-secret" not in out  # the token is never printed
    assert "every route requires a bearer token" in out and "shutting down" in out


def test_main_prints_the_identity_and_events_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _FakeServer.instances.clear()
    monkeypatch.setattr(main_module, "ReportsServer", _FakeServer)
    stand_in = types.ModuleType("hydra_umc_sdk")
    stand_in.production_event = types.SimpleNamespace(validate_production_event=lambda payload: None)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hydra_umc_sdk", stand_in)
    key_file = tmp_path / "identity.key"
    key_file.write_text(KEY.decode("ascii"), encoding="utf-8")
    code = main_module.main(["--identity-key-file", str(key_file), "--accept-production-events", "--events-file", str(tmp_path / "events.jsonl"), "--health-open"])
    out = capsys.readouterr().out
    assert code == 0
    assert "tokens of HYDRA-UMC-SERVER accepted" in out and "POST /events/production on (0 events kept" in out
    assert _FakeServer.instances[0].options["identity_key"] == KEY


def test_main_refuses_events_when_the_store_cannot_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setitem(sys.modules, "hydra_umc_sdk", None)  # the SDK cannot be imported: the contract reader refuses
    code = main_module.main(["--accept-production-events", "--token", "t"])
    assert code == 2
    assert "the production events" in capsys.readouterr().err


# ---------------------------------------------------------- intake config

def test_defaults_leave_the_intake_off() -> None:
    config = build_config({}, enabled_flag=False, max_bytes=None, rate=None, capacity=None, store_file=None)
    assert config == IntakeConfig(False, DEFAULT_MAX_BYTES, DEFAULT_RATE_PER_MINUTE, DEFAULT_CAPACITY, "")


def test_file_can_switch_it_on_and_the_command_line_wins() -> None:
    values = {"acceptProductionEvents": True, "maxBytes": 128, "ratePerMinute": 30, "capacity": 10, "storeFile": "a.jsonl"}
    config = build_config(values, enabled_flag=False, max_bytes=256, rate=None, capacity=None, store_file="b.jsonl")
    assert config.enabled and config.max_bytes == 256 and config.rate_per_minute == 30.0 and config.capacity == 10 and config.store_file == "b.jsonl"


def test_a_truthy_string_does_not_switch_the_intake_on() -> None:
    config = build_config({"acceptProductionEvents": "yes"}, enabled_flag=False, max_bytes=None, rate=None, capacity=None, store_file=None)
    assert config.enabled is False


@pytest.mark.parametrize(
    ("kwargs", "word"),
    [
        ({"max_bytes": 63}, "maxBytes"),
        ({"max_bytes": 1_048_577}, "maxBytes"),
        ({"rate": 0}, "ratePerMinute"),
        ({"rate": -1.0}, "ratePerMinute"),
        ({"rate": 600_001}, "ratePerMinute"),
        ({"capacity": 0}, "capacity"),
        ({"capacity": 5_000_001}, "capacity"),
    ],
)
def test_out_of_range_values_name_the_key(kwargs: dict[str, Any], word: str) -> None:
    base: dict[str, Any] = {"max_bytes": None, "rate": None, "capacity": None}
    base.update(kwargs)
    with pytest.raises(IntakeConfigError, match=word):
        build_config({}, enabled_flag=True, store_file=None, **base)


def test_a_non_numeric_value_is_refused() -> None:
    with pytest.raises(IntakeConfigError, match="not a number"):
        build_config({"maxBytes": "lots"}, enabled_flag=True, max_bytes=None, rate=None, capacity=None, store_file=None)
    with pytest.raises(IntakeConfigError, match="not a number"):
        build_config({"capacity": None}, enabled_flag=True, max_bytes=None, rate=None, capacity=None, store_file=None)


def test_config_file_errors_do_not_leak_paths_or_content(tmp_path: Path) -> None:
    with pytest.raises(IntakeConfigError, match="cannot read"):
        config_from_file(str(tmp_path / "missing.json"))
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(IntakeConfigError, match="not valid JSON"):
        config_from_file(str(broken))
    listing = tmp_path / "list.json"
    listing.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(IntakeConfigError, match="JSON object"):
        config_from_file(str(listing))
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"capacity": 5}), encoding="utf-8")
    assert config_from_file(str(good)) == {"capacity": 5}


# ------------------------------------------------------------ rate limiter

class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_allows_a_burst_then_says_how_long_to_wait() -> None:
    clock = _Clock()
    limiter = RateLimiter(60.0, clock=clock)  # one a second, burst of 6 (a tenth of a minute, at least 5)
    allowed = sum(1 for _ in range(20) if limiter.take() == 0.0)
    assert allowed == 6
    wait = limiter.take()
    assert 0.0 < wait <= 1.0


def test_rate_limiter_refills_with_time_and_never_beyond_the_burst() -> None:
    clock = _Clock()
    limiter = RateLimiter(60.0, clock=clock)
    for _ in range(6):
        assert limiter.take() == 0.0
    assert limiter.take() > 0.0
    clock.now += 2.0
    assert limiter.take() == 0.0 and limiter.take() == 0.0 and limiter.take() > 0.0
    clock.now += 10_000.0  # a long silence only refills the burst
    assert sum(1 for _ in range(20) if limiter.take() == 0.0) == 6


def test_small_rates_still_get_a_burst_of_five() -> None:
    limiter = RateLimiter(1.0, clock=_Clock())
    assert sum(1 for _ in range(10) if limiter.take() == 0.0) == 5


# ------------------------------------------- the contract reader (stand-in)

def _install_contract(monkeypatch: pytest.MonkeyPatch, *, refuse_when: str = "bad") -> None:
    def validate(payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ValueError("a production event must be an object")
        if payload.get("source_id") == refuse_when:
            raise ValueError("source_id refused by the stand-in contract")

    stand_in = types.ModuleType("hydra_umc_sdk")
    stand_in.production_event = types.SimpleNamespace(validate_production_event=validate)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hydra_umc_sdk", stand_in)


def _payload(second: int, source: str = "robot-1", good: bool = True, cycle: float = 5.0, job: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "source_id": source,
        "at_utc": f"2026-01-01T00:00:{second:02d}.250Z",
        "good": good,
        "cycle_time_s": cycle,
    }
    if job is not None:
        payload["job_id"] = job
    return payload


def test_missing_sdk_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "hydra_umc_sdk", None)
    with pytest.raises(ContractEventError, match="needs the SDK"):
        event_from_contract(_payload(1))


def test_event_from_contract_converts_the_time_to_epoch_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    event = event_from_contract(_payload(1, cycle=7))
    assert event.timestamp_ms == 1_767_225_601_250  # 2026-01-01T00:00:01.250Z
    assert event.good is True and event.cycle_time_s == 7.0


def test_a_lower_case_z_and_a_numeric_offset_give_the_same_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    z = _payload(1)
    lower = dict(z, at_utc="2026-01-01t00:00:01.250z")
    offset = dict(z, at_utc="2026-01-01T01:00:01.250+01:00")
    assert event_from_contract(lower).timestamp_ms == event_from_contract(z).timestamp_ms == event_from_contract(offset).timestamp_ms


def test_split_keeps_the_good_ones_and_names_the_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    events, refused = split_contract_events([_payload(1), _payload(2, source="bad"), _payload(3)])
    assert len(events) == 2
    assert refused == [(1, "source_id refused by the stand-in contract")]


def test_one_refused_event_refuses_the_whole_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    with pytest.raises(OEEError, match=r"event 1 is refused .*1 refused in all"):
        oee_from_contract_events([_payload(1), _payload(2, source="bad")], planned_time_s=60.0, ideal_cycle_time_s=5.0)
    report = oee_from_contract_events([_payload(1), _payload(2, good=False)], planned_time_s=60.0, ideal_cycle_time_s=5.0)
    assert report is not None


# --------------------------------------------------------- the event store

def test_store_counts_a_retried_message_once_and_drops_the_oldest(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    store = ProductionEventStore(capacity=3)
    assert store.add(_payload(1)) is True
    assert store.add(_payload(1)) is False  # the same message again
    for second in (2, 3, 4):
        assert store.add(_payload(second)) is True
    assert store.count() == 3
    assert store.add(_payload(1)) is True  # the oldest was dropped, so it is new again
    assert store.sources() == ["robot-1"]


def test_store_filters_by_source_and_half_open_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    store = ProductionEventStore()
    store.add(_payload(1))
    store.add(_payload(2))
    store.add(_payload(3, source="robot-2"))
    start = event_from_contract(_payload(1)).timestamp_ms
    end = event_from_contract(_payload(2)).timestamp_ms
    assert len(store.events("robot-1", start, end)) == 1  # the end is exclusive
    assert len(store.events("robot-1", start, end + 1)) == 2
    assert store.events("robot-9", 0, 2**62) == []
    assert store.sources() == ["robot-1", "robot-2"]


def test_store_persists_and_survives_a_restart_with_damaged_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_contract(monkeypatch)
    path = tmp_path / "events.jsonl"
    first = ProductionEventStore(path=str(path))
    first.add(_payload(1))
    first.add(_payload(2))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n")  # a blank line is skipped, not counted as damage
        handle.write("{broken json\n")
        handle.write(json.dumps(_payload(3, source="bad")) + "\n")  # refused by the contract
        handle.write(json.dumps({"source_id": "robot-1"}) + "\n")  # a missing key
        handle.write("[1, 2]\n")  # not an object
    second = ProductionEventStore(path=str(path))
    assert second.count() == 2
    assert second.refused_on_load == 4


# -------------------------------------------------------- identity tokens

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _token(claims: dict[str, Any], *, header: dict[str, Any] | None = None, key: bytes = KEY) -> str:
    head = _b64(json.dumps(header or {"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    body = _b64(json.dumps(claims).encode("utf-8"))
    signature = hmac.new(key, f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(signature)}"


def _claims(**changes: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {"username": "ana", "role": "operator", "exp": NOW + 60}
    claims.update(changes)
    return {name: value for name, value in claims.items() if value is not ...}


def test_a_valid_token_gives_the_identity() -> None:
    identity = verify_token(_token(_claims()), KEY, now=NOW)
    assert (identity.username, identity.role) == ("ana", "operator")


@pytest.mark.parametrize(
    ("token", "word"),
    [
        (None, "required"),
        ("", "required"),
        ("a." * 3000, "malformed"),
        ("only.two", "malformed"),
        ("a..c", "malformed"),
        ("a.b.c.d", "malformed"),
        ("!!!.e30.e30", "malformed"),
    ],
)
def test_shapes_that_are_not_a_token(token: str | None, word: str) -> None:
    with pytest.raises(IdentityError, match=word):
        verify_token(token, KEY, now=NOW)


def test_a_header_that_is_not_an_object_is_malformed() -> None:
    head = _b64(b"[1]")
    with pytest.raises(IdentityError, match="malformed"):
        verify_token(f"{head}.e30.e30", KEY, now=NOW)
    not_utf8 = _b64(b"\xff\xfe")
    with pytest.raises(IdentityError, match="malformed"):
        verify_token(f"{not_utf8}.e30.e30", KEY, now=NOW)


def test_only_hs256_is_accepted_and_none_is_refused() -> None:
    for alg in ("none", "HS512", "RS256", None):
        with pytest.raises(IdentityError, match="algorithm"):
            verify_token(_token(_claims(), header={"alg": alg}), KEY, now=NOW)


def test_a_wrong_key_or_a_tampered_body_fails_the_signature() -> None:
    with pytest.raises(IdentityError, match="signature"):
        verify_token(_token(_claims(), key=b"x" * 32), KEY, now=NOW)
    head, _, signature = _token(_claims()).split(".")
    forged = f"{head}.{_b64(json.dumps(_claims(role='admin')).encode('utf-8'))}.{signature}"
    with pytest.raises(IdentityError, match="signature"):
        verify_token(forged, KEY, now=NOW)


@pytest.mark.parametrize("exp", [..., "tomorrow", True, None, float("nan"), float("inf")])
def test_an_expiry_that_is_missing_or_not_a_finite_number_is_refused(exp: Any) -> None:
    with pytest.raises(IdentityError, match="no expiry"):
        verify_token(_token(_claims(exp=exp)), KEY, now=NOW)


def test_an_expired_token_is_refused_at_the_exact_second() -> None:
    with pytest.raises(IdentityError, match="expired"):
        verify_token(_token(_claims(exp=NOW)), KEY, now=NOW)
    assert verify_token(_token(_claims(exp=NOW + 0.5)), KEY, now=NOW).username == "ana"


@pytest.mark.parametrize("nbf", [NOW + 1, "soon", True, float("nan")])
def test_not_before_in_the_future_or_malformed_is_refused(nbf: Any) -> None:
    with pytest.raises(IdentityError, match="not valid yet"):
        verify_token(_token(_claims(nbf=nbf)), KEY, now=NOW)


def test_issued_at_tolerates_a_small_clock_skew_only() -> None:
    assert verify_token(_token(_claims(iat=NOW + 5)), KEY, now=NOW).username == "ana"
    with pytest.raises(IdentityError, match="future"):
        verify_token(_token(_claims(iat=NOW + 6)), KEY, now=NOW)
    with pytest.raises(IdentityError, match="future"):
        verify_token(_token(_claims(iat=False)), KEY, now=NOW)


@pytest.mark.parametrize("changes", [{"username": ""}, {"username": 7}, {"username": ...}])
def test_a_token_without_a_user_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(IdentityError, match="names no user"):
        verify_token(_token(_claims(**changes)), KEY, now=NOW)


@pytest.mark.parametrize("role", ["root", "", None, 3, ...])
def test_a_token_with_an_unknown_role_is_refused(role: Any) -> None:
    with pytest.raises(IdentityError, match="no known role"):
        verify_token(_token(_claims(role=role)), KEY, now=NOW)


def test_sign_token_round_trips_and_is_read_only_by_default() -> None:
    identity = verify_token(sign_token(KEY, now=NOW), KEY, now=NOW + 30)
    assert identity.role == "viewer"
    with pytest.raises(IdentityError, match="expired"):
        verify_token(sign_token(KEY, ttl_seconds=10, now=NOW), KEY, now=NOW + 11)


def test_identity_key_files(tmp_path: Path) -> None:
    short = tmp_path / "short.key"
    short.write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="at least 32"):
        load_identity_key(str(short))
    good = tmp_path / "good.key"
    good.write_text(KEY.decode("ascii") + "\n", encoding="utf-8")
    assert load_identity_key(str(good)) == KEY
    assert identity_key_from(None, {}) is None
    assert identity_key_from(None, {"HYDRA_UMC_PRODUCTION_REPORTS_IDENTITY_KEY_FILE": str(good)}) == KEY
    assert identity_key_from("  ", {}) is None
    assert identity_key_from(str(good), {"HYDRA_UMC_PRODUCTION_REPORTS_IDENTITY_KEY_FILE": str(short)}) == KEY  # the flag wins over the variable


def test_probe_command_reports_a_bad_key_file_with_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert identity_main(["probe", "--port", "1", "--key-file", str(tmp_path / "missing.key")]) == 2
    assert "error:" in capsys.readouterr().out


def test_probe_command_fails_when_nothing_listens() -> None:
    assert identity_main(["probe", "--port", "1"]) == 1

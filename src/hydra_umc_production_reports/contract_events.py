# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - Production events of the shared contract
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Reads finished cycles published as `ProductionEvent` of HYDRA-UMC-SDK and turns them into the events the OEE needs.

A producer (the job dispatcher, the orchestrator or a bridge) publishes one `ProductionEvent` per finished cycle. Before a cycle counts for a report it is
validated with the contract of the SDK (closed schema, a real date-time, `good` a boolean, a cycle time above 0 and at most one week), so a malformed or
extended message is refused instead of being counted. The SDK is imported when it is needed: install it with `pip install "hydra-umc-production-reports[contract]"`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable

from .oee import OEEError, OEEReport, ProductionEvent, compute_oee


class ContractEventError(ValueError):
    """A production event that the contract of the SDK refuses, or the SDK is not installed."""


def _sdk() -> Any:
    try:
        from hydra_umc_sdk import production_event
    except ImportError as error:  # pragma: no cover - exercised by hiding the module in the tests
        raise ContractEventError('the ProductionEvent contract needs the SDK: pip install "hydra-umc-production-reports[contract]"') from error
    return production_event


def event_from_contract(payload: dict[str, Any]) -> ProductionEvent:
    """Validates one `ProductionEvent` payload with the SDK and returns the event of the OEE (`at_utc` in epoch milliseconds)."""
    module = _sdk()
    try:
        module.validate_production_event(payload)
    except ValueError as error:
        raise ContractEventError(str(error)) from error
    moment = datetime.fromisoformat(payload["at_utc"].upper().replace("Z", "+00:00"))
    return ProductionEvent(timestamp_ms=round(moment.timestamp() * 1000), good=payload["good"], cycle_time_s=float(payload["cycle_time_s"]))


def split_contract_events(payloads: Iterable[Any]) -> tuple[list[ProductionEvent], list[tuple[int, str]]]:
    """The valid events of a batch and, for each refused payload, its position and the reason."""
    events: list[ProductionEvent] = []
    refused: list[tuple[int, str]] = []
    for index, payload in enumerate(payloads):
        try:
            events.append(event_from_contract(payload))
        except ContractEventError as error:
            refused.append((index, str(error)))
    return events, refused


def oee_from_contract_events(payloads: Iterable[Any], *, planned_time_s: float, ideal_cycle_time_s: float) -> OEEReport:
    """The OEE of a batch of `ProductionEvent` payloads; one refused payload refuses the whole report (nothing is silently dropped)."""
    events, refused = split_contract_events(payloads)
    if refused:
        index, reason = refused[0]
        raise OEEError(f"production event {index} is refused by the contract ({len(refused)} refused in all): {reason}")
    return compute_oee(events, planned_time_s=planned_time_s, ideal_cycle_time_s=ideal_cycle_time_s)

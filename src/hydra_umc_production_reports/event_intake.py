# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/event_intake.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Receives the `ProductionEvent` messages of the producers (the job dispatcher first) and keeps the last ones for the OEE.

`POST /events/production` is off by default. The operator turns it on with `--accept-production-events` (or a configuration file) and then every request needs an
identity: the bearer token of the service or a signed token of HYDRA-UMC-SERVER. A request is refused when it is bigger than the limit, when it comes faster than
the rate allows, and when the contract of the SDK refuses the message (closed schema, a real date-time, `good` a boolean, a cycle time above 0 and at most a week).
The events live in a bounded queue (the oldest is dropped first), and optionally in a file of JSON lines that is read again at start, so a restart does not lose them.
A message that arrives twice (a producer that retries) is counted once.
"""

from __future__ import annotations

import collections
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .contract_events import ContractEventError, event_from_contract
from .oee import ProductionEvent

DEFAULT_MAX_BYTES = 4096
DEFAULT_RATE_PER_MINUTE = 600.0
DEFAULT_CAPACITY = 100_000
MAX_CAPACITY = 5_000_000
CONFIG_KEYS = {"acceptProductionEvents", "maxBytes", "ratePerMinute", "capacity", "storeFile"}


class IntakeConfigError(ValueError):
    """The configuration of the intake is wrong; the message names the key and never a secret."""


@dataclass(frozen=True)
class IntakeConfig:
    """What the operator decides about the intake. `enabled` is False unless it was asked for."""

    enabled: bool = False
    max_bytes: int = DEFAULT_MAX_BYTES
    rate_per_minute: float = DEFAULT_RATE_PER_MINUTE
    capacity: int = DEFAULT_CAPACITY
    store_file: str = ""

    def validate(self) -> "IntakeConfig":
        if not 64 <= self.max_bytes <= 1_048_576:
            raise IntakeConfigError("maxBytes must be between 64 and 1048576")
        if not 0 < self.rate_per_minute <= 600_000:
            raise IntakeConfigError("ratePerMinute must be above 0 and at most 600000")
        if not 1 <= self.capacity <= MAX_CAPACITY:
            raise IntakeConfigError(f"capacity must be between 1 and {MAX_CAPACITY}")
        return self


def config_from_file(path: str) -> dict[str, Any]:
    """The values of a JSON configuration file (unknown keys are refused so a typo is not silently ignored)."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except OSError as error:
        raise IntakeConfigError(f"cannot read the events configuration file: {error.strerror or 'unreadable'}") from error
    except ValueError as error:
        raise IntakeConfigError("the events configuration file is not valid JSON") from error
    if not isinstance(data, dict):
        raise IntakeConfigError("the events configuration file must hold a JSON object")
    unknown = sorted(set(data) - CONFIG_KEYS)
    if unknown:
        raise IntakeConfigError(f"unknown keys in the events configuration file: {unknown}")
    return data


def build_config(file_values: dict[str, Any], *, enabled_flag: bool, max_bytes: int | None, rate: float | None, capacity: int | None, store_file: str | None) -> IntakeConfig:
    """File first, then the command line over it; a file can switch the intake on, the absence of everything leaves it off."""
    enabled = bool(enabled_flag) or file_values.get("acceptProductionEvents") is True
    try:
        return IntakeConfig(
            enabled=enabled,
            max_bytes=int(max_bytes if max_bytes is not None else file_values.get("maxBytes", DEFAULT_MAX_BYTES)),
            rate_per_minute=float(rate if rate is not None else file_values.get("ratePerMinute", DEFAULT_RATE_PER_MINUTE)),
            capacity=int(capacity if capacity is not None else file_values.get("capacity", DEFAULT_CAPACITY)),
            store_file=str(store_file if store_file is not None else file_values.get("storeFile", "")),
        ).validate()
    except (TypeError, ValueError) as error:
        if isinstance(error, IntakeConfigError):
            raise
        raise IntakeConfigError("a value of the events configuration is not a number") from error


class RateLimiter:
    """A token bucket: `rate_per_minute` events a minute on average, a burst of at most one tenth of a minute's worth (never fewer than 5)."""

    def __init__(self, rate_per_minute: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._rate = rate_per_minute / 60.0
        self._burst = max(5.0, rate_per_minute / 10.0)
        self._tokens = self._burst
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()

    def take(self) -> float:
        """0.0 when the request may go on; otherwise the seconds to wait before the next one would be accepted."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0
            return (1.0 - self._tokens) / self._rate


def _key(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    return (payload["source_id"], payload.get("job_id", ""), payload["at_utc"], repr(payload["cycle_time_s"]))


class ProductionEventStore:
    """The last `capacity` accepted events, with a file of JSON lines behind them when a path is given."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY, path: str = "") -> None:
        self._capacity = capacity
        self._path = path
        self._lock = threading.Lock()
        self._items: collections.deque[tuple[tuple[str, str, str, str], str, ProductionEvent]] = collections.deque()
        self._keys: set[tuple[str, str, str, str]] = set()
        self.refused_on_load = 0
        if path and os.path.exists(path):
            self._load(path)

    def _load(self, path: str) -> None:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                    self._insert(payload, event_from_contract(payload))
                except (ValueError, ContractEventError, KeyError, TypeError):
                    self.refused_on_load += 1

    def _insert(self, payload: dict[str, Any], event: ProductionEvent) -> bool:
        key = _key(payload)
        if key in self._keys:
            return False
        self._items.append((key, payload["source_id"], event))
        self._keys.add(key)
        while len(self._items) > self._capacity:
            old = self._items.popleft()
            self._keys.discard(old[0])
        return True

    def add(self, payload: dict[str, Any]) -> bool:
        """Validates with the contract and keeps the event; False when it was already there. Raises ContractEventError when the contract refuses it."""
        event = event_from_contract(payload)
        with self._lock:
            fresh = self._insert(payload, event)
            if fresh and self._path:
                with open(self._path, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
        return fresh

    def events(self, source_id: str, start_ms: int, end_ms: int) -> list[ProductionEvent]:
        with self._lock:
            return [event for _, source, event in self._items if source == source_id and start_ms <= event.timestamp_ms < end_ms]

    def count(self) -> int:
        with self._lock:
            return len(self._items)

    def sources(self) -> list[str]:
        with self._lock:
            return sorted({source for _, source, _ in self._items})

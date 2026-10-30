# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/datalake_client.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""A real HTTP client for HYDRA-UMC-DATALAKE's own API
(src/hydra_umc_datalake/api.py) - this is the actual "built from
HYDRA-UMC-DATALAKE history" mechanism the README promises, not a stub.
Deliberately just a thin wrapper over DATALAKE's real GET /query and GET
/aggregate (stdlib urllib, no new dependency) - PRODUCTION-REPORTS does
not import DATALAKE's own Python package directly (they are separate
repos/services on purpose, see both projects' own "why a sibling, not a
submodule" architecture notes) - HTTP is the real, decoupled integration
seam between them.
"""
from __future__ import annotations

import http.client
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


CACHE_TTL_VARIABLE = "HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS"
DEFAULT_CACHE_TTL_SECONDS = 30.0
MAX_CACHE_ENTRIES = 256


def cache_ttl_from_environment(environ: dict[str, str] | None = None) -> float:
    """Seconds an aggregate is reused without asking the data lake anything: `HYDRA_UMC_PRODUCTION_REPORTS_CACHE_TTL_SECONDS`,
    default 30. `0` asks the data lake only for the fingerprint of the series each time; a negative or unreadable value is the default."""
    env = os.environ if environ is None else environ
    try:
        value = float(env.get(CACHE_TTL_VARIABLE, "").strip())
    except ValueError:
        return DEFAULT_CACHE_TTL_SECONDS
    return value if value >= 0 and value == value and value != float("inf") else DEFAULT_CACHE_TTL_SECONDS


READ_TOKEN_VARIABLES = ("HYDRA_UMC_DATALAKE_READ_TOKEN", "HYDRA_UMC_DATALAKE_TOKEN")


def token_from_environment(environ: dict[str, str] | None = None) -> str:
    """The bearer token for reading the data lake: HYDRA_UMC_DATALAKE_READ_TOKEN, else HYDRA_UMC_DATALAKE_TOKEN (the one for every
    role). Each one may instead be given as a file, `<name>_FILE`, whose first line is the token. Empty when none is set (a data
    lake on loopback with no token asks for none)."""
    env = os.environ if environ is None else environ
    for name in READ_TOKEN_VARIABLES:
        direct = env.get(name, "").strip()
        if direct:
            return direct
        path = env.get(f"{name}_FILE", "").strip()
        if path:
            try:
                with open(path, encoding="utf-8") as handle:
                    first = handle.readline().strip()
            except OSError:
                first = ""
            if first:
                return first
    return ""


def _usable_token(token: str) -> str:
    """A token with a control character could split a header: it is not sent."""
    return token if token and token.isprintable() and "\n" not in token and "\r" not in token else ""


class DatalakeError(RuntimeError):
    """Raised when a real request to DATALAKE fails or DATALAKE itself
    reports an error - never swallowed silently, since a report built on
    a request that silently returned nothing would look like "no data"
    instead of "couldn't ask".

    `status` is the HTTP status DATALAKE answered with, when it answered."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Point:
    """Mirrors HYDRA-UMC-DATALAKE's own query response shape exactly
    (src/hydra_umc_datalake/store.py's Point, serialized by its api.py)."""

    source_id: str
    kind: str
    field: str
    timestamp: int
    value: float


@dataclass(frozen=True)
class Bucket:
    """One bucket of DATALAKE's GET /aggregate: where it starts, its reduced value and how many raw points fell into it.
    A bucket without points is not returned by the data lake."""

    bucket_start: int
    value: float
    count: int


class DatalakeClient:
    """A real client against one running HYDRA-UMC-DATALAKE instance."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = 10.0,
        *,
        max_attempts: int = 3,
        retry_delay_seconds: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        token: str | None = None,
        cache_ttl_seconds: float | None = None,
        cache_entries: int = MAX_CACHE_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # aggregates already asked for: key = source, kind, field, reducer, bucket, window and the fingerprint of the series
        self.cache_ttl_seconds = cache_ttl_from_environment() if cache_ttl_seconds is None else max(0.0, float(cache_ttl_seconds))
        self._cache_entries = max(0, int(cache_entries))  # 0 turns the cache off
        self._cache: "OrderedDict[tuple, tuple[float, str, list[Bucket]]]" = OrderedDict()
        self._clock = clock
        self.cache_hits = 0
        self.cache_misses = 0
        # The bearer token the data lake asks for (read role). `None` reads it from the environment, "" sends none.
        self._token = _usable_token(token_from_environment() if token is None else token)
        self.timeout_s = timeout_s
        # Found while auditing the code: this
        # client had a timeout but no retry on transient network failure -
        # one network hiccup (a dropped connection, a momentary DNS blip,
        # DATALAKE mid-restart) used to fail an entire report (daily/
        # weekly/monthly) instead of retrying before surfacing the
        # DatalakeError that already exists. Only urllib.error.URLError
        # (unreachable host, timeout, connection refused/reset) is
        # retried - an HTTPError is a real response DATALAKE itself sent
        # and is never transient in the same sense, so it still raises
        # immediately, same as before this fix.
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._sleep = sleep

    def _get(self, path: str, params: dict[str, str | int]) -> object:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token}"} if self._token else {})
                with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                raise DatalakeError(f"DATALAKE returned HTTP {e.code} for {path}: {body}", status=e.code) from e
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                # urlopen only wraps a failure to send the request in a
                # URLError: a timeout or a reset while the answer is being
                # read arrives as a bare TimeoutError / ConnectionResetError /
                # RemoteDisconnected, and is just as transient.
                last_error = e
                if attempt < self.max_attempts:
                    self._sleep(self.retry_delay_seconds)
                continue
            try:
                return json.loads(raw)
            except ValueError as e:  # JSONDecodeError, or bytes that are not UTF-8
                raise DatalakeError(f"DATALAKE answered {path} with a body that is not JSON: {e}") from e
        reason = getattr(last_error, "reason", last_error)
        raise DatalakeError(
            f"could not reach DATALAKE at {self.base_url} after {self.max_attempts} attempts: {reason}"
        ) from last_error

    def query(
        self,
        *,
        source_id: str | None = None,
        kind: str | None = None,
        field: str | None = None,
        start: int | None = None,
        end: int | None = None,
        limit: int = 10000,
    ) -> list[Point]:
        """Real range query against DATALAKE's own GET /query."""
        params: dict[str, str | int] = {"limit": limit}
        if source_id is not None:
            params["sourceId"] = source_id
        if kind is not None:
            params["kind"] = kind
        if field is not None:
            params["field"] = field
        if start is not None:
            params["start"] = start
        if end is not None:
            params["end"] = end

        raw = self._get("/query", params)
        try:
            return [
                Point(
                    source_id=p["sourceId"],
                    kind=p["kind"],
                    field=p["field"],
                    timestamp=p["timestamp"],
                    value=p["value"],
                )
                for p in raw
            ]
        except (KeyError, TypeError) as e:
            raise DatalakeError(f"DATALAKE answered /query with a shape this client does not know: {e!r}") from e

    def series_fingerprint(self, *, source_id: str, kind: str, field: str) -> str | None:
        """What the data lake holds for one series right now: its point count and its first and last timestamp (from GET /series).
        An aggregate that was computed when this was the same is still right. `None` when the data lake does not say (an older one,
        a series that does not exist): nothing is then reused."""
        try:
            raw = self._get("/series", {"kind": kind, "limit": 100000})
        except DatalakeError as error:
            if error.status is None and "not JSON" not in str(error):
                raise  # the data lake cannot be reached at all: say so now, once, and do not try the next request
            return None
        if not isinstance(raw, list):
            return None
        for entry in raw:
            if isinstance(entry, dict) and entry.get("sourceId") == source_id and entry.get("field") == field:
                return f"{entry.get('count')}|{entry.get('firstTimestamp')}|{entry.get('lastTimestamp')}"
        return None

    def aggregate(
        self,
        *,
        source_id: str,
        kind: str,
        field: str,
        start: int,
        before: int,
        bucket_ms: int,
        agg: str = "avg",
    ) -> list[Bucket]:
        """Real GET /aggregate over the half-open window [start, before): one bucket per `bucket_ms` that holds points, aligned to `start`.

        The answer is kept for `cache_ttl_seconds`; after that the fingerprint of the series (see `series_fingerprint`) decides: the
        same fingerprint refreshes the entry, a different one (new or deleted points) asks the data lake again."""
        key = (self.base_url, source_id, kind, field, agg, bucket_ms, start, before)
        now = self._clock()
        cached = self._cache.get(key) if self._cache_entries else None
        if cached is not None and now - cached[0] < self.cache_ttl_seconds:
            self.cache_hits += 1
            self._cache.move_to_end(key)
            return list(cached[2])
        fingerprint = self.series_fingerprint(source_id=source_id, kind=kind, field=field) if self._cache_entries else None
        if cached is not None and fingerprint is not None and cached[1] == fingerprint:
            self.cache_hits += 1
            self._cache[key] = (now, fingerprint, cached[2])
            self._cache.move_to_end(key)
            return list(cached[2])
        self.cache_misses += 1
        raw = self._get(
            "/aggregate",
            {"sourceId": source_id, "kind": kind, "field": field, "bucketMs": bucket_ms, "start": start, "before": before, "agg": agg},
        )
        try:
            buckets = [Bucket(bucket_start=int(b["bucketStart"]), value=float(b["value"]), count=int(b["count"])) for b in raw]
        except (KeyError, TypeError, ValueError) as e:
            raise DatalakeError(f"DATALAKE answered /aggregate with a shape this client does not know: {e!r}") from e
        if self._cache_entries and fingerprint is not None:
            self._cache[key] = (now, fingerprint, buckets)
            while len(self._cache) > self._cache_entries:
                self._cache.popitem(last=False)
        return list(buckets)

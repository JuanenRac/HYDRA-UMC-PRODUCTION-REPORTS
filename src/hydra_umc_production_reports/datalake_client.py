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

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable


class DatalakeError(RuntimeError):
    """Raised when a real request to DATALAKE fails or DATALAKE itself
    reports an error - never swallowed silently, since a report built on
    a request that silently returned nothing would look like "no data"
    instead of "couldn't ask"."""


@dataclass(frozen=True)
class Point:
    """Mirrors HYDRA-UMC-DATALAKE's own query response shape exactly
    (src/hydra_umc_datalake/store.py's Point, serialized by its api.py)."""

    source_id: str
    kind: str
    field: str
    timestamp: int
    value: float


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
    ) -> None:
        self.base_url = base_url.rstrip("/")
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
        last_error: urllib.error.URLError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                raise DatalakeError(f"DATALAKE returned HTTP {e.code} for {path}: {body}") from e
            except urllib.error.URLError as e:
                last_error = e
                if attempt < self.max_attempts:
                    self._sleep(self.retry_delay_seconds)
        raise DatalakeError(
            f"could not reach DATALAKE at {self.base_url} after {self.max_attempts} attempts: {last_error.reason}"
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

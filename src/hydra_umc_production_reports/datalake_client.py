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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


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

    def __init__(self, base_url: str, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _get(self, path: str, params: dict[str, str | int]) -> object:
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise DatalakeError(f"DATALAKE returned HTTP {e.code} for {path}: {body}") from e
        except urllib.error.URLError as e:
            raise DatalakeError(f"could not reach DATALAKE at {self.base_url}: {e.reason}") from e

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

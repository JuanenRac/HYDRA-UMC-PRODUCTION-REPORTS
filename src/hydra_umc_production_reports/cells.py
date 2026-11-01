# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/cells.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0-or-later - see LICENSE
# =============================================================================
"""OEE of a fleet grouped by cell.

A fleet report (`/reports/oee/fleet`) gives the OEE of every source and of the whole group. A plant also wants it per cell: the robots of
one cell share a station, so the cell is the unit that has a bottleneck. Which robot belongs to which cell comes from one of two places:

* the `cells` parameter of the request, `robot-1:cell-a,robot-2:cell-a,robot-3:cell-b` (it wins), or
* the robot registry of HYDRA-UMC-JOB-DISPATCHER (`GET /robots`, the `location` of each robot), when the service is started with
  `--job-dispatcher-url`. A robot without a location, and a source that is not in the registry, go to the cell `unassigned`: they are
  never dropped from the report and never guessed into a cell.

The cell is only a way to group the sources that were already reported; the formula and the numbers of each source are the same.
The queue-time bottleneck between cells needs timestamps per job that the dispatcher does not keep, and is not part of this.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .reports import FleetOEEReport, ReportError, combine_oee, find_bottleneck

UNASSIGNED = "unassigned"
MAX_CELLS = 64
MAX_ROBOTS_BYTES = 1024 * 1024


class CellMapError(ValueError):
    """The mapping of sources to cells cannot be read or fetched."""


def _label_ok(text: str) -> bool:
    return 0 < len(text) <= 128 and all(c.isalnum() or c in "._:- " for c in text)


def parse_cell_map(text: str) -> dict[str, str]:
    """`robot-1:cell-a,robot-2:cell-b` -> {robot-1: cell-a, ...}; the first colon of each pair splits the robot from the cell."""
    mapping: dict[str, str] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        source, sep, cell = part.partition(":")
        source, cell = source.strip(), cell.strip()
        if not sep or not source or not cell:
            raise CellMapError(f"{part!r} is not source:cell")
        if not _label_ok(source) or not _label_ok(cell):
            raise CellMapError(f"{part!r}: a name is at most 128 letters, digits or . _ : -")
        if source in mapping and mapping[source] != cell:
            raise CellMapError(f"the source {source!r} is in two cells")
        mapping[source] = cell
    if len({*mapping.values()}) > MAX_CELLS:
        raise CellMapError(f"at most {MAX_CELLS} cells")
    return mapping


def robot_cells(base_url: str, token: str = "", timeout_s: float = 3.0) -> dict[str, str]:
    """The cell (the `location`) of each robot in the registry of HYDRA-UMC-JOB-DISPATCHER; a robot without one is left out."""
    request = urllib.request.Request(base_url.rstrip("/") + "/robots", headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - the URL is the operator's own setting
            raw = response.read(MAX_ROBOTS_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise CellMapError(f"the robot registry of the job dispatcher could not be read: {exc}") from exc
    if len(raw) > MAX_ROBOTS_BYTES:
        raise CellMapError("the robot registry of the job dispatcher is too large")
    try:
        robots = json.loads(raw)
    except ValueError as exc:
        raise CellMapError("the robot registry of the job dispatcher is not JSON") from exc
    if not isinstance(robots, list):
        raise CellMapError("the robot registry of the job dispatcher is not a list")
    cells: dict[str, str] = {}
    for robot in robots:
        if not isinstance(robot, dict):
            continue
        lowered = {str(k).lower(): v for k, v in robot.items()}
        robot_id, location = lowered.get("id"), lowered.get("location")
        if isinstance(robot_id, str) and isinstance(location, str) and robot_id and location.strip():
            cells[robot_id] = location.strip()
    return cells


def group_by_cell(
    fleet: FleetOEEReport,
    cell_of: Mapping[str, str],
    *,
    planned_time_s: float,
    ideal_cycle_time_s: float,
) -> dict[str, dict[str, Any]]:
    """The OEE of each cell from the reports of its sources.

    Every source of the fleet (reported or failed) is in exactly one cell. A cell whose sources all failed has `combined: null`."""
    members: dict[str, list[str]] = {}
    for name in [*fleet.sources, *fleet.failed]:
        members.setdefault(cell_of.get(name) or UNASSIGNED, []).append(name)
    if len(members) > MAX_CELLS:
        raise ReportError(f"at most {MAX_CELLS} cells per report")
    cells: dict[str, dict[str, Any]] = {}
    for cell in sorted(members):
        reports = {name: fleet.sources[name] for name in members[cell] if name in fleet.sources}
        failed = {name: fleet.failed[name] for name in members[cell] if name in fleet.failed}
        combined = combine_oee(reports, planned_time_s=planned_time_s, ideal_cycle_time_s=ideal_cycle_time_s) if reports else None
        cells[cell] = {
            "sources": sorted(members[cell]),
            "failed": failed,
            "combined": asdict(combined) if combined is not None else None,
            "bottleneck": find_bottleneck(reports),
        }
    return cells

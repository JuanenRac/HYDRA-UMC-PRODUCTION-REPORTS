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
The waiting time in the queue of each cell comes from the timestamps the dispatcher keeps per job (`queue_wait_by_cell`): the time from
the submission of a job to its last assignment, grouped by the cell of the robot that got it.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .reports import FleetOEEReport, ReportError, combine_oee, find_bottleneck

UNASSIGNED = "unassigned"
MAX_CELLS = 64
MAX_ROBOTS_BYTES = 1024 * 1024
MAX_JOBS_BYTES = 8 * 1024 * 1024
MAX_JOBS = 20000  # the jobs read from the dispatcher for one report; more than this is refused, never cut


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


def dispatcher_jobs(base_url: str, token: str = "", timeout_s: float = 5.0) -> list[dict[str, Any]]:
    """The jobs of HYDRA-UMC-JOB-DISPATCHER (`GET /jobs`) with the keys in lower case; more than MAX_JOBS is refused, not cut."""
    request = urllib.request.Request(base_url.rstrip("/") + f"/jobs?limit={MAX_JOBS + 1}", headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - the URL is the operator's own setting
            raw = response.read(MAX_JOBS_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise CellMapError(f"the jobs of the job dispatcher could not be read: {exc}") from exc
    if len(raw) > MAX_JOBS_BYTES:
        raise CellMapError("the jobs of the job dispatcher are too many to read in one report")
    try:
        jobs = json.loads(raw)
    except ValueError as exc:
        raise CellMapError("the jobs of the job dispatcher are not JSON") from exc
    if not isinstance(jobs, list):
        raise CellMapError("the jobs of the job dispatcher are not a list")
    if len(jobs) > MAX_JOBS:
        raise CellMapError(f"the job dispatcher holds more than {MAX_JOBS} jobs: narrow the window or archive the old ones")
    return [{str(k).lower(): v for k, v in job.items()} for job in jobs if isinstance(job, dict)]


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of an ascending list."""
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def queue_wait_by_cell(
    jobs: list[dict[str, Any]],
    cell_of: Mapping[str, str],
    *,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    """How long the jobs of each cell waited in the queue, and the cell that waits most.

    A job counts when it was submitted inside [start_ms, end_ms) and has been given to a robot (`assignedatms` after `submittedatms`);
    its wait is the difference. The cell is that of the robot that got it; a robot with no cell, and a job that is still queued, are
    never guessed into one (the first goes to `unassigned`, the second is counted in `waiting`). Times are in seconds."""
    waits: dict[str, list[float]] = {}
    waiting = 0
    for job in jobs:
        submitted, assigned = job.get("submittedatms"), job.get("assignedatms")
        if not isinstance(submitted, (int, float)) or isinstance(submitted, bool) or not start_ms <= submitted < end_ms:
            continue
        if not isinstance(assigned, (int, float)) or isinstance(assigned, bool) or assigned < submitted or assigned == 0:
            waiting += 1
            continue
        robot = job.get("assignedrobot")
        cell = (cell_of.get(robot) if isinstance(robot, str) else None) or UNASSIGNED
        waits.setdefault(cell, []).append((assigned - submitted) / 1000.0)
    cells: dict[str, dict[str, Any]] = {}
    for cell in sorted(waits):
        values = sorted(waits[cell])
        cells[cell] = {
            "jobs": len(values),
            "meanS": round(sum(values) / len(values), 3),
            "p95S": round(_percentile(values, 0.95), 3),
            "maxS": round(values[-1], 3),
        }
    worst = None
    if len(cells) >= 2:
        name, stats = max(cells.items(), key=lambda item: (item[1]["meanS"], item[0]))
        worst = {"cell": name, "meanS": stats["meanS"], "p95S": stats["p95S"]}
    return {"byCell": cells, "stillWaiting": waiting, "bottleneck": worst}

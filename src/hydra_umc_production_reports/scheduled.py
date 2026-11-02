# =============================================================================
# HYDRA-UMC-PRODUCTION-REPORTS - src/hydra_umc_production_reports/scheduled.py
# Copyright (C) 2026 JuanenRac (Electro Hobby 3D) <electrohobby3d@gmail.com>
# GPL-3.0 - see LICENSE
# =============================================================================
"""Daily and weekly reports written to disk by a systemd timer.

One run builds, for every source in the configuration file, the OEE report and the availability report of the last complete
day (or week) and writes them as CSV and/or HTML, plus a manifest that records the sha256 of every file, the formula versions,
the input fingerprints and the provenance of each report, so a file can be checked later (`--verify`). The job does nothing
until `enabled` is true in the configuration file: it is off by default. Every value that can be tuned lives in that file, none in
the code. Files go only under `output_dir`, in a folder per period and window, and are written atomically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .datalake_client import DatalakeClient, DatalakeError
from .export import export_availability_csv, export_availability_html, export_oee_csv, export_oee_html
from .reports import (
    DEFAULT_STATE_LOOKBACK_MS,
    ReportError,
    availability_from_datalake,
    availability_from_states_datalake,
    oee_from_datalake,
)
from .shift import day_window_ms

PERIODS = {"daily": 1, "weekly": 7}
FORMATS = ("csv", "html")
MANIFEST_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ScheduleError(ValueError):
    """The configuration file or a command-line value cannot be used."""


@dataclass(frozen=True)
class SourceConfig:
    id: str
    # "gaps": downtime inferred from gaps in a telemetry series; "state": explicit up/down events; "off": no availability report.
    availability_mode: str = "gaps"
    availability_kind: str = ""
    availability_field: str = ""
    expected_interval_ms: float = 1000.0
    gap_factor: float = 3.0
    lookback_ms: int = DEFAULT_STATE_LOOKBACK_MS
    planned_time_s_per_day: float | None = None
    ideal_cycle_time_s: float | None = None


@dataclass(frozen=True)
class ScheduleConfig:
    enabled: bool = False
    datalake_url: str = "http://127.0.0.1:8095"
    output_dir: str = ""
    formats: tuple[str, ...] = ("csv", "html")
    timezone_offset_minutes: int = 0
    planned_time_s_per_day: float = 28800.0
    ideal_cycle_time_s: float = 10.0
    sources: tuple[SourceConfig, ...] = field(default_factory=tuple)


def _number(raw: object, name: str, *, positive: bool = False, minimum: float | None = None) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ScheduleError(f"{name} must be a number")
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise ScheduleError(f"{name} must be a finite number")
    if positive and value <= 0:
        raise ScheduleError(f"{name} must be greater than 0")
    if minimum is not None and value < minimum:
        raise ScheduleError(f"{name} must be at least {minimum}")
    return value


def parse_config(data: object) -> ScheduleConfig:
    if not isinstance(data, dict):
        raise ScheduleError("the configuration must be a JSON object")
    known = {"enabled", "datalake_url", "output_dir", "formats", "timezone_offset_minutes", "planned_time_s_per_day", "ideal_cycle_time_s", "sources"}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ScheduleError(f"unknown configuration keys: {unknown}")
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ScheduleError("enabled must be true or false")
    formats = tuple(data.get("formats", ["csv", "html"]))
    if not formats or any(f not in FORMATS for f in formats):
        raise ScheduleError(f"formats must be a non-empty list of {list(FORMATS)}")
    sources: list[SourceConfig] = []
    seen: set[str] = set()
    for entry in data.get("sources", []):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise ScheduleError("every source needs a text id")
        sid = entry["id"]
        if not _SAFE_ID.match(sid):
            raise ScheduleError(f"source id {sid!r} is not usable as a file name (letters, digits, . _ -, up to 64)")
        if sid in seen:
            raise ScheduleError(f"source id {sid!r} is repeated")
        seen.add(sid)
        availability = entry.get("availability", {})
        if not isinstance(availability, dict):
            raise ScheduleError("availability must be an object")
        mode = availability.get("mode", "gaps")
        if mode not in ("gaps", "state", "off"):
            raise ScheduleError("availability.mode must be gaps, state or off")
        if mode != "off" and not (isinstance(availability.get("kind"), str) and isinstance(availability.get("field"), str)):
            raise ScheduleError(f"source {sid!r}: availability needs kind and field")
        sources.append(
            SourceConfig(
                id=sid,
                availability_mode=mode,
                availability_kind=availability.get("kind", ""),
                availability_field=availability.get("field", ""),
                expected_interval_ms=_number(availability.get("expected_interval_ms", 1000.0), "expected_interval_ms", positive=True),
                gap_factor=_number(availability.get("gap_factor", 3.0), "gap_factor", minimum=1),
                lookback_ms=int(_number(availability.get("lookback_ms", DEFAULT_STATE_LOOKBACK_MS), "lookback_ms", minimum=0)),
                planned_time_s_per_day=None if "planned_time_s_per_day" not in entry else _number(entry["planned_time_s_per_day"], "planned_time_s_per_day", positive=True),
                ideal_cycle_time_s=None if "ideal_cycle_time_s" not in entry else _number(entry["ideal_cycle_time_s"], "ideal_cycle_time_s", positive=True),
            )
        )
    output_dir = data.get("output_dir", "")
    if not isinstance(output_dir, str):
        raise ScheduleError("output_dir must be text")
    url = data.get("datalake_url", "http://127.0.0.1:8095")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise ScheduleError("datalake_url must be an http(s) URL")
    return ScheduleConfig(
        enabled=enabled,
        datalake_url=url,
        output_dir=output_dir,
        formats=formats,
        timezone_offset_minutes=int(_number(data.get("timezone_offset_minutes", 0), "timezone_offset_minutes", minimum=-14 * 60)),
        planned_time_s_per_day=_number(data.get("planned_time_s_per_day", 28800.0), "planned_time_s_per_day", positive=True),
        ideal_cycle_time_s=_number(data.get("ideal_cycle_time_s", 10.0), "ideal_cycle_time_s", positive=True),
        sources=tuple(sources),
    )


def load_config(path: str) -> ScheduleConfig:
    try:
        with open(path, encoding="utf-8") as handle:
            return parse_config(json.load(handle))
    except OSError as exc:
        raise ScheduleError(f"cannot read the configuration: {exc.strerror or type(exc).__name__}") from exc
    except json.JSONDecodeError as exc:
        raise ScheduleError(f"the configuration is not valid JSON: {exc.msg}") from exc


def period_window(period: str, until: date, *, timezone_offset_minutes: int = 0) -> tuple[int, int]:
    """[start, end) in epoch ms of the `period` that ends at the local midnight that opens `until`."""
    if period not in PERIODS:
        raise ScheduleError(f"period must be one of {sorted(PERIODS)}")
    start, _ = day_window_ms(until - timedelta(days=PERIODS[period]), timezone_offset_minutes=timezone_offset_minutes)
    end, _ = day_window_ms(until, timezone_offset_minutes=timezone_offset_minutes)
    return start, end


def _write_atomic(path: Path, text: str) -> str:
    data = text.encode("utf-8")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    return hashlib.sha256(data).hexdigest()


def run_schedule(
    config: ScheduleConfig,
    client: DatalakeClient,
    *,
    period: str,
    until: date,
) -> tuple[int, dict[str, Any] | None]:
    """Build and write the reports of one period. Returns (exit code, manifest); 0 when every report was written, 1 when any
    source failed (the others are still written and the failure is in the manifest), None for the manifest when disabled."""
    if not config.enabled:
        return 0, None
    if not config.output_dir:
        raise ScheduleError("output_dir is empty")
    if not config.sources:
        raise ScheduleError("sources is empty")
    start_ms, end_ms = period_window(period, until, timezone_offset_minutes=config.timezone_offset_minutes)
    days = PERIODS[period]
    folder = Path(config.output_dir) / period / until.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for src in config.sources:
        planned = (src.planned_time_s_per_day or config.planned_time_s_per_day) * days
        ideal = src.ideal_cycle_time_s or config.ideal_cycle_time_s

        def emit(kind: str, report: Any, csv_fn: Any, html_fn: Any, src: SourceConfig = src) -> None:
            for fmt in config.formats:
                text = csv_fn(report, source_id=src.id, start_ms=start_ms, end_ms=end_ms) if fmt == "csv" else html_fn(
                    report, source_id=src.id, start_ms=start_ms, end_ms=end_ms
                )
                name = f"{src.id}-{kind}.{fmt}"
                files.append({"name": name, "sha256": _write_atomic(folder / name, text), "bytes": len(text.encode("utf-8"))})
            row = asdict(report)
            reports.append(
                {
                    "sourceId": src.id,
                    "report": kind,
                    "formulaVersion": report.formula_version,
                    "inputFingerprint": report.input_fingerprint,
                    "method": getattr(report, "method", None),
                    "provenance": row.get("provenance"),
                }
            )

        try:
            emit("oee", oee_from_datalake(client, source_id=src.id, start_ms=start_ms, end_ms=end_ms, planned_time_s=planned, ideal_cycle_time_s=ideal), export_oee_csv, export_oee_html)
        except (ReportError, DatalakeError, ValueError) as exc:
            errors.append({"sourceId": src.id, "report": "oee", "error": str(exc)})
        if src.availability_mode == "off":
            continue
        try:
            if src.availability_mode == "state":
                avail = availability_from_states_datalake(
                    client, source_id=src.id, kind=src.availability_kind, field=src.availability_field,
                    start_ms=start_ms, end_ms=end_ms, lookback_ms=src.lookback_ms,
                )
            else:
                avail = availability_from_datalake(
                    client, source_id=src.id, kind=src.availability_kind, field=src.availability_field,
                    start_ms=start_ms, end_ms=end_ms, expected_interval_ms=src.expected_interval_ms, gap_factor=src.gap_factor,
                )
            emit("availability", avail, export_availability_csv, export_availability_html)
        except (ReportError, DatalakeError, ValueError) as exc:
            errors.append({"sourceId": src.id, "report": "availability", "error": str(exc)})
    manifest = {
        "manifestVersion": MANIFEST_VERSION,
        "generatorVersion": __version__,
        "period": period,
        "windowStartMs": start_ms,
        "windowEndMs": end_ms,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": sorted(files, key=lambda f: f["name"]),
        "reports": reports,
        "errors": errors,
    }
    _write_atomic(folder / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return (1 if errors else 0), manifest


def verify_folder(folder: str) -> list[str]:
    """The problems found when the files of a folder are compared with the sha256 in its manifest (empty when all is intact)."""
    base = Path(folder)
    try:
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest.json: {exc}"]
    problems: list[str] = []
    for entry in manifest.get("files", []):
        name = entry.get("name", "")
        if not _SAFE_FILE.match(name):
            problems.append(f"{name!r}: not a file name this job writes")
            continue
        try:
            digest = hashlib.sha256((base / name).read_bytes()).hexdigest()
        except OSError:
            problems.append(f"{name}: missing")
            continue
        if digest != entry.get("sha256"):
            problems.append(f"{name}: sha256 differs from the manifest")
    return problems


_SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.(csv|html)$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hydra-umc-production-reports-scheduled")
    parser.add_argument("--config", required=True, help="JSON configuration file (see systemd/scheduled-reports.example.json)")
    parser.add_argument("--period", choices=sorted(PERIODS), help="daily or weekly")
    parser.add_argument("--until", help="the period ends at the local midnight that opens this day, YYYY-MM-DD (default: today)")
    parser.add_argument("--verify", metavar="FOLDER", help="check the files of a report folder against its manifest and exit")
    args = parser.parse_args(argv)
    if args.verify:
        problems = verify_folder(args.verify)
        for line in problems:
            print(f"[scheduled-reports] {line}", file=sys.stderr)
        return 1 if problems else 0
    if not args.period:
        parser.error("--period is required")
    try:
        config = load_config(args.config)
        if args.until:
            try:
                until = date.fromisoformat(args.until)
            except ValueError:
                raise ScheduleError("--until must be YYYY-MM-DD") from None
        else:
            until = (datetime.now(timezone.utc) + timedelta(minutes=config.timezone_offset_minutes)).date()
        client = DatalakeClient(config.datalake_url)
        code, manifest = run_schedule(config, client, period=args.period, until=until)
    except ScheduleError as exc:
        print(f"[scheduled-reports] fatal: {exc}", file=sys.stderr)
        return 2
    if manifest is None:
        print("[scheduled-reports] disabled in the configuration (enabled is false): nothing written")
        return 0
    print(f"[scheduled-reports] {len(manifest['files'])} files written, {len(manifest['errors'])} failures")
    for item in manifest["errors"]:
        print(f"[scheduled-reports] {item['sourceId']} {item['report']}: {item['error']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())

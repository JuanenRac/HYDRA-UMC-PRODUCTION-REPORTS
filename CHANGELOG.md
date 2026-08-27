# Changelog

All notable work on **HYDRA-UMC-PRODUCTION-REPORTS** is summarized here, newest first. Full
session-by-session detail (including dates) lives in a private,
unpublished internal log - this file is public, so it intentionally
omits calendar dates.

## Versioning scheme

`pyproject.toml`'s `version` field bumps automatically on every real
build (`build.sh`/`.bat` - see `bump_version.py`, run as the first real
step of both scripts).

It follows the ecosystem-wide base-10 "odometer" rule rather than
semantic-versioning judgment calls:

- `PATCH` +1 on every build
- when `PATCH` would exceed 9, it resets to 0 and `MINOR` +1 instead (e.g. `0.0.9` -> `0.1.0`, never `0.0.10`)
- the same carry cascades into `MAJOR` if `MINOR` would exceed 9

---

## Documentation - Real HTTP API reference

- **`docs/API.md`** (new) - every real endpoint (`GET /reports/oee`,
  `GET /reports/availability`, `GET /stats`) documented from the actual
  handler code in `api.py`: required query params, real example
  responses with every field's exact meaning (from `oee.py`/
  `availability.py`), and the `502` vs `400` error distinction (DATALAKE
  unreachable vs. bad input). Cross-checked field-by-field against
  `tests/test_api.py`'s real assertions (30/30 tests passing).
  Documentation-only - no code changed, no version bump.

---

## [0.0.2] - Real OEE/availability reporting, real integration with HYDRA-UMC-DATALAKE

- **`src/hydra_umc_production_reports/oee.py`** - real, standard industrial OEE formula (Availability x Performance x Quality), computed from a real list of `ProductionEvent` records. `Performance` and `Availability` are explicitly clamped to `[0, 1]` so noisy/optimistic inputs (a real cycle running faster than a conservative "ideal" figure, or operating time exceeding a mis-set planned window) never report a misleading >100%. Raises `OEEError` for real, unrepresentable inputs (zero events, non-positive planned time or ideal cycle time) instead of returning a misleading number.
- **`src/hydra_umc_production_reports/availability.py`** - real machine-availability estimation from ANY existing telemetry stream already sitting in HYDRA-UMC-DATALAKE (not requiring a special schema): a gap between consecutive sample timestamps larger than `expected_interval_ms * gap_factor` (default `gap_factor=3.0`, tuned so ordinary jitter isn't misclassified as an outage) counts as real downtime, including leading/trailing gaps against the query window's own edges.
- **`src/hydra_umc_production_reports/datalake_client.py`** - a real HTTP client (stdlib `urllib`, no new runtime dependency) for HYDRA-UMC-DATALAKE's actual `GET /query` API. Deliberately does NOT import DATALAKE's Python package directly even though both sit in the same dev environment - real HTTP is the real, decoupled integration seam matching how separate repos/services would actually talk in production.
- **`src/hydra_umc_production_reports/reports.py`** - the real orchestration layer tying `datalake_client.py` to `oee.py`/`availability.py`, making this project's own "built from HYDRA-UMC-DATALAKE history" claim actually true. Defines and documents this project's own v0 convention for a `production_event` DATALAKE sample (`kind="production_event"`, fields `"good"` and `"cycleTimeS"` written together at the same timestamp) - explicitly noted as this project's own invention, not an established ecosystem-wide schema; no other project writes this kind yet (HYDRA-UMC-JOB-DISPATCHER would be the real future source - see `mejoras_futuras.txt`). `oee_from_datalake()` queries both fields separately and joins them by exact-timestamp match, honestly counting and reporting any unmatched `"good"` readings instead of defaulting a missing cycle time to 0s.
- **`src/hydra_umc_production_reports/api.py`** - real `http.server`-based HTTP API (same convention as HYDRA-UMC-DATALAKE's and HYDRA-UMC-ANOMALY-DETECTOR's own `api.py`): `GET /reports/oee`, `GET /reports/availability`, `GET /stats`. Every report request makes a real live call to a running HYDRA-UMC-DATALAKE instance - this project holds no store of its own, it is a real-time query+compute layer over its parent's data.
- **`src/hydra_umc_production_reports/main.py`** - rewritten around `--datalake-url` (default `http://localhost:8095`), `--addr`, `--port`, starting a real `ReportsServer`.
- **Real cross-service integration verified end-to-end**: a real HYDRA-UMC-DATALAKE instance was started, seeded via real HTTP POSTs to `/ingest` with both `production_event` and `motor_temp`-shaped samples (the same shape HYDRA-UMC-TELEMETRY-COLLECTOR's own new `DatalakeSink` writes), and this project's real HTTP API was hit for real - `GET /reports/oee` and `GET /reports/availability` returned correct, hand-checkable numbers computed from data that traveled over a real socket the whole way, not from data handed directly to Python objects.
- **28 new tests** across `tests/test_oee.py`, `tests/test_availability.py`, `tests/test_datalake_client.py`, `tests/test_reports.py` and `tests/test_api.py` (30 total with the 2 pre-existing) - including real round-trips against a real (if fake, `tests/fake_datalake.py`) HTTP server implementing HYDRA-UMC-DATALAKE's actual `/query` contract, and real HTTP round-trips against a real `ReportsServer`.
- **`build.sh`/`build.bat`** - now install with dev extras and run the real test suite as their final step (matching HYDRA-UMC-DATALAKE/HYDRA-UMC-ANOMALY-DETECTOR's own build scripts); `build.sh`/`build.bat`/`run.sh`/`run.bat` no longer auto-close their window on completion.
- Removed unused empty `docs/`, `images/`, `scripts/`, `build/` scaffold folders - all real source now lives under `src/`.

## [0.0.1] - Initial scaffolding

- **`src/hydra_umc_production_reports/main.py`** - minimal real entry point. No reporting logic yet - shift/OEE/production-run report generation over HYDRA-UMC-DATALAKE's own telemetry lands in a later pass.
- **`pyproject.toml`** - packaging metadata, no runtime dependencies yet.
- **`bump_version.py`** - ecosystem-standard odometer bump script.
- **`build.sh` / `build.bat`**, **`run.sh` / `run.bat`** - venv creation, editable install, compile-check, and entry-point execution.

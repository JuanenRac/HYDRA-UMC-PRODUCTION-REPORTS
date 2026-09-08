# Changelog

All notable work on **HYDRA-UMC-PRODUCTION-REPORTS** is summarized here, newest first.
This file intentionally omits calendar dates from individual entries.

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

## [0.0.9] - C09/F04: a real Telemetry-Collector -> Datalake -> Production-Reports chain test

Every real stage of this chain had its own isolated test, but none of
them proved the FULL real path together - and this repo's own
`tests/fake_datalake.py` was itself part of the gap: a real HTTP server,
but a hand-written reimplementation of `HYDRA-UMC-DATALAKE`'s own query
contract, not the real thing.

New `tests/test_real_telemetry_to_report_chain.py` starts the REAL
HYDRA-UMC-DATALAKE package (a real sibling checkout, skips if not
present), ingests real samples using the exact real sourceId/kind/
timestamp/fields shape HYDRA-UMC-TELEMETRY-COLLECTOR's own
`sink/datalake.go` sends (confirmed by reading it directly), then runs
this repo's own real `availability_from_datalake()` against that real,
live-queried data: a real 40s gap in arrival becomes a real reported
downtime period, continuous arrival reports full availability, and a
source that never reports at all is honestly reported as fully down for
the whole window - not each repo trusting the other's own unit tests in
isolation.

Verified: full pytest suite (68/68, 4 new), `tools/ci_validate.py` PASS.

## [0.0.8] - DOC-32: removed private-document references

- **DOC-32 (found in an ecosystem-wide software-improvements audit, P2):**
  `mejoras_futuras.txt` is a real, tracked, public file in this repo -
  but every one of its 12 cross-references (`CHANGELOG.md` x3, README in
  all 7 languages, `reports.py`, `shift.py`) required the reader to jump
  elsewhere for a short reason that fits the sentence itself. Each site
  now states its own reason inline; `mejoras_futuras.txt` stays as the
  one place all deferred items are written up in full, its own header no
  longer claims references that don't exist inline anymore, and it is
  now listed in the README's own directory structure (all 7 languages)
  so it stays discoverable.
- **`datalake_client.py`'s `DatalakeClient` now retries a transient
  network failure** (`max_attempts=3` by default, with a short delay
  between attempts) - found in an ecosystem-wide software-improvements
  audit: it had a timeout but no retry, so one network hiccup (a dropped
  connection, a momentary DNS blip, DATALAKE mid-restart) failed an
  entire report (daily/weekly/monthly) instead of retrying before
  surfacing the `DatalakeError` that already exists. Only a transient
  `URLError` is retried - a real HTTP response DATALAKE itself sent is
  never transient in that sense and still raises immediately. New
  regression tests prove the retry against a real socket (a genuine
  `ConnectionRefusedError` on the first attempt, a real server brought up
  before the second) and that an HTTP error is never retried.
- **`main.py`**'s `--addr` flag now defaults to `127.0.0.1` instead of
  `0.0.0.0` - an unqualified default binds every interface, and this HTTP
  API (`GET /reports/oee`, `GET /reports/availability`, `GET /stats`) has
  no authentication of its own. The real CM5 deployment was never exposed
  (`systemd/hydra-umc-production-reports.service` already pinned
  `--addr 127.0.0.1` explicitly, precisely because the code default was
  unsafe), but running the binary directly without `--addr` - local dev,
  a manual invocation - listened on every interface by default. Same
  class of bug already fixed in HYDRA-UMC-DATALAKE/ANOMALY-DETECTOR and
  HYDRA-UMC-TELEMETRY-COLLECTOR.
- Rejects repeated query parameters with `400`. Report inputs such as
  `sourceId`, time boundaries and metric fields can no longer be silently
  selected from a duplicated URL value.
- **`.github/workflows/ci.yml`** - the real `tests/` pytest suite is now
  actually installed and run in CI. The baseline workflow's Python
  handling previously only compile-checked (`py_compile`) every `.py`
  file and validated the manifest/docs - it never ran `pytest`, so a
  regression in `tests/` could be merged without CI ever failing.
  CI-only fix, no runtime code changed, no version bump.

---

## Documentation - Real HTTP API reference

- **`docs/API.md`** (new) - every real endpoint (`GET /reports/oee`,
  `GET /reports/availability`, `GET /stats`) documented from the actual
  handler code in `api.py`: required query params, real example
  responses with every field's exact meaning (from `oee.py`/
  `availability.py`), and the `502` vs `400` error distinction (DATALAKE
  unreachable vs. bad input). Cross-checked field-by-field against
  `tests/test_api.py`'s real assertions (12/12 tests passing).
  Documentation-only - no code changed, no version bump.

---

## [0.0.7]

- **Fixed a real bug found by an ecosystem-wide bug audit: report
  windows could be silently truncated with no signal at all.**
  `oee_from_datalake()`/`availability_from_datalake()` used to call
  `DatalakeClient.query()` with its own default `limit=10000` and never
  checked whether that limit was actually hit. `HYDRA-UMC-DATALAKE`'s own
  `store.py` orders every query ascending by timestamp, so a
  limit-truncated result silently keeps only the EARLIEST rows in the
  window and drops the rest - a real source reporting once a second
  blows through 10000 points in well under 3 hours of a real 24h shift,
  and the resulting OEE/Availability report looked like a real, complete
  number computed from the requested window while actually covering only
  a fraction of it. New `_query_all_or_raise()` (queries with a real
  200,000-point headroom and raises a clear `ReportError` naming exactly
  what happened if that cap is reached) replaces the raw `client.query()`
  calls in both report functions - a truncated window now fails loudly
  instead of silently returning a wrong-but-plausible number. 3 new
  tests (2 confirming the honest failure, 1 confirming a real, non-
  truncated result is never falsely flagged) - `tests/fake_datalake.py`
  gained real `limit` handling (it previously ignored the parameter
  entirely) so the fake server's own behavior actually matches
  DATALAKE's real `LIMIT` semantics.

## [0.0.6] - Real CM5 deployment

- **`systemd/hydra-umc-production-reports.service`** (new) - loopback-only
  unit for `HYDRA-UMC-OS/provisioning/install_production_reports.sh`
  (new, that repo), same real "copy src/ + PYTHONPATH" shape as
  `install_datalake.sh` - this repo is genuinely stdlib-only Python (a
  real `ThreadingHTTPServer`, no third-party runtime dependency). `--addr`
  pinned to `127.0.0.1` in the unit, overriding this module's own `0.0.0.0`
  default, for consistency with every other internal-only API already on
  this CM5. Real gap found auditing the ecosystem against actual CM5
  hardware: the real OEE/availability reporting engine had never been
  installed anywhere.

## [0.0.5] - Fixed a real version-mirror drift

- **`src/hydra_umc_production_reports/__init__.py`**'s `__version__` had
  fallen one real build behind `pyproject.toml`/the manifest - running
  only `bump_manifest_version.py` (which only touches its declared
  `native_version.file`, pyproject.toml) without this repo's separate
  `bump_version.py` (the one that keeps `__init__.py` mirrored) leaves
  the two drifting apart. Fixed via the real, intended sequence
  (`bump_version.py` then `bump_manifest_version.py --sync`).

## [0.0.4] - Real ecosystem live-status opt-in

- **`hydra-umc.project.json`** declares its real `service.port` (8099)
  and `health_path` (`/stats`) - HYDRA-UMC-SERVER's ecosystem status
  endpoint now does a real HTTP GET against it (expecting 2xx) instead
  of only reporting static manifest metadata.

## [0.0.3] - Shift/day boundaries, versioned formulas with real traceability, reproducible CSV export

- **`shift.py`** (new) - a real, single source of truth for where a shift or calendar day starts/ends, so two reports can't silently disagree about the window they both claim to describe (the exact risk the promotion audit flagged). `day_window_ms()`/`ShiftSchedule`/`shift_window_ms()`/`shift_index_for_timestamp()`: real UTC-ms boundaries for a fixed timezone offset (DST intentionally not handled), a real night shift correctly crossing midnight into the next calendar day, and a real inverse lookup (which day/shift a timestamp falls into) proven to round-trip through every shift of a schedule.
- **Real formula versioning + input traceability** (`oee.py`/`availability.py`) - every `OEEReport`/`AvailabilityReport` now carries `formula_version` (`"oee-v1"`/`"availability-v1"`, bumped only if the formula itself changes) and a real `input_fingerprint` - a sha256 over the exact, order-independent input data (production events / sample timestamps) that produced it. Two reports built from the same real data always get the same fingerprint; any real difference in the input changes it. Both fields are additive on `GET /reports/oee`/`GET /reports/availability`.
- **`export.py`** (new) + **`GET /reports/oee/export`**, **`GET /reports/availability/export`** - a real, byte-for-byte reproducible CSV rendering of either report: fixed field order, fixed float formatting, and a header recording the real range (`sourceId`/`startMs`/`endMs`) and any filters, so the file is self-describing even detached from the request that produced it. Calling either export endpoint twice with identical parameters against identical DATALAKE history returns identical bytes - proven by a real test, not assumed.
- 27 new tests (`tests/test_shift.py`, `tests/test_export.py` new, plus additions to `test_oee.py`/`test_availability.py`/`test_api.py`) = 57 total.
- Real verification beyond the test suite: ran a real `ReportsServer` against a real (fake) DATALAKE, fetched both the JSON and CSV-export forms of a real OEE report over an actual socket, and confirmed the two CSV fetches were byte-identical.
- What's still not real, on purpose: plant/cell authorization before querying aggregates, PDF export, and DST-aware timezones.

## [0.0.2] - Real OEE/availability reporting, real integration with HYDRA-UMC-DATALAKE

- **`src/hydra_umc_production_reports/oee.py`** - real, standard industrial OEE formula (Availability x Performance x Quality), computed from a real list of `ProductionEvent` records. `Performance` and `Availability` are explicitly clamped to `[0, 1]` so noisy/optimistic inputs (a real cycle running faster than a conservative "ideal" figure, or operating time exceeding a mis-set planned window) never report a misleading >100%. Raises `OEEError` for real, unrepresentable inputs (zero events, non-positive planned time or ideal cycle time) instead of returning a misleading number.
- **`src/hydra_umc_production_reports/availability.py`** - real machine-availability estimation from ANY existing telemetry stream already sitting in HYDRA-UMC-DATALAKE (not requiring a special schema): a gap between consecutive sample timestamps larger than `expected_interval_ms * gap_factor` (default `gap_factor=3.0`, tuned so ordinary jitter isn't misclassified as an outage) counts as real downtime, including leading/trailing gaps against the query window's own edges.
- **`src/hydra_umc_production_reports/datalake_client.py`** - a real HTTP client (stdlib `urllib`, no new runtime dependency) for HYDRA-UMC-DATALAKE's actual `GET /query` API. Deliberately does NOT import DATALAKE's Python package directly even though both sit in the same dev environment - real HTTP is the real, decoupled integration seam matching how separate repos/services would actually talk in production.
- **`src/hydra_umc_production_reports/reports.py`** - the real orchestration layer tying `datalake_client.py` to `oee.py`/`availability.py`, making this project's own "built from HYDRA-UMC-DATALAKE history" claim actually true. Defines and documents this project's own v0 convention for a `production_event` DATALAKE sample (`kind="production_event"`, fields `"good"` and `"cycleTimeS"` written together at the same timestamp) - explicitly noted as this project's own invention, not an established ecosystem-wide schema; no other project writes this kind yet (HYDRA-UMC-JOB-DISPATCHER would be the real future source, once it's wired to report completions this way). `oee_from_datalake()` queries both fields separately and joins them by exact-timestamp match, honestly counting and reporting any unmatched `"good"` readings instead of defaulting a missing cycle time to 0s.
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

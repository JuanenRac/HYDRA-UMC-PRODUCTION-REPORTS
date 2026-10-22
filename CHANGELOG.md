# Changelog

All notable changes to this project are documented here.

## [Unreleased]

- The banner follows the animated style of the ecosystem banners, with the name, the kind and the technologies of the project.
- Added `tools/hydra_project_tool.py`: the same `validate`, `test`, `version`, `status` and `bump` commands in every repository. The checks run with `HYDRA_UMC_CI=1` and fail if the version changes; only `bump` changes it.

## [0.1.5]

- Maturity is functional: nothing here has been verified on real hardware yet.
- The READMEs say, in the eight languages, what is planned and what has only been tested against fakes, emulators or fixtures, and carry a short safety and security status note.

## [0.1.4] - Reports that refuse bad numbers, do not double-count a boundary and say what they dropped

- **Non-finite numbers are refused.** `plannedTimeS=nan` produced `NaN` availability and OEE (and `NaN` is not JSON), `expectedIntervalMs=nan` or `inf` with no samples at all reported 100% availability for a machine that sent nothing, and `gapFactor=nan` or `-1` did the same or the opposite. Planned time, ideal cycle time and expected interval must be finite and greater than 0, `gapFactor` finite and at least 1, and responses are written with `allow_nan=False`, so a bad value can no longer leave as a number that is not JSON.
- **A DATALAKE that fails halfway no longer closes the connection on the caller.** A timeout or reset while the answer was being read, a hang-up with no answer, or a body that is not JSON escaped as a bare exception (no retry, no answer, or a `400`). Those are retried like any network failure and then reported as `502`; any other failure answers `500`.
- **Windows are `[start, end)`.** The query asked DATALAKE for the end inclusive, so a cycle exactly on a shift boundary counted in two consecutive reports and shift totals exceeded the day. A sample at `end` now belongs to the next window only. `end` must be after `start` (`400`, instead of "no data found").
- **Inconsistent input is flagged.** `inconsistent_input` is `true` when the cycles add up to more operating time than the planned time, or the planned time is longer than the window: availability is capped and the product understates the real figure.
- **Cycles that share a millisecond are paired in order,** instead of every `good` of that millisecond taking the last cycle time. Cycle times with no result are counted too, and a `good` that is not exactly 0 or 1 (a `NaN` counted as a good part) is discarded and counted.
- **The fingerprint really is order-independent** when events share a timestamp.
- **The exports show what was dropped.** CSV and HTML carry `unmatched_count` and `inconsistent_input` (`exportVersion` 2), and the HTML page carries the provenance. The CSV formula guard also covers cells that start with spaces, `|`, `%` or a line break.
- **Shifts are wired in.** `day=YYYY-MM-DD` (and `shift=`, `shiftLengthHours`, `firstShiftStartHour`, `tzOffsetMinutes`) derive the window and, by default, the planned time, so reports of a plant agree on where a shift begins. Shift lengths are computed in whole minutes: five shifts of 4.8 h are valid (floating point rejected them).
- **Access and load.** The API listens on loopback by default; another address needs `--token` / `PRODUCTION_REPORTS_TOKEN` or `--allow-anonymous`, and the process refuses to start otherwise. Only `--max-concurrent-reports` reports (default 4) are built at once, with `503` beyond that. `GET /health` is open; the systemd unit gains `PrivateDevices=true` and an optional environment file for the token.
- A workflow checks out HYDRA-UMC-DATALAKE and runs the real-DATALAKE chain tests that were skipped in CI. The README and `docs/API.md` document `format=html`, the shift parameters and the new fields, the diagram marks PDF, templating and delivery as planned, and the note on who produces `production_event` no longer names a dispatcher that keeps no timestamps. 122 tests.

## [0.1.3] - CSV formula guard, idle connections dropped, Portuguese README

- **Spreadsheet formula injection in the CSV export:** the source id and the filter values written into the CSV come from the request or from a device, so a source id such as `=HYPERLINK("http://...","click")` was written verbatim and ran as a formula when the file was opened in Excel, LibreOffice or Google Sheets (CWE-1236). A free-text cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return now gets a leading apostrophe; numbers are never touched (a negative value legitimately starts with `-`) and an ordinary export is byte-for-byte what it was.
- **Idle connections are dropped:** the HTTP handler had no connection timeout, so a client that opened a connection and sent nothing held a handler thread forever. A silent client is now dropped after 15 seconds; the limit covers socket I/O only, never the time a report takes to compute.
- **Tests:** new tests cover the formula guard (a hostile source id and filter values, and that an ordinary CSV is unchanged) and the dropped idle connection, 89 in total.
- **Docs:** the README says `export.py` also writes a self-contained HTML page (it only claimed CSV), documents the formula guard and corrects the test count, and a README in Portuguese (`README_por.md`) joins the other translations.

## [0.1.2] - Reports say where their numbers came from

- OEE and availability reports built from DATALAKE now carry a `provenance` block: source,
  series kind and fields, the time window, how many samples were used and the version of
  this program that computed it (it appears in the API JSON). CSV exports add `pointsUsed`
  and `generatorVersion` rows when it is present. A report computed directly, without a
  source, has no provenance and its CSV is unchanged.

## [0.1.1]

- Added a real HTML export option for both OEE and availability reports (`GET .../export?format=html`), alongside the existing CSV/JSON output: a single self-contained page with an embedded, hand-generated SVG chart (a bar chart of Availability/Performance/Quality/OEE, or a downtime timeline for availability) and no external JS/CSS dependency of any kind, consistent with this project's own existing avoidance of heavy dependencies.

## [0.1.0] - An unvalidated cycle time, and an incomplete cycle disappearing silently

- `compute_oee()`'s `sum(e.cycle_time_s for e in events)`
  accepted any float, including negative or non-finite (NaN/Infinity)
  values, with no per-event check. A single bad reading (real
  clock-skew/sensor error, or a loosely-typed loader letting NaN/
  Infinity through) could make `operating_time_s` itself negative or
  non-finite - `availability` is never clamped on its lower bound, so
  this silently produced a nonsensical report instead of the honest
  `OEEError` this function already raises for every other
  unrepresentable input. Now every event's `cycle_time_s` is validated
  as a finite, non-negative real number first.
- `oee_from_datalake()`'s own `unmatched` counter (a "good"
  reading with no matching "cycleTimeS" at the same timestamp - a real,
  incomplete cycle) only ever surfaced inside the `ReportError` message,
  reachable only when EVERY cycle failed to match. A partial mismatch
  (some cycles complete, some not) silently computed a real, successful
  `OEEReport` from fewer events than actually existed, with no trace
  that any were dropped. `OEEReport` gains a new `unmatched_count` field
  (defaults to `0` for `compute_oee()`'s own direct callers, which have
  no concept of it) - `oee_from_datalake()` now attaches the real count
  to every report it returns, successful or not.
- 4 new regression tests.

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

## [0.0.8] - removed private-document references

- **Removed dangling private-document references:**
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
  between attempts) - found on code review: it had a timeout but
  no retry, so one network hiccup (a dropped connection, a momentary DNS
  blip, DATALAKE mid-restart) failed an
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

- **Fixed a real bug found on code review: report
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
  this CM5. Real gap found checking the ecosystem against actual CM5 hardware: the real OEE/availability reporting engine had never been
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

- **`shift.py`** (new) - a real, single source of truth for where a shift or calendar day starts/ends, so two reports can't silently disagree about the window they both claim to describe (the exact risk flagged in review). `day_window_ms()`/`ShiftSchedule`/`shift_window_ms()`/`shift_index_for_timestamp()`: real UTC-ms boundaries for a fixed timezone offset (DST intentionally not handled), a real night shift correctly crossing midnight into the next calendar day, and a real inverse lookup (which day/shift a timestamp falls into) proven to round-trip through every shift of a schedule.
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

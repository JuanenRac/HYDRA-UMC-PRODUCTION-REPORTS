# HTTP API Reference

Real, plain JSON/HTTP surface implemented in
[`src/hydra_umc_production_reports/api.py`](../src/hydra_umc_production_reports/api.py)
with Python's stdlib `http.server` (same convention as HYDRA-UMC-DATALAKE
and HYDRA-UMC-ANOMALY-DETECTOR). This service has **no store of its own**
- both report endpoints make a real live HTTP call to a running
HYDRA-UMC-DATALAKE instance on every request.

Start it with:

```bash
hydra-umc-production-reports --addr 0.0.0.0 --port <port> --datalake-url http://localhost:8095
```

Run `hydra-umc-production-reports --help` for the exact default port/flags.

All responses are `application/json` unless a route says otherwise. The API listens on
`127.0.0.1` by default. **Another address needs a token** (`--token`, or the
`PRODUCTION_REPORTS_TOKEN` environment variable, which keeps it out of the process list),
or `--allow-anonymous` for an open lab instance; the process refuses to start otherwise
(exit status `2`). With a token every route except `GET /health` demands
`Authorization: Bearer <token>` and answers `401` without it. Only
`--max-concurrent-reports` (default 4) reports are built at the same time, because each can
bring up to two queries of 200,000 points into memory: beyond that a request gets `503`.
Any failure that is not one of the errors below answers `500` with
`{"error": "internal error: <type>"}` instead of a closed connection.

Each declared query parameter may occur exactly once. Repeated values are a
`400` error rather than silently selecting one source, time range or metric.

---

## `GET /reports/oee`

Computes an OEE (Overall Equipment Effectiveness) report from `HYDRA-UMC-DATALAKE` production events over a time window - see [`oee.py`](../src/hydra_umc_production_reports/oee.py) for the real formulas.

**Query parameters**

| Param | Type | Meaning |
|---|---|---|
| `sourceId` | string, required | Which machine/robot to report on. |
| `start` / `end` | integer | Window `[start, end)`, epoch ms (`end` must be after `start`). A sample exactly at `end` belongs to the next window, so consecutive reports never count it twice. |
| `day` | `YYYY-MM-DD` | Instead of `start`/`end`: the window is that calendar day, or one shift of it when `shift` is given. |
| `shift` | integer | 0-based shift of `day`. Optional: `shiftLengthHours` (default `8`, and it must divide 24 h in whole minutes), `firstShiftStartHour` (default `0`) and `tzOffsetMinutes` (default `0`, a fixed offset east of UTC) define the schedule, so every report of a plant agrees on where a shift begins and ends. |
| `plannedTimeS` | float, finite and > 0 | Planned production time for the window, in seconds. Required with `start`/`end`; with `day` it defaults to the whole window. |
| `idealCycleTimeS` | float, finite and > 0 | The ideal (best-case) cycle time per unit, in seconds. |

A number that is `nan`, `inf` or not a number is a `400`; it is never passed on into the report.

**Response** - `200`:

```json
{
  "availability": 0.92,
  "performance": 0.87,
  "quality": 0.99,
  "oee": 0.792,
  "total_count": 480,
  "good_count": 475,
  "operating_time_s": 6624.0,
  "formula_version": "oee-v1",
  "input_fingerprint": "7c4cfff7c9c1afedba28126c1b9d7f520d438e3445f151a430657c0f6352e156",
  "unmatched_count": 0,
  "provenance": {"source_id": "robot-1", "kind": "production_event", "fields": ["good", "cycleTimeS"], "window_start_ms": 0, "window_end_ms": 6000, "points_used": 12, "generator_version": "0.1.4"},
  "inconsistent_input": false
}
```

- `availability` = Operating Time / Planned Production Time (0.0-1.0).
- `performance` = (Ideal Cycle Time x Total Count) / Operating Time, clamped to 0.0-1.0.
- `quality` = Good Count / Total Count (0.0-1.0).
- `oee` = `availability * performance * quality`.
- `total_count` / `good_count` - real event counts pulled from DATALAKE for the window.
- `operating_time_s` - real time the machine was actually producing, in seconds.
- `formula_version` - which real version of the OEE formula produced this report (`oee.FORMULA_VERSION`) - only changes if the formula itself changes.
- `input_fingerprint` - a real sha256 over the exact production events used, order-independent (events that share a timestamp are ordered by their other fields first) - two reports with the same fingerprint were computed from the same real input data.
- `unmatched_count` - readings that could not be used: a `good` with no `cycleTimeS` at the same timestamp (or the reverse), or a `good` that is not exactly `0` or `1`. Cycles that share a millisecond are paired in order.
- `inconsistent_input` - `true` when the cycles add up to more operating time than `plannedTimeS`, or `plannedTimeS` is longer than the window itself (overlapping cycles, several stations under one source id, cycles that began before the window). Availability is then capped at 100% and the product understates the real figure: do not take the numbers at face value.

**Errors**

| Status | Body | Meaning |
|---|---|---|
| 400 | `{"error": "missing required params: [...]"}` | One or more required params absent. |
| 400 | `{"error": "<message>"}` | Invalid param value (not a number, not finite, `end` not after `start`, a `shift` outside the day), or the computation itself rejected the input. |
| 401 | `{"error": "..."}` | A token is configured and the request did not carry it. |
| 502 | `{"error": "could not read from DATALAKE: <message>"}` | The live call to HYDRA-UMC-DATALAKE failed: unreachable, a timeout or a reset while reading the answer (retried first), non-2xx, or an answer that is not what DATALAKE sends. |
| 503 | `{"error": "too many reports ..."}` | Every report slot is busy; retry shortly. |

---

## `GET /reports/oee/export`

Same query parameters, same real query+compute as `GET /reports/oee` above, rendered as a real, reproducible CSV instead of JSON - calling this twice with the same parameters against the same DATALAKE history always returns byte-for-byte identical output. See [`export.py`](../src/hydra_umc_production_reports/export.py). `format=html` (default `csv`) returns the same data as a self-contained HTML page with an embedded SVG chart (no external script or style); any other `format` is a `400`.

**Response** - `200`, `Content-Type: text/csv` (or `text/html; charset=utf-8`):

```csv
sourceId,robot-1
startMs,0
endMs,6000
exportVersion,2
pointsUsed,12
generatorVersion,0.1.4

formula_version,input_fingerprint,availability,performance,quality,oee,total_count,good_count,operating_time_s,unmatched_count,inconsistent_input
oee-v1,7c4cfff7c9c1afedba28126c1b9d7f520d438e3445f151a430657c0f6352e156,0.900000,0.666667,0.833333,0.500000,6,5,18.000000,0,False
```

The leading rows record the real range (`sourceId`/`startMs`/`endMs`) and provenance this export was built from, so the file is self-describing even detached from the request that produced it; the discarded-readings count and the consistency flag are in the table, as in the JSON. A free-text cell that a spreadsheet would read as a formula (it starts, after any spaces, with `=`, `+`, `-`, `@`, `|`, `%` or a control character) gets a leading apostrophe. **Errors** - same as `/reports/oee` above (returned as JSON, not CSV).

---

## `GET /reports/availability`

Computes an availability report by finding gaps between consecutive sample timestamps in `HYDRA-UMC-DATALAKE` - see [`availability.py`](../src/hydra_umc_production_reports/availability.py).

**Query parameters**

| Param | Required | Type | Meaning |
|---|---|---|---|
| `sourceId` | yes | string | Which machine/robot to report on. |
| `kind` | yes | string | Sample kind to query in DATALAKE. |
| `field` | yes | string | Field name to query in DATALAKE. |
| `start` / `end` | yes (or `day`) | integer | Window `[start, end)`, epoch ms; `day` (and `shift`) work as for `/reports/oee`. |
| `expectedIntervalMs` | yes | float, finite and > 0 | Expected time between consecutive samples when the machine is healthy. |
| `gapFactor` | no | float, finite and >= 1 | A gap longer than `expectedIntervalMs * gapFactor` counts as downtime (default `3.0`). |

**Response** - `200`:

```json
{
  "window_start_ms": 1735689600000,
  "window_end_ms": 1735693200000,
  "downtime_periods": [
    {"start_ms": 1735690800000, "end_ms": 1735691100000}
  ],
  "downtime_ms": 300000,
  "availability": 0.917,
  "formula_version": "availability-v1",
  "input_fingerprint": "..."
}
```

- `downtime_periods` - every real gap detected between samples that exceeded the `gapFactor` threshold.
- `downtime_ms` - sum of every downtime period's duration.
- `availability` = 1 - (downtime_ms / window duration).
- `formula_version` / `input_fingerprint` - same real traceability as `/reports/oee` above, over the exact (window-filtered) sample timestamps used.

**Errors** - same pattern as `/reports/oee` above (`400` for missing/invalid params, `502` if DATALAKE is unreachable).

---

## `GET /reports/availability/export`

Same query parameters as `GET /reports/availability` above, rendered as a real, reproducible CSV - see `GET /reports/oee/export` above for the same guarantee and rationale.

---

## `GET /stats`

**Response** - `200 {"datalakeUrl": "<url>"}` - the DATALAKE base URL this instance was configured to read from.

---

## `GET /health`

**Response** - `200 {"status": "ok", "name": "HYDRA-UMC-PRODUCTION-REPORTS"}`. Liveness only; it never asks for the token and never waits for a report slot.

---

## Errors

Any other path returns `404 {"error": "not found"}`.

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

All responses are `application/json`. There is no authentication - internal/same-network use.

---

## `GET /reports/oee`

Computes an OEE (Overall Equipment Effectiveness) report from `HYDRA-UMC-DATALAKE` production events over a time window - see [`oee.py`](../src/hydra_umc_production_reports/oee.py) for the real formulas.

**Query parameters** (all required)

| Param | Type | Meaning |
|---|---|---|
| `sourceId` | string | Which machine/robot to report on. |
| `start` | integer | Window start, epoch ms. |
| `end` | integer | Window end, epoch ms. |
| `plannedTimeS` | float | Planned production time for the window, in seconds. |
| `idealCycleTimeS` | float | The ideal (best-case) cycle time per unit, in seconds. |

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
  "input_fingerprint": "7c4cfff7c9c1afedba28126c1b9d7f520d438e3445f151a430657c0f6352e156"
}
```

- `availability` = Operating Time / Planned Production Time (0.0-1.0).
- `performance` = (Ideal Cycle Time x Total Count) / Operating Time, clamped to 0.0-1.0.
- `quality` = Good Count / Total Count (0.0-1.0).
- `oee` = `availability * performance * quality`.
- `total_count` / `good_count` - real event counts pulled from DATALAKE for the window.
- `operating_time_s` - real time the machine was actually producing, in seconds.
- `formula_version` - which real version of the OEE formula produced this report (`oee.FORMULA_VERSION`) - only changes if the formula itself changes.
- `input_fingerprint` - a real sha256 over the exact production events used, order-independent - two reports with the same fingerprint were computed from the same real input data.

**Errors**

| Status | Body | Meaning |
|---|---|---|
| 400 | `{"error": "missing required params: [...]"}` | One or more required params absent. |
| 400 | `{"error": "<message>"}` | Invalid param value, or the computation itself rejected the input (e.g. zero planned time). |
| 502 | `{"error": "could not read from DATALAKE: <message>"}` | The live call to HYDRA-UMC-DATALAKE failed (unreachable, non-2xx, etc.). |

---

## `GET /reports/oee/export`

Same query parameters, same real query+compute as `GET /reports/oee` above, rendered as a real, reproducible CSV instead of JSON - calling this twice with the same parameters against the same DATALAKE history always returns byte-for-byte identical output. See [`export.py`](../src/hydra_umc_production_reports/export.py).

**Response** - `200`, `Content-Type: text/csv`:

```csv
sourceId,robot-1
startMs,0
endMs,6000

formula_version,input_fingerprint,availability,performance,quality,oee,total_count,good_count,operating_time_s
oee-v1,7c4cfff7c9c1afedba28126c1b9d7f520d438e3445f151a430657c0f6352e156,0.900000,0.666667,0.833333,0.500000,6,5,18.000000
```

The leading rows record the real range (`sourceId`/`startMs`/`endMs`) this export was built from, so the file is self-describing even detached from the request that produced it. **Errors** - same as `/reports/oee` above (returned as JSON, not CSV).

---

## `GET /reports/availability`

Computes an availability report by finding gaps between consecutive sample timestamps in `HYDRA-UMC-DATALAKE` - see [`availability.py`](../src/hydra_umc_production_reports/availability.py).

**Query parameters**

| Param | Required | Type | Meaning |
|---|---|---|---|
| `sourceId` | yes | string | Which machine/robot to report on. |
| `kind` | yes | string | Sample kind to query in DATALAKE. |
| `field` | yes | string | Field name to query in DATALAKE. |
| `start` | yes | integer | Window start, epoch ms. |
| `end` | yes | integer | Window end, epoch ms. |
| `expectedIntervalMs` | yes | float | Expected time between consecutive samples when the machine is healthy. |
| `gapFactor` | no | float | A gap longer than `expectedIntervalMs * gapFactor` counts as downtime (default `3.0`). |

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

## Errors

Any other path returns `404 {"error": "not found"}`.

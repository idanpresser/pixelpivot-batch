# PixelPivot API Reference

Complete REST reference for the PixelPivot Batch Engine backend (FastAPI). Every
request and response shown below was captured from a live server
(`scripts/verify_api_endpoints.py`).

For a narrative, step-by-step walkthrough see [USER_GUIDE.md](USER_GUIDE.md).
This document is the endpoint-by-endpoint spec.

---

## Base URL & conventions

| | |
|---|---|
| Base URL | `http://<host>:8000` |
| API prefix | `/api/v1` (the root `/` health check has no prefix) |
| Content type | `application/json` for all request bodies |
| Auth | **None.** See [Out of scope](#out-of-scope). |
| Interactive docs | `GET /docs` (Swagger UI), `GET /openapi.json` (schema) |

**Conventions used throughout:**

- `run_id` is an **integer**; `watcher_id` is a **hex string**.
- All directory paths are **server-side absolute paths**. The server reads its
  own filesystem — it does not receive uploaded files. Use absolute Windows
  paths (`F:/DEV/...` or `F:\\DEV\\...`); a POSIX-style `/f/DEV/...` resolves to
  a bogus path on Windows.
- `total_images` is the count of **conversions** (input images × tools ×
  formats), not the input file count. A batch of 2 images × 1 tool × 1 format
  reports `total_images: 2`; 10 images × 4 tools × 1 format reports `40`.
- Quality is **not** a request parameter — it is interpolated per image
  server-side. See [Out of scope](#out-of-scope).

### Batch status state machine

```
queued ──► running ──► completed
              │  ▲          
              │  └── paused (via POST /control {pause|resume})
              ├──────► failed
              └──────► cancelled   (via POST /control {stop})
```

`summary` (aggregated metrics) is populated only once `status` is `completed`.

---

## Endpoint index

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Liveness check |
| POST | `/api/v1/batch/start` | Queue a batch conversion |
| GET | `/api/v1/batch/status/{run_id}` | Status + live counters + summary |
| GET | `/api/v1/batch/{run_id}/progress` | Live in-flight progress + CPU/RAM sample |
| GET | `/api/v1/batch/{run_id}/errors` | Per-file error records |
| POST | `/api/v1/batch/{run_id}/control` | Pause / resume / stop a running batch |
| POST | `/api/v1/batch/{run_id}/restart` | Re-run a finished batch |
| GET | `/api/v1/batch/history` | All batch runs |
| POST | `/api/v1/hotfolder/register` | Watch a directory for auto-batches |
| GET | `/api/v1/hotfolder/list` | List active watchers |
| DELETE | `/api/v1/hotfolder/{watcher_id}` | Remove a watcher |

---

## Root

### `GET /`

Liveness check. No prefix, no parameters.

```bash
curl http://127.0.0.1:8000/
```
```powershell
Invoke-RestMethod http://127.0.0.1:8000/
```

**200**
```json
{ "message": "PixelPivot Batch Engine API is running" }
```

---

## Batch lifecycle

### `POST /api/v1/batch/start`

Queue a batch job. Returns immediately with a `run_id`; the conversion runs in a
background task. Poll `status` (or `progress`) to follow it.

**Request body** ([`BatchRequest`](#batchrequest)):

| Field | Type | Required | Default | Notes |
|---|---|---|---|---|
| `source_dir` | string | yes | — | Absolute path. Scanned for `.jpg .jpeg .png .webp .tiff .heic .heif .avif`. |
| `target_dir` | string | yes | — | Absolute path. Created if missing. |
| `target_format` | string[] | yes | — | ≥1 of `webp`, `avif`, `jxl`. |
| `tool` | string[] | yes | — | ≥1 of `magick`, `ffmpeg`, `vips`, `sharp`. |
| `category` | string[] | no | `["general"]` | Heuristic category. |
| `trigger_type` | string | no | `"manual"` | Free-form label. |

```bash
curl -X POST http://127.0.0.1:8000/api/v1/batch/start \
  -H "Content-Type: application/json" \
  -d '{
        "source_dir": "F:/DEV/pixelpivot_batch/e2e_src",
        "target_dir":  "F:/DEV/pixelpivot_batch/e2e_out",
        "target_format": ["webp"],
        "tool": ["vips"],
        "category": ["general"],
        "trigger_type": "manual"
      }'
```
```powershell
$body = @{
  source_dir    = "F:/DEV/pixelpivot_batch/e2e_src"
  target_dir    = "F:/DEV/pixelpivot_batch/e2e_out"
  target_format = @("webp")
  tool          = @("vips")
  category      = @("general")
  trigger_type  = "manual"
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/batch/start `
  -ContentType "application/json" -Body $body
```

**200**
```json
{ "run_id": 813, "status": "queued" }
```

| Status | When |
|---|---|
| 200 | Queued. |
| 422 | Body failed validation (empty list, bad format/tool, empty path). See [validation errors](#validation-errors). |
| 500 | DB error or path resolution failure (e.g. source does not exist). |

---

### `GET /api/v1/batch/status/{run_id}`

Status and metrics for one run. While the run is in flight, live counters
(`cells_done`, `cells_total`, `current_cell`, `ok`, `fail`) are folded in when
available. `summary` appears only after completion.

```bash
curl http://127.0.0.1:8000/api/v1/batch/status/813
```
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/status/813
```

**200 (completed)**
```json
{
  "run_id": 813,
  "status": "completed",
  "total_images": 2,
  "created_at": "2026-06-24T19:10:11",
  "completed_at": "2026-06-24T22:10:13.074269",
  "summary": {
    "batch_id": 813,
    "duration_ms": 1768.20,
    "cpu_avg_pct": 19.01,
    "cpu_peak_pct": 74.4,
    "ram_peak_mb": 88.02,
    "yield_mb_sec": 0.0099,
    "savings_pct": -81.93,
    "success_count": 2,
    "failure_count": 0
  }
}
```

> `savings_pct` can be **negative** when outputs are larger than inputs — common
> for tiny PNGs re-encoded to WebP, as in this verification run.

While `running`, the same call additionally returns `cells_done`,
`cells_total`, `current_cell`, `ok`, `fail`, and `summary` is `null`.

| Status | When |
|---|---|
| 200 | Run found. |
| 404 | `{"detail": "Batch run not found"}` |

---

### `GET /api/v1/batch/{run_id}/progress`

Live in-flight progress plus a fresh CPU/RAM sample. **Only valid while the run
is executing** — once it finishes the in-memory progress state is gone and this
returns 404. Use `status` for terminal runs.

```bash
curl http://127.0.0.1:8000/api/v1/batch/813/progress
```
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/813/progress
```

**200 (in-flight)**
```json
{
  "cells_done": 0,
  "cells_total": 1,
  "current_cell": null,
  "ok": 0,
  "fail": 0,
  "started_at": 1782328211.30,
  "cpu_pct": 17.6,
  "ram_mb": 26614.9
}
```

A "cell" is one `(format, tool)` work unit. `cpu_pct` is system-wide CPU; `ram_mb`
is total used system RAM at sample time.

| Status | When |
|---|---|
| 200 | Run is live. |
| 404 | `{"detail": "No live progress for that run"}` (finished, never started, or unknown). |

---

### `GET /api/v1/batch/{run_id}/errors`

Per-file error records for a run. Empty list when there were no failures.

```bash
curl http://127.0.0.1:8000/api/v1/batch/813/errors
```
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/813/errors
```

**200**
```json
[]
```

Each element (when present) carries the offending `path` and an `error` message.
Always 200; an unknown `run_id` simply yields `[]`.

---

### `POST /api/v1/batch/{run_id}/control`

Pause, resume, or stop a **running** batch. Only works while the run has an
active in-memory controller; finished runs return 404.

**Request body** ([`ControlRequest`](#controlrequest)):

| Field | Type | Values |
|---|---|---|
| `action` | string | `pause`, `resume`, `stop` |

`stop` cancels the run; the orchestrator marks it `cancelled` when the loop exits.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/batch/813/control \
  -H "Content-Type: application/json" -d '{"action": "pause"}'
```
```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/batch/813/control `
  -ContentType "application/json" -Body '{"action":"pause"}'
```

**200**
```json
{ "run_id": 813, "action": "pause" }
```

| Status | When |
|---|---|
| 200 | Action applied. |
| 404 | `{"detail": "No active run with that id"}` (run finished or never ran). |
| 422 | `action` not one of `pause`/`resume`/`stop`. |

---

### `POST /api/v1/batch/{run_id}/restart`

Re-run a finished batch using its **stored** source, target, formats, and tools.
Returns a new `run_id`. No request body.

> **Quirk:** `category` is not persisted on the run record, so a restart always
> re-runs with `category: ["general"]` and `trigger_type: "restart"`, regardless
> of the original category.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/batch/813/restart
```
```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/batch/813/restart
```

**200**
```json
{ "run_id": 814, "status": "queued" }
```

| Status | When |
|---|---|
| 200 | Queued as a new run. |
| 404 | `{"detail": "Batch run not found"}` |

---

### `GET /api/v1/batch/history`

All batch runs (newest first), each with its summary metrics flattened in.

```bash
curl http://127.0.0.1:8000/api/v1/batch/history
```
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/history
```

**200** (array; one element shown)
```json
[
  {
    "run_id": 814,
    "status": "completed",
    "target_format": "webp",
    "tool": "vips",
    "trigger_type": "restart",
    "total_images": 2,
    "created_at": "2026-06-24T19:10:13",
    "completed_at": "2026-06-24T22:10:13.678997",
    "duration_ms": 340.20,
    "success_count": 2,
    "failure_count": 0,
    "cpu_avg_pct": 7.28,
    "cpu_peak_pct": 25.0,
    "ram_peak_mb": 88.94,
    "yield_mb_sec": 0.051,
    "savings_pct": -81.93
  }
]
```

`target_format` and `tool` are comma-joined strings here (the stored form), not
arrays. There is no pagination — the full table is returned.

---

## Hot folder

A hot folder is a watched directory: a batch fires automatically ~5 seconds
after the last file is written into it, using the registered config.

### `POST /api/v1/hotfolder/register`

**Request body** ([`HotFolderRequest`](#hotfolderrequest)) — same fields as
`BatchRequest` minus `trigger_type`.

```bash
curl -X POST http://127.0.0.1:8000/api/v1/hotfolder/register \
  -H "Content-Type: application/json" \
  -d '{
        "source_dir": "F:/inbox",
        "target_dir":  "F:/out",
        "target_format": ["webp"],
        "tool": ["vips"],
        "category": ["general"]
      }'
```
```powershell
$body = @{
  source_dir = "F:/inbox"; target_dir = "F:/out"
  target_format = @("webp"); tool = @("vips"); category = @("general")
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/hotfolder/register `
  -ContentType "application/json" -Body $body
```

**200**
```json
{ "watcher_id": "d9ecb2e8161846d78a4064d4fbff919b", "status": "active" }
```

| Status | When |
|---|---|
| 200 | Watcher active. |
| 400 | Invalid config (e.g. bad directory). |
| 422 | Body failed schema validation. |
| 500 | System error registering the watcher. |

---

### `GET /api/v1/hotfolder/list`

```bash
curl http://127.0.0.1:8000/api/v1/hotfolder/list
```
```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/hotfolder/list
```

**200**
```json
[
  {
    "watcher_id": "d9ecb2e8161846d78a4064d4fbff919b",
    "source_dir": "F:\\inbox",
    "target_dir": "F:\\out",
    "target_format": ["webp"],
    "tool": ["vips"],
    "category": ["general"]
  }
]
```

---

### `DELETE /api/v1/hotfolder/{watcher_id}`

```bash
curl -X DELETE http://127.0.0.1:8000/api/v1/hotfolder/d9ecb2e8161846d78a4064d4fbff919b
```
```powershell
Invoke-RestMethod -Method Delete http://127.0.0.1:8000/api/v1/hotfolder/d9ecb2e8161846d78a4064d4fbff919b
```

**200**
```json
{ "status": "removed" }
```

| Status | When |
|---|---|
| 200 | Removed. |
| 404 | `{"detail": "Watcher not found"}` |

---

## Schemas

### BatchRequest
```jsonc
{
  "source_dir":    "string (abs path, required)",
  "target_dir":    "string (abs path, required)",
  "target_format": ["webp" | "avif" | "jxl"],   // ≥1
  "tool":          ["magick" | "ffmpeg" | "vips" | "sharp"],  // ≥1
  "category":      ["string"],                  // default ["general"]
  "trigger_type":  "string"                     // default "manual"
}
```

### HotFolderRequest
Identical to `BatchRequest` without `trigger_type`.

### ControlRequest
```jsonc
{ "action": "pause" | "resume" | "stop" }
```

### Tool / TargetFormat enums
- `Tool`: `magick`, `ffmpeg`, `vips`, `sharp`
- `TargetFormat`: `webp`, `avif`, `jxl`

### Validation errors

Schema violations return **422** with FastAPI's standard detail array:

```json
{
  "detail": [
    {
      "type": "too_short",
      "loc": ["body", "tool"],
      "msg": "List should have at least 1 item after validation, not 0",
      "input": [],
      "ctx": { "field_type": "List", "min_length": 1, "actual_length": 0 }
    }
  ]
}
```

Application-level errors (`HTTPException`) return the simpler
`{ "detail": "<message>" }` form with a 400/404/500 code.

---

## Worked example: start → watch → control → result

```bash
# 1. Start
RID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/batch/start \
  -H "Content-Type: application/json" \
  -d '{"source_dir":"F:/in","target_dir":"F:/out","target_format":["webp"],"tool":["vips"]}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['run_id'])")

# 2. Watch live progress
curl -s http://127.0.0.1:8000/api/v1/batch/$RID/progress

# 3. Pause / resume mid-run
curl -s -X POST http://127.0.0.1:8000/api/v1/batch/$RID/control -d '{"action":"pause"}'
curl -s -X POST http://127.0.0.1:8000/api/v1/batch/$RID/control -d '{"action":"resume"}'

# 4. Final status + summary
curl -s http://127.0.0.1:8000/api/v1/batch/status/$RID
```

```powershell
# 1. Start
$rid = (Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/batch/start `
  -ContentType "application/json" `
  -Body '{"source_dir":"F:/in","target_dir":"F:/out","target_format":["webp"],"tool":["vips"]}').run_id

# 2. Watch live progress
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/$rid/progress

# 3. Pause / resume mid-run
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/batch/$rid/control -Body '{"action":"pause"}'
Invoke-RestMethod -Method Post http://127.0.0.1:8000/api/v1/batch/$rid/control -Body '{"action":"resume"}'

# 4. Final status + summary
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/status/$rid
```

---

## Out of scope

What the API deliberately does **not** do:

- **No authentication or authorization.** Any client that can reach the port has
  full control. Bind to `127.0.0.1` or firewall it; do not expose it publicly.
- **No file upload.** The server converts files already present on **its own
  filesystem**, addressed by absolute path. Client and server must share a path
  namespace (same machine, or a mounted SMB/network share).
- **No synchronous conversion.** `batch/start` and `restart` return immediately
  with a `run_id`; you must poll `status`/`progress`. There is no blocking
  "convert and return the bytes" call.
- **No streaming / websockets / push.** Progress is poll-only.
- **No per-image result rows in the batch path.** Only the aggregated `summary`
  is exposed; there is no endpoint returning a list of individual conversions.
- **No per-request quality control.** Quality is interpolated per image from a
  fitted heuristic curve and clamped to each encoder's native range. You cannot
  pass a quality number to a batch. See the README quality-scale section.
- **No run or watcher persistence management.** No endpoint deletes a run, prunes
  history, or edits a registered hot folder — register a new one / delete and
  re-add.
- **No batch cancellation after completion.** `control` only affects runs with a
  live in-memory controller.
- **`restart` does not preserve `category`** (re-runs as `["general"]`).

---

## See also

- [USER_GUIDE.md](USER_GUIDE.md) — narrative walkthrough (tools, CLI, Sharp daemon)
- [ARCHITECTURE.md](ARCHITECTURE.md) — service topology and converter internals
- [README](../README.md) — install, configuration, quality scale
- `scripts/verify_api_endpoints.py` — regenerates the live responses in this doc

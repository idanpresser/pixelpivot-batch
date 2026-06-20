# PixelPivot TUI + Control Plane — Design

**Date:** 2026-06-20
**Status:** Approved design, pre-implementation
**Related beads:** `wct` (TUI), `4bg` (cli dispatcher) — existing; `ctrl`, `prog`, `sup` — to be created.

## 1. Purpose

Replace the removed Streamlit GUI with a terminal UI that is also a **control
plane** for the batch engine. The TUI owns the lifecycle of the backend: it
starts, stops, and restarts the FastAPI API and the sharp node daemon as child
processes, drives batch jobs (submit / pause / resume / stop / restart), shows
live telemetry, surfaces logs, and edits settings.

This is broader than the original `wct` bead (which scoped only submit + live
telemetry + history). The extra capabilities require three new backend pieces
that do not exist today.

## 2. Decisions (locked)

| Topic | Decision |
|---|---|
| TUI ↔ API relationship | **Supervisor**: TUI is the parent process; spawns uvicorn API as a child; talks to it over REST. |
| Worker model | **Single active batch**. stop = cancel current run; pause/resume = hold at matrix-cell boundaries; restart = re-run same config. No job queue, no concurrency. |
| Tool control | Health/version panel (all 4 tools) + start/stop/restart sharp daemon + per-tool enable/disable toggles. |
| Live telemetry | Poll a new `GET /batch/{id}/progress` (~1s) into a Rich Live table. |
| Log source | Capture child stdout/stderr pipes into a scrolling log panel. |
| Settings | Editable, persisted to `settings.toml`; live-apply safe keys, mark the rest restart-required. |
| Build structure | Backend-first layered beads (Approach A). |
| Sharp daemon launch | On-demand only (not auto-started with API). |
| settings.toml location | `PROJ_ROOT/data/settings.toml` (next to the DB). |

## 3. Architecture

The TUI is the single terminal owner. **prompt_toolkit** hosts the full-screen
application and handles all input. **Rich** is used only as a render-to-string
engine: tables and progress are rendered to ANSI text
(`rich.console.Console(file=StringIO, force_terminal=True)`) and embedded inside
prompt_toolkit windows. This avoids two libraries fighting for terminal control.

```
TUI (prompt_toolkit host, parent process)
 |- spawns child: uvicorn API  -- REST (httpx) --> 127.0.0.1:8000
 |- spawns child: node sharp_daemon.js :8765   (on demand)
 |- captures children stdout/stderr --> log ring buffer --> log panel
 |- ProcessSupervisor: start/stop/restart children, readiness probe
 |- APIClient: submit / progress / control / restart / history / errors
 |- toolcheck: structured health/version probe (shared with CLI)
```

Existing facts this builds on:
- `execute_batch` (`app/batch_api/orchestrator.py:229`) is a synchronous function
  run via FastAPI `BackgroundTasks` (threadpool). The matrix loop processes
  `(category, tool, format)` cells sequentially. There is currently **no** cancel
  hook, pause, or progress publication.
- Sharp is reached by socket connect only (`orchestrator.py` SharpConverter,
  port 8765); its process lifecycle is not managed. Script:
  `app/scripts/sharp_daemon.js`.
- `ffmpeg.exe` / `magick.exe` are subprocess-per-call (verify-only); `vips` is
  in-process pyvips (no lifecycle).
- Binary checks already exist as print-based functions in `app/cli.py`
  (`check_binary`, `check_pyvips`, `check_sharp_daemon`).
- REST client pattern: `app/web/batch_gui/api_client.py` (httpx, raise on
  non-200). The web GUI is removed from runtime; the TUI gets its own client.

## 4. Beads

### 4.1 `ctrl` — batch control (new)
Add pause/resume/cancel to the orchestrator at cell-boundary granularity.

- Per-run control object stored in `app.state.run_controls[run_id]`:
  a `threading.Event` for pause-gate (set = running, clear = paused) and a
  `cancel` boolean.
- `execute_batch` checks the control object at the top of each matrix-cell
  iteration: if cancelled, break and mark status `cancelled`; if paused, block on
  the Event until resumed or cancelled. (Granularity is one matrix cell — a cell
  already in `convert_batch` runs to completion; acceptable for single-batch
  pause/resume.)
- New endpoints:
  - `POST /batch/{id}/control` body `{ "action": "pause" | "resume" | "stop" }`.
    404 if run not active; returns new status.
  - `POST /batch/{id}/restart` — reads the original config from the `batch_runs`
    row, creates a new run, queues it; returns new `run_id`.
- New statuses: `paused`, `cancelled` (alongside `running`/`completed`/`failed`).
- Control object is created when the run is queued and removed when it finalizes.

### 4.2 `prog` — live progress (new)
- `execute_batch` publishes in-flight state to `app.state.progress[run_id]`:
  `{ cells_done, cells_total, current_cell, ok, fail, started_at }`, updated as
  each cell finishes (and `current_cell` set when a cell starts).
- `GET /batch/{id}/progress`: returns the published dict plus a live psutil
  sample `{ cpu_pct, ram_mb }` taken at request time. 404 if no live progress
  (run unknown or already finalized — caller falls back to `/batch/status`).
- In-memory only; no new DB rows (consistent with the batch-summary-only model).

### 4.3 `sup` — process supervision + tool checks (new)
- `app/tui/supervisor.py` `ProcessSupervisor`:
  - `start_api()` / `stop_api()` / `restart_api()` — spawn uvicorn (via
    `cli serve` once `4bg` lands, or `python -m uvicorn` until then) as a child;
    readiness probe by polling `GET /` until 200 or timeout.
  - `start_sharp()` / `stop_sharp()` / `restart_sharp()` — spawn
    `node app/scripts/sharp_daemon.js` child (on demand).
  - Pipe-capture: a reader thread per child drains stdout/stderr into a
    thread-safe ring buffer (bounded), tagged by source (`API` / `SHARP`).
  - `status()` — per-child running/stopped via `proc.poll()`.
- `app/core/toolcheck.py` — refactor `cli.py check_*` into pure functions
  returning structured results `{name, ok, version, detail}` (no prints).
  `cli.py` and the TUI Tools screen both consume it.

### 4.4 `wct` — TUI app (exists; depends on ctrl, prog, sup)
- `app/tui/` package: `app.py` (prompt_toolkit Application + layout + keybindings),
  `api_client.py` (httpx client incl. new progress/control/restart calls),
  `screens/` (one module per screen), `state.py` (UI state + reducers),
  `settings.py` (load/save `settings.toml`, precedence + live-apply classification),
  `render.py` (Rich→ANSI helpers).
- Screens: Submit, Telemetry, History, Tools, Settings (section 5).
- Global log panel + status bar.

### 4.5 `4bg` — cli dispatcher (exists; depends on wct)
- `cli.py` grows subcommands: `serve` (run uvicorn), `convert` (headless batch /
  existing validation), `tui` (launch ProcessSupervisor + TUI app). Preserve the
  current `--source/--target/--dry-run` validation behavior under the relevant
  subcommand.

## 5. TUI screens

Tabbed full-screen layout; bottom split = log panel; top/bottom = status bar.

1. **Submit** — source/target path inputs with path completion + pre-submit
   validation (existing dry-run-style checks via `toolcheck` + path checks);
   tool checkboxes (gated by health + enabled toggles); format checkboxes
   (webp/avif/jxl); category input (default `general`); `[Start]` →
   `POST /batch/start`, then switch to Telemetry.
2. **Telemetry** — Rich Live table polling `/batch/{id}/progress` ~1s:
   cell x/total, current cell, ok/fail counts, cpu%, ram MB. Control bar:
   `p` pause, `r` resume, `s` stop, `R` restart → control/restart endpoints.
   On finalize, render `/batch/status` summary.
3. **History** — `/batch/history` table; select a run to view
   `/batch/{id}/errors`.
4. **Tools** — health/version board (`toolcheck`); sharp daemon
   start/stop/restart buttons; per-tool enable/disable toggles (persisted in
   settings, feed the Submit picker; respect circuit-breaker `is_broken`).
5. **Settings** — editable form bound to `settings.toml`; live-apply safe keys
   immediately, tag restart-required keys.

## 6. Settings (`settings.toml`)

Location: `PROJ_ROOT/data/settings.toml`. Loader precedence: file → env → defaults.

- **Live-apply:** default tool / format / quality, allowed root, enabled-tools list.
- **Restart-required:** api host/port, db path, sharp port, tool binary paths,
  max_workers ceiling (`PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS`).

Indicative shape:
```toml
[api]
host = "127.0.0.1"
port = 8000
[paths]
db = "./data/pixelpivot.db"
sharp_port = 8765
[tools]
ffmpeg = ""        # blank = use bundled/PATH
magick = ""
sharp_script = "app/scripts/sharp_daemon.js"
enabled = ["magick", "ffmpeg", "vips", "sharp"]
[security]
allowed_root = ""
[limits]
max_workers = 0    # 0 = auto
[batch]
default_tool = "ffmpeg"
default_format = "avif"
default_quality = 90
```

## 7. Data flow

1. `cli tui` → `ProcessSupervisor.start_api()` → readiness probe → construct
   `APIClient`.
2. Submit → `client.start_batch(...)` → `run_id` → Telemetry screen → poll
   `/batch/{id}/progress` → control actions as keyed.
3. Logs: supervisor reader threads append to ring buffer; TUI log panel refreshes
   on the render tick.
4. Tools screen: `toolcheck` probes on open; sharp buttons call supervisor.

## 8. Error handling

- Child crash → supervisor `poll()` detects, status bar shows DOWN, exit code in
  log; user restarts from Tools (or the status bar action).
- Poll failures (API briefly down) → transient notice in panel; keep retrying.
- API path errors (400/422 from Pydantic validators) → surface inline on Submit.
- control/restart on a non-active run → 404; show message, no crash.
- Sharp absent / node missing → start fails gracefully with a logged reason;
  batches that don't use sharp are unaffected (on-demand).

## 9. Testing

- **ctrl:** inject fast fake converters; assert the matrix loop exits at a cell
  boundary on stop, blocks on the pause Event, and resumes; assert
  control/restart endpoint status codes and status transitions.
- **prog:** assert `/batch/{id}/progress` returns published state merged with a
  monkeypatched psutil sample; 404 when no live progress.
- **sup:** spawn a dummy child (`python -c ...`); assert start/stop/restart and
  that pipe output reaches the ring buffer; `toolcheck` against fake present /
  missing paths.
- **wct:** headless unit tests for pure logic (state reducers, settings
  load/save + precedence + live-apply classification, batch-request payload
  building); prompt_toolkit smoke test via its `create_pipe_input` / dummy
  output. No emoji/icons in tests (Python console encoding rule).

## 10. Out of scope (YAGNI)

- Job queue / multiple concurrent batches / per-tool concurrency lanes.
- SSE/WebSocket telemetry streaming (polling is sufficient at single-batch scale).
- Mid-cell interruption (pause/stop act at cell boundaries only).
- Remote/multi-host operation; the TUI and API are co-located on one machine.
- Persisting live progress to the DB.

## 11. Build order

`ctrl` and `prog` (backend, independent, parallelizable) → `sup` (supervisor +
toolcheck) → `wct` (TUI consumes all three) → `4bg` (cli dispatcher wiring).

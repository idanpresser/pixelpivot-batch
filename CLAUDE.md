# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PixelPivot Batch Engine — a decoupled microservice system for high-throughput image conversion. A headless **FastAPI backend** orchestrates batch jobs; a standalone **Streamlit frontend** communicates with it exclusively via REST.

## Commands

### Running Tests
```bash
pytest                            # All tests (verbose, short tracebacks per pytest.ini)
pytest tests/test_base_converter.py  # Single file
pytest -k "test_interpolator"    # Run by keyword

# Run tests with Postgres (requires docker service test-postgres running):
docker compose up -d test-postgres
$env:PIXELPIVOT_DB_URL="postgresql+psycopg://pixelpivot:pixelpivot@localhost:5433/pixelpivot_test"
pytest
```

### Starting Services (Docker)
```bash
docker-compose up --build        # Build and start all services
```

### Running Locally (without Docker)
```bash
# Optional: Set PIXELPIVOT_DB_PATH (defaults to ./data/pixelpivot.db)
# $env:PIXELPIVOT_DB_PATH = "./my_data/pixelpivot.db"

uvicorn app.batch_api.main:app --host 0.0.0.0 --port 8000  # API
streamlit run -m app.web.batch_gui.main --server.port 8503  # GUI
```
Set `BATCH_API_URL=http://localhost:8000/api/v1` for the GUI to find the API.

## Architecture

### Service Topology
```
Streamlit GUI (port 8503)
    └── REST → FastAPI Backend (port 8000)
                    ├── BatchOrchestrator  (app/batch_api/orchestrator.py)
                    ├── HotFolderManager   (app/batch_api/hot_folder.py)
                    └── Converters Layer   (app/core/converters/)
                                    └── SQLite DB (WAL Mode)
```

### Converters Layer (`app/core/converters/`)
`BaseConverter` (ABC) defines `convert()` and `convert_batch()`. The default `convert_batch()` uses a `ThreadPoolExecutor` calling `convert()` per file. Subclasses override it for efficiency:
- **`MagickConverter`** — groups images by quality, calls native `mogrify` for true batch processing; falls back to individual `magick` per-file on failure.
- **`FFmpegConverter`** — subprocess only. `convert()` spawns one `ffmpeg.exe` per file. `convert_batch()` uses a hybrid native-batching path: groups by `(format, quality)`, sub-groups by exact `(W, H)`. Uniform-size sub-groups of ≥ `IMAGE2_THRESHOLD` use the `image2` demuxer with hardlinked staging; smaller or mixed sub-groups use multi-input/multi-output chunks bounded by `FFMPEG_BATCH_MAX_FILES` and `FFMPEG_BATCH_MAX_CMDLINE_BYTES`. Per-file `convert()` is the final fallback. See `app/core/converters/ffmpeg_batch_helpers.py` for the pure helpers. Measured speedup vs per-file: ~4.9x on uniform-size batches, ~1.9x on mixed real-world batches.
- **`VipsConverter`** — pyvips in-process.
- **`SharpConverter`** — persistent socket connection to `sharp_daemon.js`.

`_run_subprocess` and `_run_library` in `BaseConverter` handle telemetry capture and circuit-breaker logic (fatal error detection) so individual converters don't need to.

### Heuristic Quality System
`HeuristicInterpolator` (`app/core/heuristic_interpolator.py`) loads `app/core/heuristic_table.json` (schema `category → format → tool → {a, b, n, mp_min, mp_max}`, table `version` 2.0.0) and evaluates a fitted log-linear curve `quality = a + b·log10(megapixels)` at the image's exact megapixel count. The result is clamped to the curve's observed `[mp_min, mp_max]` (no extrapolation) and to the encoder's valid native range (`config.quality_range_for`). The canonical generator `app/core/heuristic.generate_heuristic_table` fits one curve per `(category, format, tool)` over raw per-image `(megapixels, quality)` samples via least squares (`fit_log_linear`), dropping any cell with fewer than `config.HEURISTIC_MIN_SAMPLES` points (the interpolator then falls back via `config.default_quality_for`). `tools/generate_heuristic_data.generate_cli` is a thin wrapper over that single generator. The shipped `heuristic_table.json` ships without priors (just the version); regenerate it from a populated analytics DB.

**Continuous learning (2026-07-08):** A sidecar layer (`app/core/adjustment.py`, `heuristic_adjust.json`) enables live adaptation without offline calibration. Two mechanisms:
1. **Steady-state verification:** 1% of batch outputs are SSIM-scored vs originals; error is fed to a leaky-integrator that nudges a per-cell offset (`offset += k·err·sign − λ·offset`). Offset is applied on top of the canonical curve during interpolation (`q = curve(mp) + offset[cell]`). This allows the heuristic to drift with encoder changes or content mix shifts. Gated by `PIXELPIVOT_ONLINE_LEARNING`.
2. **Cold-start bootstrap:** New categories (missing `heuristic_table[cat][fmt][tool]`) trigger inline calibration of the first ~100 images; winning encodes reused as outputs. The SSIM-measured samples fit a fresh canonical curve, which is reloaded. Remaining images convert normally with the warm curve. Gated by `PIXELPIVOT_BOOTSTRAP_ENABLED`.

Critical fix (circular dependency): `generate_heuristic_table` now fits only from `WHERE calib_method='ssim'` (calibration/bootstrap samples), not from live-batch conversions. Live batches write predicted quality without the flag so they don't corrupt the canonical fit. The nudge sidecar handles live drift instead. See `docs/superpowers/specs/2026-07-08-continuous-learning-design.md` for full design.

### Database
SQLite 3 with WAL mode enabled. Connection management in `app/core/db/connection.py`. Schema initialized via `app/core/db/schema.py` — call `init_db()` on startup.

Key tables:
- `batch_runs` — one row per batch job (`status`: `running`/`completed`/`failed`)
- `batch_summary` — one aggregated metrics row per `batch_runs` row
- `images`, `conversions`, `metrics`, `quality_priors` — analytics schema

### Environment Variables
| Variable | Where used |
|---|---|
| `PIXELPIVOT_DB_PATH` | All services (path to SQLite file) |
| `BATCH_API_URL` | Streamlit GUI → FastAPI address |

## Key Design Constraints

- `quality` in `convert()` is `Union[int, float]` and is **tool-and-format-native** — there is no single normalized scale. Most paths use a 0–100 "higher is better" quality (magick/vips/sharp all formats, ffmpeg webp, ffmpeg_nvenc avif), but `ffmpeg` avif takes a libaom-av1 **CRF** (0–63, *lower* is better, shipped ~28). JXL is passed as a 0–100 quality too (shipped ~90) and each converter maps it internally to a Butteraugli **distance** (0.0–15.0, lower is better) via `utils.quality_to_jxl_distance`. Because the scalar is non-uniform, fallback defaults must be resolved per `(tool, format)` — see `config.default_quality_for` / `DEFAULT_QUALITY_BY_TOOL_FORMAT`. Never cast to `int` inside converter implementations (interpolated quality is fractional; the jxl distance mapping needs the float).
- `convert_batch()` returns `{"success_count", "failure_count", "duration_ms", "errors"}` — not a list of individual results.
- The batch orchestrator writes to `batch_summary` only after the full batch completes; there is no per-image DB row in the batch path.
- Hot folder handler debounces 5 seconds after the last file write before firing a batch.
- Global constants (timeouts, thresholds, meta-score weights) live in `app/core/config.py`.

## Known Issues & Gotchas

**Status**: Identified in E12 runtime audit (2026-07-06). See individual beads for detailed reproduction, acceptance criteria, and workarounds.

### Concurrency & Breaker State

**Issue: Torn breaker counter under ThreadPoolExecutor batch** (`bd-qk1.1`)
- `BaseConverter._mark_failure()` and `_reset_failures()` do read-modify-write on `state["consecutive_failures"]` outside the lock.
- Under concurrent batch workloads (multiple workers probing same converter), counter mutations race → lost updates.
- **Impact**: Converter breaker trips/resets unpredictably; healthy converters may be marked broken or vice versa.
- **Current state**: `_bypass_breaker` mask during batch suppresses the broken state, but counter corruption leaks out after.

**Issue: Cross-run breaker interference via global state reset** (`bd-qk1.3`)
- `_reset_failures()` wipes the global `None`-keyed breaker state whenever an active run_id is set.
- Concurrent batches (run A + run B) share breaker state via the `None`-priority getters.
- **Impact**: Run A's failures can clear the breaker that run B reads; runs are not isolated.
- **Fixed (2026-07-09)**: `consecutive_failures`/`is_broken`/`broken_since` getters now read only the active run's state (no global-`None` priority peek), and `_reset_failures()` resets only the active run's state (no global-`None` wipe). Each run_id is fully isolated via its own `_breaker_states` key; `run_id=None` remains the default context for non-batch `convert()` calls. Regression tests: `tests/core/test_circuit_breaker_isolation.py::test_isolated_run_ignores_global_none_breaker` and `::test_reset_in_run_does_not_wipe_other_run_state`.

**Issue: Magick recover chunk asymmetric breaker save/restore** (`bd-qk1.4`)
- `_recover_chunk_per_file()` saves breaker fields via getters (global-`None` priority) but restores via setters (write run_id state).
- Read side and write side target different dict keys → breaker state corruption under concurrent activity.
- **Dependency**: Blocked on `bd-qk1.1`.

**Issue: CALIBRATION_ENABLED global flag set by worker, never reset** (`bd-qk1.2`)
- `queue_manager.py` sets `config.CALIBRATION_ENABLED = True` from a worker thread during calibration.
- Flag is never restored to its prior value.
- **Impact**: Once any calibration run executes, all subsequent *normal* batch runs silently write calibration/analytics rows (the record_* gate stays open).
- **Fixed (2026-07-09)**: `queue_manager._worker_loop` now saves the prior `CALIBRATION_ENABLED` value, sets it `True` only around the `run_calibration` call, and restores it in a `finally`. The write-gate is scoped to the calibration run; subsequent normal batches read the prior value. Regression test: `tests/batch_api/test_calibration_flag_scope.py`.

### Converter Batch Lifecycle

**Issue: Magick no-suffix path counts success without verifying output** (`bd-qk1.5`)
- When `suffix=""`, mogrify success (rc==0) increments `success_count` for every input *without* checking the output file exists.
- mogrify can return 0 while silently skipping an unreadable file → phantom success, no bytes written.
- Suffix path (with rename) does verify; no-suffix path does not.
- **Impact**: Data integrity; batch reports success for files that were never converted.

**Issue: FFmpegProcess lifecycle nits** (`bd-qk1.12`)
- Supervisor loop ends with unbounded `proc.wait()` after `kill_process_tree()` — can hang if reaping fails.
- Exception between `spawn()` and `run()/unregister()` orphans Popen in registry until shutdown `terminate_all()`.
- Reader threads joined with `timeout=1.0`; slow `on_progress` callback leaks daemon threads (cosmetic, bounded).
- Hot-folder handler: if `run_coroutine_threadsafe()` raises synchronously (loop stopped during shutdown), handler stays wedged.

### Hot Folder Semantics

**Issue: Trigger failure leaves orphaned 'running' DB row** (`bd-qk1.6`)
- `create_run()` inserts with default `status='running'` before `execute_batch()` is dispatched.
- Broad `except Exception: log.error()` swallows dispatch failures; row stuck in `running` forever.
- Restart's `reap_stale_running()` transitions it to `interrupted` (terminal), not `failed` or re-queued.
- **Impact**: Orphaned row; error not surfaced to any caller.
- **Workaround**: Check DB for stale `running` rows on a long-lived hot folder.

**Issue: Readiness passes on stalled write + TOCTOU partial conversion** (`bd-qk1.10`)
- Readiness check: "stable" = two consecutive equal-size polls. A paused network copy (write stall) reads identical size twice → declared ready → mid-write conversion.
- TOCTOU: readiness scans dir, then glob re-scans; file created in that window bypasses readiness checks.
- **Impact**: Hot-folder can convert partially-written files on networked/slow volumes.
- **Workaround**: Avoid hot-folder on NFS/SMB with high latency. Use exclusive-open probing (pending fix).

**Issue: processed_files set grows unbounded (memory leak)** (`bd-qk1.11`)
- Every processed `(path, mtime, size)` key retained forever on a long-lived watcher.
- **Impact**: Memory growth over hours/days of hot-folder operation.
- **Workaround**: Restart the API process periodically if hot-folder is long-lived.

### Shutdown & Crash Recovery

**Issue: In-flight batch job lost on crash-restart** (`bd-qk1.8`)
- `claim_next_queued()` commits `status='running'` before worker adds to `_running_jobs`.
- On process crash, `reap_stale_running()` sets those rows to `interrupted` (terminal), **not** `queued`.
- **Impact**: Active batch at crash-time is abandoned; queued jobs survive but the running one is lost.
- **Product decision needed**: Is no-resume intentional ("don't auto-resume partial work") or a bug?
- **Current state**: Documented as a limitation until decision.

**Issue: Cancellation granularity: converter chunks ignore ctrl.cancel** (`bd-qk1.9`)
- `RunControl.cancel()` is checked only at matrix-cell boundaries.
- `FFmpegConverter.convert_batch()` (image2/multimap chunk) and `MagickConverter.convert_batch()` (mogrify chunk) have **no internal cancel check**.
- One large chunk runs to its full scaled timeout, ignoring shutdown signal.
- SIGTERM grace-join times out; chunk killed mid-way (partial outputs).
- **Impact**: Graceful shutdown does not gracefully interrupt a large batch chunk.

### Resource Management

**Issue: RAM chunk-sizing model omits encoder working set (OOM risk)** (`bd-qk1.7`)
- `chunk_sizing.py` assumes 4 B/px (raw RGBA only).
- `base.py` worker cap assumes 12 B/px (3× encoder intermediate buffers).
- Image2/multimap chunk sizing omits the encoder working set → large uniform batches size chunks ~3× too big.
- **Impact**: Potential OOM past `CHUNK_RAM_BUDGET_FRACTION` on large uniform batches.
- **Nuance**: image2 demuxer decodes sequentially (peak ~1 frame not N), so the model is conservative there. Real gap is per-frame 4 vs 12 and the multimap path (opens N inputs).
- **Also**: `base.py:636-662` samples available RAM point-in-time; stale under concurrent runs/probes.

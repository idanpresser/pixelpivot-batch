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


<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

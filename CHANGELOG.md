# Changelog

All notable changes to PixelPivot Batch Engine will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Interactive TUI**: Built a terminal user interface using `prompt_toolkit` and `Rich` for configuring/submitting batches, live telemetry progress bars, settings forms, and managing tool daemons.
- **SQLite-backed Job Queue**: Introduced a concurrency-capped serial/priority queue manager for submitting and processing batches asynchronously.
- **cavif AVIF Encoder Support**: Integrated custom `CavifConverter` for native AVIF conversion including configurations and packaging scripts.
- **Dual-Dialect Database Support**: Migrated database structures and repositories to SQLAlchemy Core, enabling simultaneous support for SQLite and PostgreSQL.
- **Prometheus Metrics & Otel Tracing**: Plumbed Prometheus `/metrics` endpoints and OpenTelemetry context propagation to track in-flight runs and sample worker execution.
- **Process Supervision**: Structured checks and automatic daemon lifecycle management for Node.js `sharp` daemon.
- **Windows Service Wrapping**: Added `install_windows_service.ps1` and NSSM documentation to run the engine as a persistent Windows Service.
- **WSL / Docker Offline Distribution**: Added scripts (`scripts/manifest.ps1`, `scripts/build_bundle.ps1`, `scripts/smoke_test.ps1`) and WSL image exporters to compile minimal Alpine WSL-based distribution bundles under 10GB for air-gapped environments.
- **Dual-Dialect CI Pipeline**: Wired a pytest CI runner matrix testing both SQLite and Postgres.

### Changed
- **Runtime Surface Decoupling**: Decoupled the Streamlit GUI from core conversion utilities, disabling default phone-home telemetry and pruning development dependencies.
- **Orchestrator SRP Refactoring**: Decomposed the 250-line `execute_batch` god method and decoupled the converter registry for better Single Responsibility and Open-Closed Principle compliance.
- **Universal Connection Facade**: Replaced raw sqlite3 connections with a thread-safe connection wrapper supporting transaction savepoints.

### Fixed
- **Circuit Breaker Concurrency**: Replaced `threading.Lock` with `threading.RLock` in `BaseConverter` and synchronized all reads and mutations of circuit breaker state (e.g. `consecutive_failures`, `is_broken`, `broken_since`) under `_breaker_lock`. This prevents race conditions and torn failure counts during concurrent worker execution in the `ThreadPoolExecutor` batch path.
- **Circuit Breaker Granularity**: Refactored the circuit breaker from tool-level to file-level to prevent single poison-pill files from blacking out whole tools.
- **Calibration OOM Safety**: Diagnosed and fixed ProcessPoolExecutor crashes by pruning unpicklable closures and memory-limiting calibration frames.
- **API Security Hardening**: Restricted REST API binding to local interfaces and enforced token-based authentication.
- **Subprocess RAM Capping**: Extended memory-aware worker throttling to all native subprocess execution pipelines.
- **Telemetry Reliability**: Fixed sample skips on quick-ticks and ensured native Mogrify batches record non-zero telemetry samples.
- **SQLite Lock-Retry Cohesion**: Consolidated duplicate lock-handling routines to prevent database locking/timeout errors.

### Removed
- **GPU / NVENC backend.** `FFmpegNvencConverter`, the `Tool.ffmpeg_nvenc`
  enum value, `app/core/gpu_utils.py`, all `use_gpu` kwargs on converter
  `convert()` signatures, the NVML telemetry sampling path, the
  `gpu_peak_pct` / `vram_peak_mb` columns in `batch_summary`, and the
  `gpu_pct` / `vram_mb` columns in `batch_telemetry` are gone. The
  decision was driven by a 2026-05-29 matrix E2E run that surfaced two
  independent issues: 488/500 nvenc conversions failed with `av1_nvenc:
  No capable devices found` because the converter inherited the base
  48-worker concurrency cap that exceeds consumer-GPU NVENC session
  limits, and `gpu_peak_pct` came back 0.0 across every tool because the
  NVML sampling never actually fired. Combined with the target deployment
  server being CPU-only, full removal was preferred over per-tool
  concurrency tuning. **Breaking:** callers passing `tool=ffmpeg_nvenc`
  to the batch API or calling `converter.convert(..., use_gpu=True)`
  will get an error. The schema migration is idempotent and runs on
  `init_db()` -- existing DBs have the columns dropped on first bootstrap
  after upgrade; fresh DBs never get them.

## [0.1.0] - 2026-05-28

Initial open-source release. Baselines the FastAPI orchestrator, the four
converter backends (ImageMagick, FFmpeg, libvips, Sharp), the heuristic
quality system, and the Streamlit GUI client.

### Added
- FastAPI batch orchestrator (`app/batch_api/`) coordinating per-format
  converter selection and writing aggregated metrics to `batch_summary`.
- Hot-folder watcher with 5-second debounce on the last file write.
- `BaseConverter` abstraction with four production backends:
  - `MagickConverter` — native `mogrify` quality-grouped batches with
    per-file `magick` fallback.
  - `FFmpegConverter` — hybrid `image2` demuxer (hardlinked staging, for
    uniform-size sub-groups) + multi-input/multi-output chunks bounded by
    `FFMPEG_BATCH_MAX_FILES` and `FFMPEG_BATCH_MAX_CMDLINE_BYTES`.
  - `VipsConverter` — in-process `pyvips`.
  - `SharpConverter` — persistent socket connection to `sharp_daemon.js`.
- `FFmpegNvencConverter` for NVIDIA hardware-accelerated AVIF encoding.
- Heuristic quality interpolator with fitted log-linear curves
  (`quality = a + b·log10(megapixels)`) per `(category, format, tool)` cell.
- SQLite analytics schema (WAL mode, `PRAGMA busy_timeout = 5000`) with a
  repository layer covering `batch_runs`, `batch_summary`, `images`,
  `conversions`, `metrics`, and `quality_priors`.
- Streamlit GUI talking to the API exclusively via REST
  (`BATCH_API_URL` env var).
- Docker Compose topology and Windows Sandbox configuration
  (`PixelPivot.wsb`) for air-gap deployment.
- Pytest hardening suites covering subprocess reliability, memory bounding,
  database concurrency, and circuit-breaker recovery.

### Hardening highlights (rolled into 0.1.0)
- Lazy `libvips` initialization to prevent crashes during test collection.
- Windows-specific `os.add_dll_directory` discovery for libvips DLLs.
- `CREATE_NO_WINDOW` flags on every native subprocess (ffmpeg, magick, sharp).
- Self-healing circuit breaker with a 30-second cooldown.
- Bounded telemetry sampling queue (`maxsize=2000`) and FFmpeg progress
  samples (`maxlen=1000`).
- Sharp daemon `atexit` hooks and dynamic-port retry loops.
- JXL precision preserved as a float Butteraugli distance.
- `pack_chunks` to respect Windows command-line length limits for
  ImageMagick `mogrify`.
- Portable binary resolution that prefers the vendored `bin/` tools.

[Unreleased]: ../../compare/v0.1.0...HEAD
[0.1.0]: ../../releases/tag/v0.1.0

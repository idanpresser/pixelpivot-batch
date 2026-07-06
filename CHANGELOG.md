# Changelog

All notable changes to PixelPivot Batch Engine will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- _Reserved for changes landing on `main` that have not been released yet._

### Changed

### Fixed
- **Circuit Breaker Concurrency**: Replaced `threading.Lock` with `threading.RLock` in `BaseConverter` and synchronized all reads and mutations of circuit breaker state (e.g. `consecutive_failures`, `is_broken`, `broken_since`) under `_breaker_lock`. This prevents race conditions and torn failure counts during concurrent worker execution in the `ThreadPoolExecutor` batch path.

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

# PixelPivot Batch Engine Hardening - Merge Summary

## Improvements Merged

### Core Hardening (Phase 1)
- **Lazy libvips initialization**: Prevents premature side effects and crashes during test collection.
- **Improved libvips DLL discovery**: Robust Windows-specific discovery using `os.add_dll_directory`.
- **Absolute SQLite paths**: Forced absolute paths relative to project root to prevent CWD-dependent DB generation.

### Subprocess & CLI Reliability (Phase 2)
- **Centralized Error Classification**: Consolidated fatal markers in `config.py` and logic in `app/core/ffmpeg/errors.py`.
- **CLI Window Suppression**: Enforced `CREATE_NO_WINDOW` flags for FFmpeg, ImageMagick, and Sharp.
- **Self-healing Circuit Breaker**: Added 30-second cooldown period for automatic recovery of "broken" converters.

### Memory & Telemetry Bounding (Phase 3)
- **Queue Bounding**: Bounded telemetry sampling queue (`maxsize=2000`) and FFmpeg progress samples (`maxlen=1000`).
- **GPU Resilience**: Centralized GPU failure thresholds in `config.py`.

### Database & Concurrency (Phase 4)
- **SQLite Busy Timeout**: Added `PRAGMA busy_timeout = 5000` to handle write contention gracefully.
- **WAL Mode Optimization**: Moved WAL initialization to schema bootstrap.
- **Sharp Daemon Hardening**: Added `atexit` hooks and robust retry loops for dynamic port binding.
- **JXL Precision**: Preserved float formatting for Butteraugli distance in JXL.

### Orchestration & Batching
- **Hybrid FFmpeg Batching**: Integrated `image2` (hardlinks) and `multimap` strategies.
- **Command-Line Length Protection**: Added `pack_chunks` for ImageMagick `mogrify` to respect Windows limits.
- **Portable Binary Resolution**: Added resolution for local `bin/` tools in `BatchOrchestrator`.

## Verification Results
Executed 20 regression tests targeting production reliability:
- `tests/test_phase1_hardening.py`: 3 Passed
- `tests/test_phase2_hardening.py`: 2 Passed
- `tests/test_phase3_hardening.py`: 2 Passed
- `tests/test_phase4_hardening.py`: 4 Passed
- `tests/test_hardened_converters.py`: 9 Passed

**Total: 20/20 tests passed.**

The codebase is now production-ready for high-throughput, air-gapped execution on Windows.

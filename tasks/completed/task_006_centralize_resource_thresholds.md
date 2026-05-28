# Task 006 — Centralize resource thresholds; recheck disk mid-run; stop swallowing mkdir errors

**Severity:** MEDIUM (resilience; config hygiene)
**Phase:** III — "Disk Divorce" scenario
**Confidence:** Code read

## Problem

The preflight block (`orchestrator.py:71-85`):

```python
if vm.available < 50 * 1024 * 1024:                 # magic number
    raise ValueError(...)
target_path.mkdir(parents=True, exist_ok=True)      # OSError here is swallowed below
_, _, free = shutil.disk_usage(str(target_path))
if free < 50 * 1024 * 1024:                          # magic number (repeated)
    raise ValueError("Insufficient disk space ...")
except Exception as pe:
    if isinstance(pe, ValueError): raise pe
    log.warning(f"Pre-flight resource check failed: {pe}")   # mkdir failure ends up here
```

Three issues:

1. **Magic numbers inline.** `50 * 1024 * 1024` appears twice and is not in `config.py`.
   This violates the project's config-centralization rule (tunables belong in
   `app/core/config.py`).
2. **Preflight runs once, before a 90-cell loop.** A long matrix run can fill the disk
   mid-flight; there is no re-check between cells, so later conversions hit raw OS I/O
   errors (caught per-file, but noisy and late).
3. **mkdir failure is swallowed.** A genuinely bad `target_dir` raises `OSError` from
   `mkdir`, which is NOT a `ValueError`, so it is logged as a warning and execution
   continues — failing confusingly later instead of aborting cleanly up front.

## Fix

Add to `config.py`:

```python
# Preflight resource guards
MIN_AVAILABLE_RAM_BYTES = 50 * 1024 * 1024
MIN_FREE_DISK_BYTES     = 50 * 1024 * 1024
DISK_RECHECK_EVERY_CELLS = 10   # 0 disables mid-run rechecks
```

Refactor preflight into a helper `_preflight_resources(target_dir) -> None` that:
- creates the target dir and lets `mkdir` `OSError` **propagate** (abort early with a clear
  message) rather than being swallowed;
- raises on low RAM / low disk using the config constants.

Optionally call a lightweight `_check_free_disk(target_dir)` every
`DISK_RECHECK_EVERY_CELLS` cells inside the matrix loop and abort the remaining cells
cleanly (record the partial summary) if space runs out.

## Safety
Per the engineering mandate, do **not** actually fill a partition. Test with
`unittest.mock.patch` on `shutil.disk_usage` / `psutil.virtual_memory` and a `tempfile`
target dir.

## Acceptance criteria
- No literal `50 * 1024 * 1024` remains in `orchestrator.py`.
- A mocked low-disk / low-RAM reading aborts the run with a `ValueError` before any
  conversion.
- A mocked `mkdir` `OSError` aborts the run (not swallowed as a warning).
- A mid-run mocked disk-full reading stops further cells and still persists a summary for
  completed cells.

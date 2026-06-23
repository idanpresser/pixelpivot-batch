# Batch Network Robustness & Startup Latency — Design

**Date:** 2026-06-23
**Branch context:** observed on `docs/tui-interactive-ux` during live conversion test against UNC share `\\ipsds5\Share\pics`.

## Problem

Live run of a 200-image × 24-cell (4800-conversion) matrix batch against an SMB/UNC share surfaced three defects in `app/batch_api/orchestrator.py` and `app/batch_api/routes.py`:

1. **Silent empty-scan success.** Batch 760 scanned the same UNC path and found 0 files (transient SMB enumeration result); batch 761 seconds later found 200. The empty path marks the run `completed` with `total_images=0` and returns. A broken/slow mount is indistinguishable from a genuinely empty folder — the user is told the job succeeded.

2. **~23s dead startup gap.** Between log lines `Starting Matrix Batch` (14:14:08) and the first `Processing Matrix Cell` (14:14:31), nothing converts. Cause: the `pre_run_mtimes` snapshot loop (`orchestrator.py:341-348`) issues `200 images × 24 cells = 4800` serial `Path.stat()` calls against the network `target_dir` before any conversion begins. (A second, necessary 4800-stat pass runs post-loop for output sizes.)

3. **Blind status polling.** The observed client polls `GET /batch/status/{id}` ~11×/25s. That endpoint returns the DB `batch_runs` row, which only updates at batch end — so it reports a constant `running` with no counters for the entire multi-minute job. Live counters already exist in-memory (`orchestrator.progress`) and are exposed at `GET /batch/{id}/progress`, but the client never calls it.

## Fixes

### A. Empty-scan correctness (highest severity)
- On empty `input_paths`, retry the `iterdir()` scan with a short backoff (e.g. 2 retries, ~0.5s each) to absorb transient SMB enumeration races.
- If still empty after retries, mark the run **`failed`** (not `completed`) with an explicit message (e.g. `"No images found in {source_dir} after N scan attempts — check the path is reachable and contains supported files."`).
- Reuse existing `failed` status (no schema change). The `failed` path already exists at `orchestrator.py:489` for zero-success runs; this aligns the empty-scan path with it.

### B. Kill the pre-loop stat storm (biggest perf win)
- Delete the `pre_run_mtimes` snapshot loop (`orchestrator.py:340-348`) entirely.
- The snapshot's only purpose is to answer "did THIS run produce this output file?" at `orchestrator.py:478-479`. The batch `start_time` (already captured at line 243) answers the same question: replace `prev = pre_run_mtimes.get(...)` / `st.st_mtime != prev` with `st.st_mtime >= start_time`.
- Eliminates 4800 network `stat()` calls before the first conversion. No parallelization needed.
- Edge case: `start_time` is client wall-clock, output `mtime` is set by the writer on the same host (local converters write to the mounted target). Equality vs leftover is safe with `>=`. Acceptable risk; document the assumption in a comment.

### C. Progress visibility (UX)
- Fold the live in-memory progress counters (`cells_done`, `cells_total`, `current_cell`, `ok`, `fail`) into the `GET /batch/status/{id}` response when a run is in-flight, so existing pollers get progress without a client change.
- `/batch/{id}/progress` remains as the dedicated live endpoint.

## Out of scope
- Poll-cadence / client-side throttling (client change, separate concern).
- Async conversion of the necessary post-loop output-size stat pass.
- Caching SMB directory listings.

## Testing
- **A:** unit test — orchestrator with a stubbed source dir that returns empty then non-empty across scans (retry succeeds); empty-throughout case asserts final status `failed` and a descriptive error. Avoid icons/non-ASCII in test strings.
- **B:** unit test — savings math credits only outputs with `mtime >= start_time`; a pre-existing leftover output (older mtime) is excluded from `output_bytes`. Assert no pre-loop stat pass occurs (e.g. produced count matches files written this run).
- **C:** route test — `/batch/status/{id}` for an in-flight run includes live counter fields sourced from `orchestrator.progress`.

## Beads
- **A** — empty-scan correctness (own bead)
- **B** — stat-storm perf (own bead)
- **C** — progress visibility (own bead)

All three are independent and can be implemented/merged in any order.

# Task 008 — Close the severed heuristic feedback loop

**Severity:** HIGH (the system's core value prop — "quality learned from data" — is inert)
**Phase:** II — Heuristic steel-thread, data lineage
**Confidence:** Confirmed by code read

## Problem

The heuristic generators read from the legacy analytics tables, but the live batch
path never writes to them, so the table can only ever be hand-seeded.

- Generators read `conversions c JOIN images i WHERE c.success = 1`:
  - `app/core/heuristic.py:44-56`
  - `tools/generate_heuristic_data.py:37-42`
- The batch path writes ONLY `batch_runs`, `batch_summary`, `batch_errors`
  (`app/batch_api/orchestrator.py:270-295`).
- `app/core/db/schema.py:4-7` states plainly that `images`/`conversions`/`metrics`
  are "legacy ... not exercised by the batch path."

Consequence: `generate_heuristic_table()` over a DB produced by real batch runs returns
an **empty** result and raises `RuntimeError("No successful conversions found")`
(`heuristic.py:66-68`). The shipped `heuristic_table.json` (version `1.0.0`,
categories `general/highRes/web/uiSharp/lowContrst/edgeCase`) is hand-curated, not
machine-generated. The feedback loop is open.

## Fix

Persist a minimal per-conversion analytics record during the matrix loop so the
generator has live data. Minimum columns the generator needs:
`images(category, width, height)` + `conversions(format, tool, quality, success)`.

Decision: write these from `orchestrator.execute_batch` per cell (we already have
`dim_cache`, the per-image `qualities`, `cell.tool`, `cell.target_format`, and the
converter's success/failure counts). Add a thin `AnalyticsRepository.record_conversions`
rather than overloading `BatchRepository`. Keep it best-effort (wrapped, never fails the
batch) but covered by a test that proves rows land.

## TDD plan

RED — `tests/test_task_008.py` (ASCII only):
1. Init an in-memory DB (`init_db`).
2. Run `execute_batch` against a tiny real-asset fixture dir (reuse the pattern in
   `tests/test_real_assets_end_to_end.py`) with one tool + one format.
3. Assert `SELECT COUNT(*) FROM conversions WHERE success = 1` > 0 and the joined
   `images` rows carry non-null `width/height/category`. (Fails today: count == 0.)
4. Assert `generate_heuristic_table()` over that DB no longer raises and emits the
   `(category, bucket, format, tool)` cell that was just exercised.

GREEN:
- Add `record_conversions(conn, run_id, records)` (upsert into `images`, then
  `conversions`, honoring the existing `UNIQUE` constraints in `schema.py:87,111`).
- Call it once per cell after `converter.convert_batch(...)` in
  `orchestrator.py:224-240`, building records from `input_paths`, `dim_cache`,
  `qualities`, `cell`, and per-file success (needs `convert_batch` to surface which
  paths succeeded — see note below).

## Notes / coupling
- `convert_batch` currently returns only counts + an `errors` list, not a per-path
  success map (`base.py:281-287`). Either derive success-by-absence-from-errors, or
  (cleaner) extend the batch contract. Coordinate with [task_013](task_013_mogrify_circuit_breaker_accounting.md).
- This task makes [task_009](task_009_unify_heuristic_generators.md) and
  [task_010](task_010_emit_heuristic_version.md) meaningful (there is finally real data
  to generate from and to version).

## Acceptance criteria
- After a real batch run, the generator's source query returns rows for the converted images.
- `generate_heuristic_table()` succeeds on a DB produced solely by the batch path.
- Recording failures are logged and never abort or fail the batch.
- ASCII-only test assertions.

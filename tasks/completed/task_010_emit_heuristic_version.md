# Task 010 — Emit a version key from the heuristic generators

**Severity:** MED (provenance recorded per run is silently meaningless after regeneration)
**Phase:** II — Heuristic steel-thread
**Confidence:** Confirmed by code read

## Problem

`HeuristicInterpolator` reads a top-level `version` key:

- `app/core/heuristic_interpolator.py:16` -> `self.version = self.table.get("version", "unknown")`

and that version is stamped onto every run:

- `app/batch_api/routes.py:30` and `app/batch_api/hot_folder.py:116` ->
  `heuristic_version=orchestrator.interpolator.version`.

But NEITHER generator writes a `version` key:

- `app/core/heuristic.py:97-110` builds `final_dict = {category: ...}` only.
- `tools/generate_heuristic_data.py:71-89` writes the nested dict only.

The shipped `heuristic_table.json` has `"version": "1.0.0"` because it was hand-edited.
The moment anyone regenerates the table, `version` disappears, `interpolator.version`
becomes `"unknown"`, and the `heuristic_version` column in `batch_runs` loses all
meaning. The existing test (`tests/core/test_heuristic_versioning.py`) only exercises a
hand-written fixture table, so it never catches this gap.

## Fix

Have the canonical generator (see [task_009](task_009_unify_heuristic_generators.md))
write a `version` into the emitted JSON. Options:
- A `version: str` argument to `generate_heuristic_table(version=...)`, defaulting to a
  `HEURISTIC_TABLE_VERSION` constant in `app/core/config.py`, OR
- A content hash / generation timestamp if monotonic provenance is preferred.

Recommendation: explicit semver constant in `config.py` so rollbacks
(`test_heuristic_table_rollback`) stay deterministic.

## TDD plan

RED — `tests/test_task_010.py` (ASCII only):
1. Build a small fixture DB (images + successful conversions).
2. Call the canonical generator to write a table to a temp path.
3. Load it via `HeuristicInterpolator(temp_path)`.
4. Assert `interpolator.version != "unknown"` and equals the configured version.
   Fails today (key absent -> "unknown").

GREEN:
- Add `HEURISTIC_TABLE_VERSION` to `config.py`.
- Write `payload["version"] = version` in the generator before `json.dump`.

## Acceptance criteria
- A freshly generated table round-trips a real version through the interpolator.
- `batch_runs.heuristic_version` reflects that version for a run started after regeneration.
- ASCII-only test assertions.

## Re-scope (2026-05-27, after task_011 verification)

Premise CONFIRMED by direct read — the task stands, with two precisions:

- The committed table actually read by the engine (`app/core/heuristic_table.json`) carries
  `"version": "1.0.0"` (the task text was correct; the `1.2.3` seen elsewhere is only the
  mock fixture in `tests/core/test_heuristic_versioning.py`). It is hand-seeded.
- The regenerated, generator-produced table (`app/heuristic_table.json`) was verified to have
  NO `version` key and category-only contents. So both generators provably omit `version`.
- This task is COUPLED to [task_009](task_009_unify_heuristic_generators.md)'s output-path
  finding: emitting `version` is useless unless the generator also writes to
  `config.HEURISTIC_TABLE_PATH` (the path the interpolator loads). Sequence task_009 first,
  then task_010, or land both together. The RED test should load via
  `HeuristicInterpolator(config.HEURISTIC_TABLE_PATH)` semantics, not just an arbitrary temp
  path, to also guard the path coupling.

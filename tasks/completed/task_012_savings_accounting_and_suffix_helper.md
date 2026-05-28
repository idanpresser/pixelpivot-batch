# Task 012 — Fix savings_pct accounting on partial runs; kill the suffix round-trip hack

**Severity:** MED (reported savings are wrong on any skipped/aborted matrix; code smell)
**Phase:** I — residual of [task_005](completed/task_005_extract_matrix_planner.md)
**Confidence:** Confirmed by code read

## Problem

task_005 extracted `plan_matrix`/`output_name`, but the summary math in
`execute_batch` still has two defects:

1. **Denominator counts cells that never ran.**
   `app/batch_api/orchestrator.py:253` -> `input_bytes *= len(plan)` multiplies by the
   FULL matrix size. But cells are skipped for unsupported tools (`orchestrator.py:189-194`),
   broken converters (`196-201`), and the whole run can abort mid-way
   (`abort_matrix`, `203-209`). `output_bytes` only counts files that exist, so
   `savings_pct = 1 - output/input` (`268`) is skewed (often a large false "savings").

2. **Output rescan double-counts stale files.**
   `orchestrator.py:258-264` rescans the target dir by predicted name; a same-named file
   from a PRIOR run is counted as this run's output.

3. **Suffix derived via a string round-trip hack.**
   `orchestrator.py:221-222`:
   ```python
   name_example = output_name("TMP", cell, multi_category=multi_category)
   suffix = name_example.replace("TMP", "").replace(f".{fmt}", "")
   ```
   This re-parses `output_name`'s output to recover the suffix it just built.

## Fix

- Add a single `suffix_for(cell, multi_category) -> str` helper next to `output_name`
  (`orchestrator.py:61`) and have `output_name` call it. Replace the round-trip at
  `221-222` with a direct `suffix_for(cell, multi_category)` call. One source of truth.
- Track executed cells and the input bytes actually processed (accumulate per cell that
  ran), and compute `input_bytes` from those, not `len(plan)`.
- Count output bytes from files produced THIS run (e.g. capture produced paths from the
  converter result, or snapshot the target dir before the run and diff), not a blind rescan.

## TDD plan

RED — `tests/test_task_012.py` (ASCII only):
1. `suffix_for` unit tests: single-category -> `_<tool>`; multi-category ->
   `_<category>_<tool>`; assert `output_name` == `f"{stem}{suffix_for(...)}.{fmt}"`.
2. Accounting test: build a 2-tool plan where one tool is unsupported (so its cell is
   skipped). Assert `input_bytes` reflects only the executed cell(s) and `savings_pct`
   is within a sane band (not negative, not inflated by the skipped cell). Fails today.
3. Stale-file test: pre-create a file matching a predicted output name, run a batch that
   produces zero new files for that cell; assert the stale file is not attributed to this run.

GREEN:
- Introduce `suffix_for`; refactor the denominator to executed-cell bytes; scope output
  counting to this run.

## Acceptance criteria
- `savings_pct` is computed only over cells that actually executed.
- Suffix logic exists in exactly one place; no string round-trip.
- Stale pre-existing outputs are not counted.
- ASCII-only test assertions.

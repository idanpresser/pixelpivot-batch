# Task 017 — Min-sample gate + median aggregation for table cells

**Severity:** MED (thin corner buckets emit noisy means straight to production)
**Phase:** II — Heuristic steel-thread, robustness
**Confidence:** Confirmed by code read

## Problem

Even with ~1000 samples per category, the table fragments data across
buckets x formats x tools (roughly 60 cells/category, ~16 samples each on
average). Image sizes cluster in medium/large, so the `small` and `xlarge`
corner buckets often hold only a handful of samples. The generator emits a plain
MEAN regardless:

- `app/core/heuristic.py` -> `avg_q = sum(q_list) / len(q_list)` then
  `cast_quality(fmt, avg_q)`.

There is no minimum-sample gate and no outlier resistance, and the interpolator
treats a 3-sample cell as authoritative as a 200-sample one (it has no access to
`sample_count`). A few mis-calibrated samples in a thin cell land directly in the
shipped table.

## Fix

In the canonical generator:
- Drop any cell with `len(q_list) < HEURISTIC_MIN_SAMPLES` (new constant in
  `config.py`). An absent cell makes the interpolator fall back via
  `default_quality_for` (see [task_011](completed/task_011_tool_aware_quality_fallback.md)),
  which is safer than a noisy 2-sample mean.
- Aggregate with the MEDIAN rather than the mean (robust to outliers), still
  routed through `utils.cast_quality`.

## Coupling

- [task_016](task_016_direct_quality_curve_fit.md) SUPERSEDES the per-cell
  aggregation. If task_016 is scheduled, implement ONLY the min-sample gate there
  (a curve needs enough points to fit) and DROP the median half of this task — a
  regression over raw points has no per-cell mean/median to choose. Treat this
  task as the cheap interim improvement to the CURRENT bucket model when task_016
  is deferred.

## TDD plan

RED — `tests/test_task_017.py` (ASCII only):
1. Fixture DB: a `(cat, bucket, fmt, tool)` cell with 2 samples, one a gross
   outlier; another cell with many samples.
2. Generate the table.
3. Assert the under-sampled cell is ABSENT (count below the gate) -> the
   interpolator returns the tool-aware fallback for that combo. Fails today
   (cell present with a 2-sample mean).
4. Assert a well-sampled cell's value equals the MEDIAN of its inputs, not the
   mean (choose inputs where median != mean so the difference is unambiguous).
   Fails today (mean).

GREEN:
- Add `HEURISTIC_MIN_SAMPLES` to `config.py`.
- Gate + median in `generate_heuristic_table` (and the CLI generator, or rely on
  [task_019](task_019_cli_generator_version_parity.md) to converge them).

## Acceptance criteria
- Cells below the sample gate are omitted (fall back), not emitted as noisy means.
- Well-sampled cells use the median; the gate is sourced from `config.py`.
- ASCII-only test assertions.

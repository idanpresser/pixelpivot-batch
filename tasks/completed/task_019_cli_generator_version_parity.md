# Task 019 — CLI generator parity (emit version; converge on one generator)

**Severity:** LOW-MED (provenance breaks when the table is regenerated via the CLI)
**Phase:** II — Heuristic steel-thread, data lineage
**Confidence:** Confirmed by code read

## Problem

[task_010](completed/task_010_emit_heuristic_version.md) made the canonical
generator (`app/core/heuristic.generate_heuristic_table`) stamp a `version` into
the table. The standalone CLI generator did NOT get that treatment:

- `tools/generate_heuristic_data.py` -> `HeuristicGenerator.generate()` returns a
  category-only dict; `save()` dumps it with no `version` key.

So a table produced by the CLI reads back as `interpolator.version == "unknown"`,
silently losing the provenance recorded on every batch run
(`batch_runs.heuristic_version`). [task_009](completed/task_009_unify_heuristic_generators.md)
unified the two generators' bucketing and casting but left them as two separate
implementations.

## Fix

Converge on a single generator. Preferred: make the CLI a THIN WRAPPER over
`app/core/heuristic.generate_heuristic_table` — its `__main__` resolves the DB
path / output paths and calls the canonical function, so version (and any future
schema like [task_016](task_016_direct_quality_curve_fit.md)) is inherited for
free and there is exactly one code path to maintain.

If `HeuristicGenerator` must remain a class (other callers depend on it), have
`generate()`/`save()` write `"version" = config.HEURISTIC_TABLE_VERSION` so the
emitted table round-trips a real version.

## Coupling

- [task_016](task_016_direct_quality_curve_fit.md) changes the table SCHEMA. If
  the CLI delegates to the canonical function (preferred), it inherits the new
  schema automatically — strongly favouring the thin-wrapper option.

## TDD plan

RED — `tests/test_task_019.py` (ASCII only):
1. Build a fixture DB (images + successful conversions).
2. Produce a table via the CLI generator path (the wrapper, or
   `HeuristicGenerator().save(...)`).
3. Load it with `HeuristicInterpolator` and assert
   `version == config.HEURISTIC_TABLE_VERSION` and `!= "unknown"`. Fails today.
4. If the wrapper route is taken: assert the CLI output cells equal the canonical
   generator's cells for the same DB (one source of truth).

GREEN:
- Delegate the CLI to `generate_heuristic_table` (preferred), or write the
  `version` key in the CLI's own output.

## Acceptance criteria
- A table produced via the CLI round-trips a real version through the interpolator.
- Ideally a single generator implementation backs both entry points.
- ASCII-only test assertions.

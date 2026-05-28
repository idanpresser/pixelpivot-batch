# Task 009 — Unify the two divergent heuristic generators

**Severity:** HIGH (same DB yields different tables depending on which generator runs)
**Phase:** II — Heuristic steel-thread
**Confidence:** Confirmed by code read

## Problem

Two independent generator implementations disagree on two axes:

1. Resolution-bucket boundary at exactly 8.0 MP:
   - `app/core/utils.py:145` -> `megapixels <= 8.0` is **large**
   - `tools/generate_heuristic_data.py:18` -> `mp < 8.0` else **xlarge**
     (so an 8.0 MP image lands in different buckets)
   - `app/core/heuristic.py:87` correctly uses `utils.get_resolution_bucket`.

2. Quality casting:
   - `heuristic.py:104-106` keeps a float average, `round(avg, 2)`.
   - `generate_heuristic_data.py:22-25,79-80` int-casts non-jxl (`int(q + 0.5)`),
     float for jxl.

Same `conversions`/`images` data therefore produces non-identical
`heuristic_table.json` files. Whichever generator an operator happens to run silently
changes the shipped quality curve.

## Fix

Establish ONE canonical generator and one bucketing function.

- Make `tools/generate_heuristic_data.HeuristicGenerator` delegate bucketing to
  `app/core/utils.get_resolution_bucket` (delete the private `_get_bucket`), OR retire
  the standalone tool and have it call `app/core/heuristic.generate_heuristic_table`.
- Pick one casting rule and centralize it (e.g. a `cast_quality(format, q)` helper in
  `app/core/utils.py` or `config.py`), used by both the table and weights writers.
- Recommendation: keep `app/core/heuristic.generate_heuristic_table` as the single source
  of truth (it already emits the weights file + time buckets) and make the CLI a thin
  wrapper around it.

## TDD plan

RED — `tests/test_task_009.py` (ASCII only):
1. Parametrized boundary test: a synthetic image at exactly 8.0 MP (e.g. 4000x2000)
   must map to the SAME bucket through every code path
   (`utils.get_resolution_bucket`, the CLI generator, `heuristic.py`). Fails today.
2. Casting test: feed a known set of qualities for a webp/magick cell and a jxl cell;
   assert both generators emit identical values for the same input. Fails today.

GREEN:
- Route all bucketing through `utils.get_resolution_bucket`.
- Centralize `cast_quality` and use it in both writers.
- Delete or thin-wrap the duplicate.

## Acceptance criteria
- A single bucketing function is used everywhere (grep shows no second MP->bucket ladder).
- Both generation entry points produce byte-identical category/bucket/format/tool cells
  for the same DB.
- The 8.0 MP boundary is covered by an explicit test.
- ASCII-only test assertions.

## Re-scope (2026-05-27, after task_011 verification)

Re-read of both generators confirmed the two divergences above AND surfaced a third,
arguably higher-severity defect:

3. **Output-path mismatch — the production generator's table is never read.**
   - `app/core/heuristic.py:12` writes to `OUTPUT_TABLE_PATH = APP_ROOT / "heuristic_table.json"`.
   - The engine loads `config.HEURISTIC_TABLE_PATH = APP_ROOT / "core" / "heuristic_table.json"`
     (`heuristic_interpolator.__init__`).
   - Both files exist on disk today: the engine reads `app/core/heuristic_table.json`
     (hand-seeded, `version=1.0.0`, uniform values e.g. jxl=90 everywhere), while
     `app/heuristic_table.json` (the regenerated, version-less, category-only output) sits
     orphaned. So regenerating the table has ZERO effect on conversions. This must be folded
     into the unification: the canonical generator MUST write to `config.HEURISTIC_TABLE_PATH`.

4. **Casting rule is now settled by [task_011](task_011_tool_aware_quality_fallback.md).**
   jxl is a 0..100 quality (not a distance) throughout the pipeline, so
   `generate_heuristic_data._cast_quality`'s jxl-vs-non-jxl split is moot. Per `CLAUDE.md`
   ("never cast to int"; interpolation needs the fraction), the canonical rule is
   `round(avg, 2)` for ALL formats — i.e. `heuristic.py`'s existing behavior. The CLI tool's
   `int(q + 0.5)` for non-jxl is the one that must change. NOTE: ffmpeg avif values are CRF
   (~28) but are still stored/averaged as plain numbers — no special-casing needed.

Net: keep `app/core/heuristic.generate_heuristic_table` as the single source of truth,
make the CLI a thin wrapper, route all bucketing through `utils.get_resolution_bucket`,
and ensure the one writer targets `config.HEURISTIC_TABLE_PATH`. Add a regression test that
the generated table lands at the path the interpolator actually loads.

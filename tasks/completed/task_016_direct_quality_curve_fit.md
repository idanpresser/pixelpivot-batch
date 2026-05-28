# Task 016 — Fit a direct quality=f(MP) curve instead of 4-bucket means

**Severity:** HIGH (accuracy; this is the core "optimal quality from data" value prop)
**Phase:** II — Heuristic steel-thread, modelling
**Confidence:** Design proposal (verified against current interpolator/generator)

## Context / assumptions

- The batch app is fed a DB imported from the main PixelPivot app, with ~1000
  good per-image `(megapixels, quality)` samples per category. We are NOT
  measuring SSIM in the batch app; the stored `conversions.quality` is treated
  as the source of truth. This task does NOT introduce calibration/measurement.

## Problem

The pipeline collapses rich per-image data into a coarse, lossy approximation:

- `app/core/heuristic.py` aggregates each `(category, bucket, format, tool)` cell
  to a single MEAN (`sum(q_list) / len(q_list)`), reducing ~1000 samples to at
  most 4 numbers per `(category, format, tool)`.
- `app/core/heuristic_interpolator.py:99` then does LINEAR interpolation between
  those 4 means, anchored at FIXED bucket centers
  (`:18-23` -> small 0.25, medium 1.25, large 5.0, xlarge 12.0).

Two losses, even with perfect data:
1. Within-bucket resolution variation is discarded (a 2.1 MP and a 7.9 MP image
   both map to the single "large" mean).
2. Interpolation anchors at hardcoded MP centers, not the data's real centroids,
   so the curve is biased wherever a bucket's true mean MP differs from its center.

Quality-vs-resolution is non-linear (diminishing), so a piecewise line through 4
biased means leaves accuracy on the table that the 1000 samples already paid for.

## Fix

Model `quality` as a continuous function of resolution per `(category, format, tool)`,
fit over the raw `(megapixels, quality)` pairs:

- Baseline model: log-linear least squares `q = a + b * log10(megapixels)`
  (2 params, interpretable, robust). Isotonic/monotonic regression is an
  acceptable alternative if monotonicity must be guaranteed.
- New table schema: `category -> format -> tool -> { "a", "b", "n", "mp_min", "mp_max" }`
  (replaces `category -> bucket -> format -> tool -> value`). Keep the top-level
  `version` key (see [task_010](completed/task_010_emit_heuristic_version.md)).
- `HeuristicInterpolator.get_interpolated_quality` evaluates the curve at the
  image's MP, then clamps to: (a) the observed `[mp_min, mp_max]` (no wild
  extrapolation), and (b) the encoder's valid range for `(tool, format)` (reuse
  the bounds behind [task_011](completed/task_011_tool_aware_quality_fallback.md)).
- Min-sample gate: skip fitting a cell with `n < HEURISTIC_MIN_SAMPLES` (config);
  the interpolator then falls back via `default_quality_for` (task_011). This
  folds in [task_017](task_017_min_sample_gate_and_median.md)'s gate.

## Coupling

- SUPERSEDES the bucket model. If this task is scheduled, fold task_017's
  min-sample gate in here and SKIP task_017's median change (a regression over raw
  points has no per-cell mean to replace).
- The table schema change must land with a matching interpolator; bump
  `HEURISTIC_TABLE_VERSION` so a stale bucket-shaped table is detectable.
- [task_019](task_019_cli_generator_version_parity.md): the CLI generator must
  emit the same new schema (another reason to make it delegate to the canonical fn).

## TDD plan

RED — `tests/test_task_016.py` (ASCII only):
1. Build a fixture DB whose `(mp, quality)` pairs follow a KNOWN log-linear law
   (e.g. q = 95 - 8*log10(mp)) across a wide MP range for one (cat, fmt, tool).
2. Generate the table; load via `HeuristicInterpolator`.
3. Assert `get_interpolated_quality` matches the known law within a tolerance at
   several MPs, INCLUDING intermediate values (e.g. 3.5 MP) where the old
   4-bucket interpolation was provably off. Fails today (bucket model).
4. Assert clamping: a MP far above `mp_max` returns the curve value at `mp_max`,
   and the result never violates the encoder's valid range.
5. Assert a cell with `n < HEURISTIC_MIN_SAMPLES` is absent -> interpolator
   returns the tool-aware fallback.

GREEN:
- Add the curve fit to the canonical generator; store params.
- Rewrite the interpolator to evaluate the curve (same public signature).
- Add `HEURISTIC_MIN_SAMPLES` to `config.py`.

## Acceptance criteria
- Quality is read from a fitted curve, not bucket-mean interpolation.
- Intermediate-MP accuracy is within tolerance of the known law in tests.
- Results are clamped to observed MP range and to the encoder's valid range.
- Under-sampled cells fall back rather than emit a noisy fit.
- `get_interpolated_quality(category, format, tool, w, h)` signature unchanged.
- ASCII-only test assertions.

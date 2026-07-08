# Continuous Learning: Quality Curve Adaptation via Online Verification

**Date:** 2026-07-08  
**Status:** Design approved, ready for implementation  
**Epic:** Heuristic table self-improvement without offline calibration cost

## Problem

Heuristic curves (quality vs megapixels) are fitted offline via manual calibration runs. Once deployed, they're static until the next scheduled recalibration. Content shifts, encoder versions change, or category mixes drift — the curve never adapts. New categories start on conservative defaults and stay there until someone runs a ~30-min calibration.

Goal: **Enable live curves to adapt continuously** with low overhead, zero batch latency, and bounded confidence (no bad batch corrupts the canonical fit).

## Solution: Two-tier learning

### Tier 1: Steady-state verification (1% sample, per-batch)
- Sample 1% of converted outputs (capped per cell, bounded thread pool)
- Compute SSIM vs original (no re-encode, just two decodes + cv2 score)
- Measure error: `err = TARGET_SSIM − mean(ssim)`
- Update a leaky-integrator offset layer: `offset += k·err·sign − λ·offset`
- Write offset to sidecar file; canonical table untouched
- Run time: ~10 ms per SSIM on modern CPU; adds seconds to multi-hour batches only

### Tier 2: Cold-start bootstrap (new category, inline)
- First sight of a new (category, format, tool) cell: calibrate first 100 images inline
- Reuse winning encodes as final outputs (no double-encode)
- Fit canonical curve from SSIM-measured samples → reload interpolator
- Convert remaining images with warm curve
- Adds startup latency (100 x multi-encode search), but one-time per category and bounded

Quality resolution becomes:
```
q_adjusted = native_clamp( (a + b·log10(MP)) + offset[cell] )
```

## Architecture

### New files
- **`app/core/adjustment.py`**: `AdjustmentLayer` class
  - Loads/saves `heuristic_adjust.json` (sidecar)
  - `get(cell)` → offset
  - `update(cell, ssim_err, direction)` → leaky integrator, atomic write
  - Thread-safe via module-level lock

- **`app/batch_api/verification.py`**: Verification pipeline
  - `verify_and_nudge(cells, run_outputs, adjustment_layer)`
  - Sample 1% (bounded), score SSIM in thread pool
  - Aggregate per cell, call `adjustment_layer.update`

### Modified files
- **`app/core/heuristic_interpolator.py`**
  - `__init__` loads both `heuristic_table.json` (canonical) and `heuristic_adjust.json` (sidecar)
  - `get_interpolated_quality` applies offset after curve eval
  - New `reload()` method to refresh sidecar on cold-start bootstrap

- **`app/batch_api/orchestrator.py`**
  - Cold-cell detection in `execute_batch`; branch to inline calibration if needed
  - Call `verification.verify_and_nudge` in `_finalize_batch_run`

- **`app/core/heuristic.py`**
  - `generate_heuristic_table` fits only `WHERE calib_method='ssim'` (circular dependency fix)
  - Live-path conversions store predicted quality without `calib_method='ssim'`; they are excluded from fitting

- **`app/core/config.py`**
  - New constants (see §5)

### Sidecar format (`heuristic_adjust.json`)
```json
{
  "version": "1.0.0",
  "cells": {
    "highRes|webp|magick": {
      "offset": 2.3,
      "samples": 47,
      "updated_at": "2026-07-08T21:34:00Z"
    }
  }
}
```

Single writer, atomic write + `os.replace()` under lock. Reset = delete cells.

## Nudge math (leaky integrator)

Per cell, once per batch:
```
err   = TARGET_SSIM − mean(measured_ssim)      # >0 => under target
sign  = +1 if quality_direction(tool, fmt)=="ascending" else −1   # CRF inverted
delta = NUDGE_GAIN_K × err × sign
offset_new = clamp(
  offset × (1 − NUDGE_LEAK_LAMBDA) + delta,
  −NUDGE_MAX_OFFSET, +NUDGE_MAX_OFFSET
)
```

- Applied **once per cell per batch** from aggregated samples (mean SSIM), not per-image
- Only nudge if ≥ `VERIFY_MIN_FOR_NUDGE` valid SSIMs; else log drift, no write
- Leak term `(1−λ)` forgets stale drift; clamp bounds a bad batch's impact
- Honors encoder direction (ascending vs CRF inverted)

## Cold-start bootstrap (new category)

1. Detect: `interpolator.table[cat][fmt][tool]` absent
2. Calibrate first `min(BOOTSTRAP_SAMPLE_N, len(imgs))`
   - Run `find_optimal_quality` per image (existing secant search)
   - Move winning encode to final output directory (reuse, no double-encode)
   - Record `calib_method='ssim'` in conversions table
3. Refit canonical curve from all `calib_method='ssim'` samples
4. `interpolator.reload()` to pick up new curve
5. Convert remaining images normally with warm curve
6. Gated by `BOOTSTRAP_ENABLED`; honors `run_control.cancelled` per image

## Config constants

```python
ONLINE_LEARNING_ENABLED = env PIXELPIVOT_ONLINE_LEARNING → default True
VERIFY_SAMPLE_RATE      = 0.01                            # 1% of outputs
VERIFY_MAX_PER_CELL     = 50                              # cap threads on huge batches
VERIFY_MIN_FOR_NUDGE    = 3                               # min SSIMs before writing
NUDGE_GAIN_K            = 10.0                            # tuned; quality-pts per SSIM-unit
NUDGE_LEAK_LAMBDA       = 0.1                             # 10% forget per batch
NUDGE_MAX_OFFSET        = 10.0                            # native quality pts, clamped
BOOTSTRAP_ENABLED       = True
BOOTSTRAP_SAMPLE_N      = 100
HEURISTIC_ADJUST_PATH   = sibling of HEURISTIC_TABLE_PATH  # e.g. ./heuristic_adjust.json
```

All overridable via environment. Conservative defaults; tuning is empirical (see Testing).

## Circular dependency fix: calib_method filter

**Problem:** `generate_heuristic_table` fits from all conversions in the DB. Live batches write predicted qualities (from the interpolator) without `calib_method='ssim'`. Refitting from those is circular: it reinforces the predictions, converging to the initial default instead of the true optimal quality.

**Solution:** Fit only from rows where `calib_method='ssim'`:
```sql
WHERE c.success = 1 AND c.quality IS NOT NULL AND c.calib_method='ssim'
```

Live-path conversions set `calib_method=NULL` (or omit it); only calibration/bootstrap set `calib_method='ssim'`. The nudge mechanism (sidecar offsets) handles live-batch drift instead.

## Testing

### Unit
- `AdjustmentLayer`: load/save, clamp, leak, atomic write under lock
- Nudge sign for ascending vs descending encoders (CRF)
- Sampling: floor caps, min-samples gate
- `HeuristicInterpolator.get_interpolated_quality` with and without offset

### Integration
- Cold-cell bootstrap: detect → calibrate → refit → reload → convert (unit test fixture + E2E)
- Verify + nudge in finalize: sample count, SSIM aggregation, sidecar write
- `generate_heuristic_table` filters by `calib_method='ssim'`
- Offset application in quality resolution path

### E2E (manual)
- New category batch: verify bootstrap fires, first ~100 images slower, rest normal, curve fits
- Steady-state: run batch, verify 1% sampled, sidecar offset written, next batch uses offset
- Encoder upgrade: verify signal detects shift; offset adapts, drift logged

No icons in test output; string assertions.

## Overhead

| Scenario | Cost |
|---|---|
| Steady-state 1000 images, 4 cells | ~40 SSIM scores (1% × 1000 ÷ 4 cap) = ~400 ms, negligible vs multi-hour batch |
| New category, 100 bootstrap | 100 secant searches, reused as outputs, one-time per category |
| Cold start + rest | ~30 min (100 calibrated) + normal batch speed (rest) |

## Rollout strategy

1. Ship with `ONLINE_LEARNING_ENABLED=false` by default (opt-in)
2. Operators enable + tune `NUDGE_GAIN_K` on their content mix
3. `BOOTSTRAP_ENABLED` independent; can enable cold-start without steady-state learning
4. Monitor sidecar offset magnitudes; if >5pts consistently, investigate encoder or input mix change

## Known limitations & future work

- Nudge operates on intercept `a` only; slope `b` requires full recalibration (by design — single SSIM point doesn't constrain slope)
- Offset leaks to zero; very stale drift requires full recalibration to reset baseline
- No per-resolution stratification in nudge (single aggregated offset per cell); fine-grained correction requires cold start
- Bootstrap inline calibration blocks batch start; large N on fast I/O may justify background job in future

## References

- `app/core/calibrator.py`: `find_optimal_quality` (reused for bootstrap)
- `app/core/similarity.py`: `score_ssim` (reused for verification)
- `app/core/heuristic.py`: `generate_heuristic_table` (curve fitting, now calib_method-filtered)
- `app/core/heuristic_interpolator.py`: Quality resolution (now offset-aware)
- CLAUDE.md: Known issues bd-qk1.2 (CALIBRATION_ENABLED global state leaky, addressed by sidecar isolation)

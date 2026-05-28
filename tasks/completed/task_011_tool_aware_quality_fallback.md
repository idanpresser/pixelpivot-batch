# Task 011 — Tool/format-aware quality fallback (CRF-safe defaults)

**Severity:** MED (silent worst-quality output for ffmpeg CRF formats on any data miss)
**Phase:** II — Heuristic steel-thread / converter contract
**Confidence:** Confirmed by code read

## Problem

The heuristic value stored per `(category, bucket, format, tool)` is **tool-and-format
native**, not normalized:

- ffmpeg avif -> `-crf str(q)` (lower = better); shipped table value ~28
  (`app/core/converters/ffmpeg_batch_helpers.py:212`)
- magick/vips/sharp avif -> `-quality str(q)` (higher = better); shipped value ~82
  (`app/core/converters/magick_converter.py:33`)
- ffmpeg_nvenc avif -> expects 0-100 higher=better, converts to `-cq (100-q)/2`
  (`app/core/converters/ffmpeg_nvenc_converter.py:39`)

But BOTH fallback paths return a single tool-agnostic scalar:

- `app/batch_api/orchestrator.py:120` -> `1.0 if jxl else 80.0`
- `app/core/heuristic_interpolator.py:55-60` -> `1.0 if jxl else 80.0`

For ffmpeg avif a fallback of `80.0` becomes `-crf 80`, which is outside libaom-av1's
valid CRF range (0-63) -> clamped to worst quality or rejected. A probe failure
(`orchestrator.py:118`) or a missing table cell therefore silently produces near-garbage
output for ffmpeg, with no error.

## Fix

Make the fallback aware of `(tool, format)`. Add a `DEFAULT_QUALITY_BY_TOOL_FORMAT`
table to `app/core/config.py` (e.g. `("ffmpeg","avif") -> 30`, `("magick","avif") -> 82`,
`jxl -> 1.0`, generic 0-100 -> 80) and resolve through it in BOTH the interpolator
fallback and `_probe_quality`. Single source of truth in config (per repo convention).

## TDD plan

RED — `tests/test_task_011.py` (ASCII only):
1. `HeuristicInterpolator` over an EMPTY table:
   - `get_interpolated_quality("general","avif","ffmpeg",4000,3000)` returns a
     CRF-valid value (assert `<= 63`, e.g. ~30), NOT 80. Fails today.
   - `("general","avif","magick",...)` still returns ~80.
   - `("general","jxl","ffmpeg",...)` still returns the jxl distance default.
2. `orchestrator._probe_quality` on a path that fails to probe returns the same
   tool-aware default.

GREEN:
- Add the config map + a `default_quality_for(tool, format)` helper.
- Use it in `heuristic_interpolator.py:58` and `orchestrator.py:120`.

## Acceptance criteria
- No fallback ever yields a CRF outside the encoder's valid range.
- Defaults live in `config.py`, not inline literals.
- ASCII-only test assertions.

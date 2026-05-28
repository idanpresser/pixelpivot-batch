# Task 013 — Native mogrify batch must update circuit-breaker accounting

**Severity:** MED (circuit breaker is half-blind on the fast path)
**Phase:** III — "Jealous Process" resilience, caveat from task_000
**Confidence:** Confirmed by code read

## Problem

`BaseConverter` tracks failures and trips a breaker:

- `app/core/converters/base.py:41-49` -> `_mark_failure` / `_reset_failures`,
  `failure_threshold = 3`, `is_broken`.
- The orchestrator gates each cell on `converter.is_broken`
  (`app/batch_api/orchestrator.py:196`).

But `MagickConverter.convert_batch` (the native `mogrify` fast path,
`app/core/converters/magick_converter.py:108-237`) updates `success_count` /
`failure_count` WITHOUT calling `_mark_failure` / `_reset_failures`:

- On native success (`magick_converter.py:190-199`) it never `_reset_failures()`.
- On native failure it falls back to per-file `self.convert(...)` (`201-211`), and only
  THAT path (via `_run_subprocess`, `base.py:133-136`) touches the breaker.

So a converter whose native batch keeps failing only trips the breaker via the slower
fallback, and a healthy native batch never clears stale `consecutive_failures`. The fast
path and the breaker are decoupled — exactly the caveat flagged in
`tasks/task_000_matrix_audit_summary.md` (Phase III, "PASS with caveat").

## Fix

Route native-batch outcomes through the breaker:
- Call `self._mark_failure()` when a mogrify chunk fails (before/independent of the
  per-file fallback).
- Call `self._reset_failures()` when a chunk (or the batch) succeeds.
- Keep accounting consistent so a single shared notion of health drives `is_broken`.
  Consider a small protected helper on `BaseConverter` so VIPS/Sharp native batches can
  reuse it.

## TDD plan

RED — `tests/test_task_013.py` (ASCII only):
1. Subclass/stub `MagickConverter` so the mogrify subprocess always "fails" (returncode
   != 0) AND the per-file fallback also fails.
2. Invoke `convert_batch` over >= `failure_threshold` chunks (or call it repeatedly).
3. Assert `converter.is_broken` becomes True driven by the native path.
   Today it only flips via the fallback's `_run_subprocess`; assert the native path
   contributes (e.g. breaker trips even when the fallback is monkeypatched to no-op).
4. Success case: after a successful native chunk, assert `consecutive_failures == 0`.

GREEN:
- Add `_mark_failure`/`_reset_failures` calls on the native chunk outcome in
  `magick_converter.convert_batch`.

## Acceptance criteria
- Native mogrify failures advance the breaker without depending on the per-file fallback.
- Native successes reset the failure counter.
- No double-counting when the fallback also runs.
- ASCII-only test assertions.

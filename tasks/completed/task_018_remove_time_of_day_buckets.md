# Task 018 — Remove the unused time-of-day buckets

**Severity:** LOW (dead computation + a misleading artifact)
**Phase:** II — Heuristic steel-thread, cleanup
**Confidence:** Confirmed — no consumer reads the time-of-day data

## Problem

`app/core/heuristic.py` computes a whole time-of-day analysis on every
regeneration and writes it into the weights file, but nothing ever reads it:

- `get_time_bucket(arrival_time)` (`:19-38`) classifies day/night/twilight.
- `time_group_data` accumulation (`:108-110`).
- `flat_lookup_by_time` build (`:142-153`) and the `"time_buckets"` /
  `"lookup_by_time"` keys in `weights_payload` (`:165, :170`).

A grep across `app/` and `tools/` shows these symbols are referenced ONLY within
`heuristic.py` itself. `HeuristicInterpolator` loads the table and never touches
the weights file at all, so the time-of-day machinery is pure dead weight.

## Fix

Remove the time-of-day code path:
- Delete `get_time_bucket`, the `time_group_data` accumulation, the
  `flat_lookup_by_time` loop, and the `"time_buckets"` / `"lookup_by_time"` keys
  from `weights_payload`.
- Leave the rest of the weights file (`res_buckets`, `lookup` with `sample_count`)
  untouched for now.

NOTE (out of scope, flag only): the weights file as a whole also appears unread by
the interpolator. Whether to keep generating it at all is a separate decision —
do NOT remove the whole file under this task.

## TDD plan

RED — `tests/test_task_018.py` (ASCII only):
1. Build a small fixture DB (include `arrival_time` values spanning day/night).
2. Generate; load the emitted weights JSON.
3. Assert the payload has NO `"lookup_by_time"` and NO `"time_buckets"` keys.
   Fails today (both present).
4. Assert `get_time_bucket` is no longer importable from the module (attribute
   gone). Fails today.
5. Regression: the heuristic TABLE cells are unchanged by the removal.

GREEN:
- Delete the time-of-day code and keys per the fix.

## Acceptance criteria
- No time-of-day keys are emitted in the weights file.
- `get_time_bucket` is removed.
- Heuristic table cells are byte-identical before/after for the same DB.
- ASCII-only test assertions.

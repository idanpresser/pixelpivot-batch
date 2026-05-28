# Task 020 — Stop dropping every batch's per-tick telemetry to FK constraint failure

**Severity:** HIGH (analytics; the live monitor runs perfectly and then the
sink discards every sample. `batch_summary.cpu_avg_pct / cpu_peak_pct` come
out 0.0 on every batch despite the converter actually using a CPU/GPU.)
**Feature:** B6 / G2 / F2 (analytics persistence)
**Air-gap relevance:** general — the dropped warnings spam the log on every
batch, and on a network-disabled box those warnings cannot be triaged remotely.

## Reproduction (the breadcrumb)

Run the audit harness:
```
$env:PYTHONIOENCODING='utf-8'
python tests\audit_threads\harness_01_api_orch_db.py
```
Observe in stderr/stdout (excerpted):
```
2026-05-28 06:48:08,085 - WARNING - [telemetry.py:insert_telemetry_batch] -
   telemetry batch dropped (3 samples): FOREIGN KEY constraint failed
```
And confirm `batch_summary`:
```
B7: batch_summary row -> (693.18..., 1, 0, 55.67...)
cpu_avg_pct=0.0, cpu_peak_pct=0.0   <-- live monitor produced real samples;
                                        the in-memory aggregate is correct,
                                        but the per-tick TABLE got zero rows.
```
The warning fires from `app/core/db/repositories/telemetry.py:82` in
`insert_telemetry_batch` for every batch (and every native chunk).

## Root cause (from the code, not a doc)

Mismatch between the foreign-key target and the writer:

- `app/core/db/schema.py:168-177` — `pipeline_telemetry.run_id INTEGER NOT NULL
  REFERENCES pipeline_runs(id) ON DELETE CASCADE`.
- `app/core/converters/base.py:118` — `TelemetryMonitor(pid=proc.pid, ...,
  run_id=run_id)` is constructed with the *batch_runs* id (passed by the
  orchestrator at `app/batch_api/orchestrator.py:246`).
- `app/core/telemetry.py:241` puts the sample tuple onto the flush queue with
  that same id; `insert_telemetry_batch` then tries to write
  `(batch_runs.id, ...)` into a column whose FK requires `pipeline_runs.id`.
  `pipeline_runs` is the *legacy* analytics table (`schema.py:144-154`).

Net effect: zero per-tick samples land for the entire batch path, but the
in-memory `_get_summary()` aggregate (which is what `batch_summary` reads via
`aggregate_telemetry`) still works for the deque-backed peaks. The two GPU
columns in the example above (`gpu_peak_pct=3.0`, `vram_peak_mb=4377.24`) came
from the in-memory deque, *not* the per-tick rows.

## Required behavior

Per-tick telemetry samples produced during a `batch_runs` execution must
either (a) be persistable to a table that references `batch_runs.id`, or
(b) be skipped silently with no FK noise. The runtime cost is "1 warning per
~3 samples per batch" today — pure log spam masking real failures.

Pick one of these solutions, in preferred order:

1. **Decouple per-tick samples from the legacy `pipeline_runs` FK.** Add a
   dedicated `batch_telemetry` table that mirrors `pipeline_telemetry`'s
   columns but references `batch_runs(id)` (or no FK at all — the column is
   informational). Switch `TelemetryMonitor._flush_loop` to write there when
   the run_id originated from a batch.
2. **Drop the FK on `pipeline_telemetry`.** Make the column a plain `INTEGER`
   so the legacy and batch paths can both write without violation. Cheaper
   but loses cascade-on-delete for the legacy path.
3. **Skip per-tick persistence entirely on the batch path.** Wire a flag from
   `_run_subprocess`/`_run_library` so `TelemetryMonitor` only enqueues
   samples when a *legacy* `pipeline_runs.id` is in scope. Simplest and
   matches the "telemetry is best-effort; the summary row is the truth"
   comment in `repositories/telemetry.py:1-11`.

Whichever you pick, the production loop must emit no
`FOREIGN KEY constraint failed` warnings during a normal batch.

## TDD plan

RED — `tests/test_task_020.py` (ASCII only):

1. Build a fresh in-memory or tempfile SQLite via `init_db`. ASCII path.
2. Create a `batch_runs` row via `BatchRepository.create_run` and record its
   id. Do NOT touch `pipeline_runs`.
3. Drive a single tick of telemetry persistence the same way the live code
   does it. Two equally valid shapes:
   - Direct: call `insert_telemetry_batch(conn, [(batch_id, "2026-05-28
     03:48:08", 1.0, 2.0, 3.0, 4.0)], auto_commit=True)` and assert it
     **does not warn** and **the row landed**.
   - Integration: start `TelemetryMonitor(run_id=batch_id)`, sleep ~0.6 s
     (two ticks at default interval), stop it, then count rows in the new
     batch-keyed telemetry table. Expect `>= 1`.
4. Assert no `FOREIGN KEY constraint failed` warning was logged. Easiest way
   to capture: install a `logging.Handler` on the `app.core.db.repositories.telemetry`
   logger that records `WARNING` and above into a list, and assert empty.
5. Negative test: a tick with a `run_id` that does NOT exist in
   `batch_runs` should *either* be silently dropped (if you pick option 3)
   *or* still land (option 2). Either is fine, but it must not log a fatal
   stack trace.

GREEN — minimal change:

- If option 1: add a new `_DDL_STATEMENT` for `batch_telemetry` (mirror of
  `pipeline_telemetry` minus the FK, referencing `batch_runs(id) ON DELETE
  CASCADE` instead). Migrate via a `PRAGMA table_info` check like the
  existing `gpu_peak_pct` migration in `schema.py:243-250`. Update
  `app/core/telemetry.py:_flush_loop` to import the new
  `insert_batch_telemetry_batch` and call it instead.
- If option 2: change schema.py:170 from `REFERENCES pipeline_runs(id) ...`
  to plain `INTEGER`. Migration: detect existing FK via `PRAGMA
  foreign_key_list('pipeline_telemetry')`; if present, recreate the table
  without it (SQLite needs a copy-rename dance — small, do it).
- If option 3: thread a `is_batch_run: bool = False` (or rename the
  parameter) through `_run_subprocess`/`_run_library`/`TelemetryMonitor` and
  skip the queue.put when true.

## Acceptance criteria

- [ ] On a real batch (one PNG -> webp via `magick`), zero
      `FOREIGN KEY constraint failed` warnings are emitted to stderr or the
      `pixelpivot.log` file during execute_batch.
- [ ] Either the new per-tick table (option 1) has rows after the batch, OR
      the legacy `pipeline_telemetry` table accepts the rows (option 2), OR
      no rows are attempted (option 3) -- whichever path you pick, prove it
      with a `SELECT COUNT(*)` in the test.
- [ ] `batch_summary.cpu_avg_pct` and `cpu_peak_pct` reflect the in-memory
      deque-based summary (unchanged behavior).
- [ ] Full `pytest` suite green (no regressions).
- [ ] Any new tunable in `app/core/config.py`.
- [ ] `convert_batch()` return shape unchanged.
- [ ] No `int()` cast of `quality` inside converter code.
- [ ] ASCII-only test code/messages.

## Constraints for the implementer (Sonnet)

TDD only (red before green, paste failing output first). No destructive ops,
no `git push` / force / amend / `--no-verify`. Fix exactly this defect — no
drive-by refactors. Behavior identical on Python 3.12 and 3.14.

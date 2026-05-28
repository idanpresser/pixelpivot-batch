# Task 015 — Reap orphaned "running" batches on startup; cap concurrency

**Severity:** LOW (stuck rows after a crash; unbounded background fan-out)
**Phase:** I/III — lifecycle + resilience
**Confidence:** Confirmed by code read

## Problem

Batches run as fire-and-forget background work:

- `app/batch_api/routes.py:33` -> `bg_tasks.add_task(orchestrator.execute_batch, ...)`
- The hot-folder path dispatches via `run_in_executor` (`hot_folder.py:131`).

`execute_batch` only ever transitions a run to `completed`/`failed` from within its own
process (`orchestrator.py:286,301`). If the API process is killed mid-batch (OOM, deploy,
crash), the `batch_runs` row is left `status="running"` forever — `GET /batch/status`
(`routes.py:39-58`) will report it as running indefinitely, and `get_all_runs` history
shows a permanent ghost. There is also no cap on how many batches run concurrently.

## Fix

1. **Startup reaper:** on app startup (FastAPI lifespan in `app/batch_api/main.py`), mark
   any `status="running"` rows as `interrupted` (new terminal state) with
   `completed_at = now`. Add `BatchRepository.reap_stale_running(conn) -> int`.
2. **Concurrency cap (optional, same task):** bound in-flight batches with a semaphore /
   small worker pool so a flood of `/batch/start` calls cannot exhaust the host. Size via
   a `MAX_CONCURRENT_BATCHES` constant in `config.py`.

## TDD plan

RED — `tests/test_task_015.py` (ASCII only):
1. Init DB; insert a `batch_runs` row with `status="running"` directly.
2. Call `repo.reap_stale_running(conn)`.
3. Assert the row is now `interrupted` (or `failed`) with a non-null `completed_at`, and
   the function returns the count reaped. Fails today (method does not exist / row stays "running").
4. Concurrency (if implemented): submit N+1 batches against a cap of N; assert no more
   than N run simultaneously (use a barrier/counter stub, not real subprocesses).

GREEN:
- Add `reap_stale_running` to `BatchRepository` and call it from the startup lifespan.
- Add the semaphore + `MAX_CONCURRENT_BATCHES` if the cap is in scope.

## Acceptance criteria
- After a simulated crash (an orphan "running" row), startup transitions it to a terminal state.
- `/batch/status` never reports a permanently-running ghost after restart.
- Concurrency, if capped, is enforced and sourced from `config.py`.
- ASCII-only test assertions.

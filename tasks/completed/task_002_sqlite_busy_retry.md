# Task 002 — Retry must-succeed summary writes on SQLITE_BUSY

**Severity:** HIGH (loses telemetry; marks a fully-successful run as "failed")
**Phase:** III — "DB Heartbreak" scenario
**Confidence:** Confirmed by code read

## Problem

The DB layer protects writes only with a 5-second lock wait:

- `connection.py:72` `PRAGMA busy_timeout=5000`
- `connection.py:92` `timeout=5.0`

There is **no application-level retry**. In `orchestrator.execute_batch`:

```python
# orchestrator.py:194-209
with get_connection() as conn:
    self.repo.save_summary(conn, ...)          # <- may raise OperationalError: database is locked
    self.repo.update_status(conn, run_id, "completed", ...)
...
# orchestrator.py:218-221
except Exception as e:
    log.error(...)
    with get_connection() as conn:
        self.repo.update_status(conn, run_id, "failed")   # contends on the SAME lock
```

If a concurrent writer (another batch finishing, the telemetry sink, WAL checkpoint)
holds the write lock for >5s, `save_summary` raises `sqlite3.OperationalError`. The
outer handler then:
1. **drops the aggregated telemetry summary entirely**, and
2. **marks a run whose conversions all succeeded as `failed`** — and that write may
   itself contend.

`repositories/batch.py:1-7` even documents these as "must-succeed" writes, but nothing
enforces it.

## Fix

Add a small bounded retry-with-backoff helper that only retries on the "locked"/"busy"
`OperationalError`, and wrap the summary + status writes with it:

```python
import sqlite3, time
from typing import Callable, TypeVar

T = TypeVar("T")

def with_busy_retry(fn: Callable[[], T], *, attempts: int = 5, base_delay_s: float = 0.1) -> T:
    for i in range(attempts):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            if "lock" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            if i == attempts - 1:
                raise
            time.sleep(base_delay_s * (2 ** i))   # 0.1, 0.2, 0.4, 0.8 ...
    raise RuntimeError("unreachable")
```

Wrap the completion block so a transient lock does not destroy the summary. Keep the
final `failed` fallback for *genuine* errors only (e.g. wrap it too so the status write
itself is resilient). Put `attempts` / `base_delay_s` in `config.py` per
[task_006](task_006_centralize_resource_thresholds.md) conventions.

## Acceptance criteria
- A simulated lock (hold a write transaction on the DB file from a second connection for
  ~1s using `tempfile`/in-memory fixtures — **no destructive disk ops**) causes the
  summary write to retry and ultimately succeed; the run ends `completed` with a summary row.
- Retry triggers ONLY on lock/busy errors; other `OperationalError`s propagate immediately.
- Backoff parameters are sourced from `config.py`.

# E5 — Telemetry + Dynamic Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production telemetry (Prometheus `/metrics`, optional OpenTelemetry spans) and a resource-adaptive, crash-resilient queue (RAM-aware chunk sizing, disk-% backpressure, DB-driven priority lanes) — every network-facing piece degradable and air-gapped-safe (default local/off).

**Architecture:** Five independent features under one epic. **e5.1** adds a lazy `metrics.py` (prometheus_client) with counters/gauges/histograms, instrumented from the orchestrator + queue manager, exposed at `/metrics`. **e5.2** adds a pure `dynamic_max_files(mp, ram_budget, ceiling)` that feeds the existing `pack_chunks(max_files=...)` seam. **e5.3** adds a disk-percent backpressure probe on the *resolved* `target_dir` volume, checked before a worker claims a job. **e5.4** is the architectural change: replace the in-memory `queue.Queue` in `BatchQueueManager` with DB polling (`ORDER BY priority DESC, created_at ASC` + atomic conditional claim), backed by a new `priority` column on `batch_runs`; GUI submit = high, hot-folder = low. **e5.5** adds a lazy `otel.py` span contextmanager (no-op unless `PIXELPIVOT_OTEL_ENABLED=1`) wrapping quality-curve calc, staging, and backend exec.

**Tech Stack:** FastAPI, `prometheus_client`, `opentelemetry-sdk` (optional/lazy), `psutil`, `shutil.disk_usage`, sqlite/postgres via E2's engine (`get_connection`), pytest.

**Beads:** `pixelpivot_batch-h53` (epic) — `.1` /metrics, `.2` chunk sizing, `.3` disk backpressure, `.4` priority lanes, `.5` OTel. Branch via beads-tdd-python, one PR for the epic. Recommended implementation order: **e5.4 → e5.1 → e5.2 → e5.3 → e5.5** (land the queue rewrite first so metrics `queue_depth` and disk-backpressure hook the new poll loop, not the dying in-mem queue).

---

## File Structure

| File | Responsibility | Bead |
|---|---|---|
| `app/core/db/schema.py` (modify) | Add `priority` column to `batch_runs` DDL (both dialects) + sqlite migration. | e5.4 |
| `app/core/db/repositories/batch.py` (modify) | `create_run(priority=...)`; add `claim_next_queued()` atomic poll. | e5.4 |
| `app/batch_api/queue_manager.py` (modify) | Replace in-mem queue with DB poll loop; keep `submit_*`/`stop` API. | e5.4 |
| `app/batch_api/routes.py` (modify) | `/batch/start` sets high priority. | e5.4 |
| `app/batch_api/hot_folder.py` (modify) | Hot-folder `create_run` sets low priority. | e5.4 |
| `app/batch_api/metrics.py` (create) | Lazy prometheus registry + record helpers. | e5.1 |
| `app/batch_api/main.py` (modify) | Mount `/metrics`. | e5.1 |
| `app/batch_api/orchestrator.py` (modify) | Record jobs_total / processing_seconds / compression_ratio. | e5.1 |
| `app/core/converters/chunk_sizing.py` (create) | Pure `dynamic_max_files(...)`. | e5.2 |
| `app/core/converters/ffmpeg_converter.py` (modify) | Use dynamic max_files in the multi-IO batch path. | e5.2 |
| `app/batch_api/image_guards.py` (modify) | `disk_pct_over_threshold(target_dir, pct)`. | e5.3 |
| `app/core/otel.py` (create) | Lazy `span(name)` no-op-unless-enabled contextmanager. | e5.5 |
| `app/core/config.py` (modify) | New env constants (see Task 1). | all |
| `tests/...` (create) | One test module per feature. | all |

---

## Task 1: Config constants for all five features

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Add constants near the existing batch/disk constants**

```python
# --- E5 telemetry + dynamic queue ---
METRICS_ENABLED = os.getenv("PIXELPIVOT_METRICS_ENABLED", "1") not in ("0", "false", "False")
"""Expose /metrics and record counters. Endpoint always mounts; recording no-ops when off."""

OTEL_ENABLED = os.getenv("PIXELPIVOT_OTEL_ENABLED", "0") not in ("0", "false", "False")
"""Emit OpenTelemetry spans. Default off; opentelemetry is imported lazily only when true."""

CHUNK_RAM_BUDGET_FRACTION = float(os.getenv("PIXELPIVOT_CHUNK_RAM_FRACTION", "0.25"))
"""Fraction of currently-available system RAM a single ffmpeg chunk may target."""

DISK_BACKPRESSURE_PCT = float(os.getenv("PIXELPIVOT_DISK_BACKPRESSURE_PCT", "90"))
"""Pause job pickup when the target_dir volume is at/above this percent full."""

DISK_BACKPRESSURE_POLL_S = 2.0
"""Seconds between disk-usage re-checks while paused for backpressure."""

QUEUE_POLL_INTERVAL_S = float(os.getenv("PIXELPIVOT_QUEUE_POLL_S", "0.5"))
"""Seconds a DB-polling worker sleeps when no queued job is available."""

PRIORITY_HIGH = 100   # GUI / API submit
PRIORITY_LOW = 0      # hot-folder sync
```

> If `config.py` lacks `import os`, add it.

- [ ] **Step 2: Verify import**

Run: `python -c "from app.core import config as c; print(c.METRICS_ENABLED, c.OTEL_ENABLED, c.DISK_BACKPRESSURE_PCT, c.PRIORITY_HIGH)"`
Expected: `True False 90.0 100`

- [ ] **Step 3: Commit**

```bash
git add app/core/config.py
git commit -m "feat(e5): add telemetry + dynamic-queue config constants"
```

---

## e5.4 — Priority lanes (DB-driven queue) [land first]

### Task 2: `priority` column + migration

**Files:**
- Modify: `app/core/db/schema.py` (sqlite DDL ~line 27-39, postgres DDL ~line 194-205, migration block ~line 418-424)
- Test: `tests/db/test_priority_column.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_priority_column.py
from app.core.db.connection import get_connection
from app.core.db.schema import init_db


def test_batch_runs_has_priority_column():
    init_db()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT priority FROM batch_runs LIMIT 0")
        cols = [d[0] for d in cur.description]
    assert "priority" in cols
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/db/test_priority_column.py -v`
Expected: FAIL (no such column: priority).

- [ ] **Step 3: Add column to both DDLs**

In `app/core/db/schema.py`, in *both* `CREATE TABLE ... batch_runs` blocks (sqlite ~line 36, postgres ~line 203), add after `heuristic_version`:

```sql
    priority        INTEGER NOT NULL DEFAULT 0,
```

- [ ] **Step 4: Add sqlite migration**

In the migration block (after the `heuristic_version` migration, ~line 424):

```python
            if "priority" not in columns:
                log.info("Migrating batch_runs: adding priority column")
                cur.execute("ALTER TABLE batch_runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
```

> `columns` is already computed from `PRAGMA table_info('batch_runs')` just above — reuse it.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/db/test_priority_column.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/db/schema.py tests/db/test_priority_column.py
git commit -m "feat(e5.4): add priority column to batch_runs with migration"
```

---

### Task 3: `create_run(priority=...)` + atomic `claim_next_queued()`

**Files:**
- Modify: `app/core/db/repositories/batch.py`
- Test: `tests/db/test_priority_claim.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/db/test_priority_claim.py
from app.core.db.connection import get_connection
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository


def _mk(conn, repo, priority):
    return repo.create_run(conn, source_dir="s", target_dir="t", target_format="webp",
                           tool="ffmpeg", trigger_type="api", status="queued", priority=priority)


def test_claim_returns_high_priority_first():
    init_db()
    repo = BatchRepository()
    with get_connection() as conn:
        low = _mk(conn, repo, 0)
        high = _mk(conn, repo, 100)
    claimed = repo.claim_next_queued(get_connection)
    assert claimed is not None
    assert claimed["id"] == high  # priority DESC beats insertion order


def test_claim_is_atomic_single_winner():
    init_db()
    repo = BatchRepository()
    with get_connection() as conn:
        rid = _mk(conn, repo, 50)
    first = repo.claim_next_queued(get_connection)
    second = repo.claim_next_queued(get_connection)
    assert first is not None and first["id"] == rid
    # Row already claimed (status flipped to 'running'); second sees nothing.
    assert second is None or second["id"] != rid


def test_claim_none_when_empty():
    init_db()
    repo = BatchRepository()
    # Drain anything left queued from prior tests.
    while repo.claim_next_queued(get_connection):
        pass
    assert repo.claim_next_queued(get_connection) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/db/test_priority_claim.py -v`
Expected: FAIL (`create_run` has no `priority` kwarg / `claim_next_queued` missing).

- [ ] **Step 3: Add priority to create_run**

In `create_run` (line 31), add the param and thread it into the INSERT:

```python
    def create_run(
        self,
        conn: sqlite3.Connection,
        source_dir: str,
        target_dir: str,
        target_format: str,
        tool: str,
        trigger_type: str,
        heuristic_version: Optional[str] = None,
        status: str = "running",
        priority: int = 0,
    ) -> int:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO batch_runs (
                    source_dir, target_dir, target_format, tool, trigger_type, status, heuristic_version, priority
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (source_dir, target_dir, target_format, tool, trigger_type, status, heuristic_version, priority),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else 0
        finally:
            cur.close()
```

- [ ] **Step 4: Add the atomic claim method**

Add to `BatchRepository` (uses a fresh connection per call so each claim is its own transaction):

```python
    @with_db_retry
    def claim_next_queued(self, get_conn) -> Optional[dict]:
        """Atomically claim the highest-priority queued run and mark it running.

        Ordering: priority DESC, then created_at ASC (FIFO within a lane).
        Claim is a conditional UPDATE so concurrent workers never double-pick:
        only the worker whose UPDATE affects the row (rowcount == 1) wins.
        Returns the claimed run row as a dict, or None if nothing is queued.
        """
        with get_conn() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT id FROM batch_runs WHERE status = 'queued' "
                    "ORDER BY priority DESC, created_at ASC, id ASC LIMIT 1"
                )
                row = cur.fetchone()
                if not row:
                    return None
                run_id = row["id"]
                cur.execute(
                    "UPDATE batch_runs SET status = 'running' WHERE id = ? AND status = 'queued'",
                    (run_id,),
                )
                if cur.rowcount != 1:
                    return None  # lost the race; caller re-polls
                cur.execute(
                    "SELECT id, source_dir, target_dir, target_format, tool, trigger_type, priority "
                    "FROM batch_runs WHERE id = ?",
                    (run_id,),
                )
                claimed = cur.fetchone()
                conn.commit()
                return dict(claimed) if claimed else None
            finally:
                cur.close()
```

> Ensure `Optional` is imported at the top of `batch.py` (it is — `get_run` uses it).

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/db/test_priority_claim.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/db/repositories/batch.py tests/db/test_priority_claim.py
git commit -m "feat(e5.4): create_run priority + atomic claim_next_queued"
```

---

### Task 4: Rewrite BatchQueueManager to DB-poll

**Files:**
- Modify: `app/batch_api/queue_manager.py`
- Test: `tests/batch_api/test_priority_queue.py` (create)

The public API stays: `start()`, `stop(grace_s=...)`, `submit_batch(run_id, request)`, `submit_calibration(...)`. Internally, `submit_*` only ensures `status='queued'` (the DB row is the queue); workers poll `claim_next_queued`. The in-mem `queue.Queue`, `resume_queued_jobs`, and the None-sentinel shutdown are removed — crash recovery is automatic because queued rows are simply picked up on next poll. Calibration runs are distinguished by reconstructing the request from the row's `trigger_type`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/batch_api/test_priority_queue.py
import time
from app.core.db.connection import get_connection
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository
from app.batch_api.queue_manager import BatchQueueManager


class _RecordingOrch:
    def __init__(self):
        self.run_controls = {}
        self.executed = []
    def execute_batch(self, run_id, request):
        self.executed.append(run_id)


def _enqueue(priority):
    repo = BatchRepository()
    with get_connection() as conn:
        return repo.create_run(conn, source_dir="s", target_dir="t", target_format="webp",
                               tool="ffmpeg", trigger_type="api", status="queued", priority=priority)


def test_worker_executes_high_priority_before_low():
    init_db()
    orch = _RecordingOrch()
    # Drain leftovers so ordering is deterministic.
    BatchRepository()  # noqa: ensure import side effects
    low = _enqueue(0)
    high = _enqueue(100)
    qm = BatchQueueManager(orch, max_workers=1)
    qm.start()
    deadline = time.time() + 10
    while len(orch.executed) < 2 and time.time() < deadline:
        time.sleep(0.05)
    qm.stop(grace_s=2.0)
    # High-priority row ran before the low-priority one.
    assert orch.executed.index(high) < orch.executed.index(low)


def test_submit_batch_sets_queued_status():
    init_db()
    orch = _RecordingOrch()
    repo = BatchRepository()
    with get_connection() as conn:
        rid = repo.create_run(conn, source_dir="s", target_dir="t", target_format="webp",
                              tool="ffmpeg", trigger_type="api", status="running")
    qm = BatchQueueManager(orch, max_workers=1)
    from app.batch_api.models import BatchRequest, Tool
    req = BatchRequest(source_dir="s", target_dir="t", target_format=["webp"],
                       tool=[Tool.ffmpeg], category=["general"], trigger_type="api")
    qm.submit_batch(rid, req)
    with get_connection() as conn:
        assert repo.get_run(conn, rid)["status"] == "queued"
```

> Verify `Tool.ffmpeg` is the correct enum member name in `app/batch_api/models.py` before running; adjust if it differs (e.g. `Tool.FFMPEG`).

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/batch_api/test_priority_queue.py -v`
Expected: FAIL (worker still uses in-mem queue; high/low ordering not guaranteed by DB priority).

- [ ] **Step 3: Rewrite queue_manager.py**

Replace the body of `app/batch_api/queue_manager.py` with the DB-poll design:

```python
"""BatchQueueManager — DB-polled bounded-concurrency executor for batch runs.

The queue *is* the batch_runs table: submit sets status='queued', workers poll
claim_next_queued() (priority DESC, created_at ASC) and atomically flip the row
to 'running'. Queue order and pending work survive a process restart with no
in-memory state to lose.
"""
import os
import threading
import time
from typing import Set, Optional

from .models import BatchRequest, CalibrationRequest, Tool
from .orchestrator import BatchOrchestrator
from ..core.db.connection import get_connection
from ..core.db.repositories.batch import BatchRepository
from ..core.config import QUEUE_POLL_INTERVAL_S, DISK_BACKPRESSURE_PCT, DISK_BACKPRESSURE_POLL_S
from ..core.logger import get_logger

log = get_logger(__name__)


class BatchQueueManager:
    def __init__(self, orchestrator: BatchOrchestrator, max_workers: int = 1):
        self.orchestrator = orchestrator
        self.max_workers = max_workers
        self.repo = BatchRepository()
        self._threads: list[threading.Thread] = []
        self._running_jobs: Set[int] = set()
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._threads = []
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"BatchQueueWorker-{i+1}", daemon=True)
            self._threads.append(t)
            t.start()
        log.info(f"Started BatchQueueManager (DB-poll) with {self.max_workers} worker(s).")

    def stop(self, grace_s: float = 5.0) -> None:
        log.info("Stopping BatchQueueManager (grace=%.1fs)...", grace_s)
        self._stopped = True
        with self._lock:
            for run_id in list(self._running_jobs):
                ctrl = self.orchestrator.run_controls.get(run_id)
                if ctrl:
                    log.info(f"Cancelling in-flight run_id={run_id} during shutdown.")
                    ctrl.cancel()
        for t in self._threads:
            t.join(timeout=grace_s)
        log.info("BatchQueueManager stopped.")

    def submit_batch(self, run_id: int, request: BatchRequest) -> None:
        """Mark a run queued. Workers pick it up by priority via DB poll."""
        if self._stopped:
            raise RuntimeError("Cannot submit to a stopped queue manager.")
        with get_connection() as conn:
            self.repo.update_status(conn, run_id, "queued")
        log.info(f"Queued batch run_id={run_id}.")

    def submit_calibration(self, run_id: int, request: "CalibrationRequest") -> None:
        if self._stopped:
            raise RuntimeError("Cannot submit to a stopped queue manager.")
        with get_connection() as conn:
            self.repo.update_status(conn, run_id, "queued")
        log.info(f"Queued calibration run_id={run_id}.")

    def _disk_backpressure_wait(self, target_dir: str) -> None:
        """Block while the target volume is over the disk-% threshold (e5.3)."""
        from .image_guards import disk_pct_over_threshold
        while not self._stopped and disk_pct_over_threshold(target_dir, DISK_BACKPRESSURE_PCT):
            log.warning("Disk backpressure: %s over %.0f%%; pausing pickup.", target_dir, DISK_BACKPRESSURE_PCT)
            time.sleep(DISK_BACKPRESSURE_POLL_S)

    def _reconstruct_request(self, row: dict):
        return BatchRequest(
            source_dir=row["source_dir"],
            target_dir=row["target_dir"],
            target_format=[f for f in row["target_format"].split(",") if f],
            tool=[Tool(t) for t in row["tool"].split(",") if t],
            category=["general"],
            trigger_type=row["trigger_type"],
        )

    def _worker_loop(self) -> None:
        while not self._stopped:
            try:
                claimed = self.repo.claim_next_queued(get_connection)
                if claimed is None:
                    time.sleep(QUEUE_POLL_INTERVAL_S)
                    continue
                run_id = claimed["id"]
                self._disk_backpressure_wait(claimed["target_dir"])
                if self._stopped:
                    # Return the row to the queue so a restart re-runs it.
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "queued")
                    break
                with self._lock:
                    self._running_jobs.add(run_id)
                try:
                    request = self._reconstruct_request(claimed)
                    log.info(f"Executing run_id={run_id} (priority={claimed['priority']}).")
                    self.orchestrator.execute_batch(run_id, request)
                except Exception as e:
                    log.error(f"Error executing run_id={run_id}: {e}", exc_info=True)
                finally:
                    self.orchestrator.run_controls.pop(run_id, None)
                    with self._lock:
                        self._running_jobs.discard(run_id)
            except Exception as e:
                log.error(f"Worker loop error: {e}", exc_info=True)
                time.sleep(QUEUE_POLL_INTERVAL_S)


_global_queue_manager: Optional[BatchQueueManager] = None


def init_queue_manager(orchestrator: BatchOrchestrator) -> BatchQueueManager:
    global _global_queue_manager
    max_workers = int(os.getenv("PIXELPIVOT_MAX_CONCURRENT_BATCHES", "1"))
    _global_queue_manager = BatchQueueManager(orchestrator, max_workers=max_workers)
    _global_queue_manager.start()
    return _global_queue_manager


def get_queue_manager() -> BatchQueueManager:
    global _global_queue_manager
    if _global_queue_manager is None:
        raise RuntimeError("BatchQueueManager has not been initialized.")
    return _global_queue_manager
```

> Calibration note: the calibration path previously branched on `isinstance(request, CalibrationRequest)`. Since the DB row only stores `trigger_type`, either (a) route calibration runs through a dedicated `trigger_type="calibration"` and reconstruct a `CalibrationRequest` here, or (b) keep calibration on a separate direct-dispatch path outside this queue. Pick (a) if calibration must share the concurrency cap; add a `trigger_type == "calibration"` branch in `_worker_loop` mirroring the old `run_calibration` call. Check `app/batch_api/calibration_runner.py` for the exact params before wiring.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/batch_api/test_priority_queue.py -v`
Expected: PASS

- [ ] **Step 5: Run existing queue + route suites for regressions**

Run: `pytest tests/batch_api -k "queue or route or shutdown" -v`
Expected: PASS (fix any test that asserted the old in-mem `resume_queued_jobs`/sentinel behavior — that contract is intentionally replaced by DB polling; update such tests to assert queued-row pickup instead).

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/queue_manager.py tests/batch_api/test_priority_queue.py
git commit -m "feat(e5.4): DB-polled priority queue replaces in-mem queue.Queue"
```

---

### Task 5: Wire priority at the two enqueue sites

**Files:**
- Modify: `app/batch_api/routes.py` (`/batch/start`, ~line 43)
- Modify: `app/batch_api/hot_folder.py` (`create_run`, ~line 186)
- Test: `tests/batch_api/test_priority_queue.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/batch_api/test_priority_queue.py  (append)
from app.core.config import PRIORITY_HIGH, PRIORITY_LOW


def test_api_submit_is_high_priority(monkeypatch):
    from app.core.db.schema import init_db
    from app.core.db.connection import get_connection
    from app.core.db.repositories.batch import BatchRepository
    init_db()
    seen = {}
    repo = BatchRepository()
    orig = repo.create_run

    def _spy(conn, **kw):
        seen["priority"] = kw.get("priority")
        return orig(conn, **kw)

    monkeypatch.setattr("app.batch_api.routes.repo.create_run", _spy)
    from fastapi.testclient import TestClient
    from app.batch_api.main import app
    with TestClient(app) as client:
        client.post("/api/v1/batch/start", json={
            "source_dir": "s", "target_dir": "t", "target_format": ["webp"],
            "tool": ["ffmpeg"], "category": ["general"], "trigger_type": "api",
        })
    assert seen.get("priority") == PRIORITY_HIGH
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/batch_api/test_priority_queue.py::test_api_submit_is_high_priority -v`
Expected: FAIL (`priority` not passed / not `PRIORITY_HIGH`).

- [ ] **Step 3: Set high priority in /batch/start**

In `app/batch_api/routes.py`, in `start_batch`'s `repo.create_run(...)` call (line 43), add `priority=PRIORITY_HIGH` and import it:

```python
from ..core.config import PRIORITY_HIGH
# ...
            run_id = repo.create_run(
                conn,
                source_dir=req.source_dir,
                target_dir=req.target_dir,
                target_format=",".join(req.target_format),
                tool=",".join([t.value for t in req.tool]),
                trigger_type=req.trigger_type,
                heuristic_version=orchestrator.interpolator.version,
                priority=PRIORITY_HIGH,
            )
```

- [ ] **Step 4: Set low priority in hot_folder.py**

In `app/batch_api/hot_folder.py`, in the `self.repo.create_run(...)` call (~line 186), add `priority=PRIORITY_LOW` and import `from ..core.config import ..., HOT_FOLDER_DEBOUNCE_MS, PRIORITY_LOW` (extend the existing config import).

```python
                    run_id = self.repo.create_run(
                        conn,
                        source_dir=self.config["source_dir"],
                        target_dir=self.config["target_dir"],
                        target_format=db_formats,
                        tool=db_tools,
                        trigger_type="hot_folder",
                        heuristic_version=self.orchestrator.interpolator.version,
                        priority=PRIORITY_LOW,
                    )
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/batch_api/test_priority_queue.py::test_api_submit_is_high_priority -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/routes.py app/batch_api/hot_folder.py tests/batch_api/test_priority_queue.py
git commit -m "feat(e5.4): GUI submit high-priority, hot-folder low-priority"
```

- [ ] **Step 7: Close e5.4**

```bash
bd close pixelpivot_batch-h53.4
```

---

## e5.1 — /metrics Prometheus (degradable)

### Task 6: Metrics module + `/metrics` endpoint

**Files:**
- Create: `app/batch_api/metrics.py`
- Modify: `app/batch_api/main.py`
- Test: `tests/batch_api/test_metrics.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/batch_api/test_metrics.py
from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.batch_api import metrics


def test_metrics_endpoint_scrapeable():
    with TestClient(app) as client:
        resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "pixelpivot_jobs_total" in resp.text


def test_record_job_increments_counter():
    metrics.record_job(status="completed", tool="ffmpeg", fmt="webp")
    text = metrics.render().decode()
    assert 'pixelpivot_jobs_total{' in text
    assert 'status="completed"' in text


def test_record_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(metrics, "_ENABLED", False)
    # Must not raise even though recording is a no-op.
    metrics.record_job(status="failed", tool="magick", fmt="avif")
    metrics.set_queue_depth(3)
    metrics.observe_compression_ratio(0.4)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/batch_api/test_metrics.py -v`
Expected: FAIL (module/route missing).

- [ ] **Step 3: Implement metrics.py**

```python
# app/batch_api/metrics.py
"""Prometheus metrics for PixelPivot. Degradable: recording no-ops when disabled
or when prometheus_client is unavailable (air-gapped host without the package).
"""
from __future__ import annotations

from ..core.config import METRICS_ENABLED
from ..core.logger import get_logger

log = get_logger(__name__)

_ENABLED = METRICS_ENABLED
_registry = None
_jobs = None
_processing = None
_queue_depth = None
_compression = None

try:
    if _ENABLED:
        from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry, generate_latest
        _registry = CollectorRegistry()
        _jobs = Counter("pixelpivot_jobs_total", "Batch conversions by outcome",
                        ["status", "tool", "format"], registry=_registry)
        _processing = Histogram("pixelpivot_processing_seconds", "Batch wall time (s)", registry=_registry)
        _queue_depth = Gauge("pixelpivot_queue_depth", "Queued batch runs", registry=_registry)
        _compression = Histogram("pixelpivot_compression_ratio", "output_bytes / input_bytes", registry=_registry)
        _generate_latest = generate_latest
except Exception as e:  # prometheus_client missing or import error
    log.warning("Metrics disabled (prometheus_client unavailable): %s", e)
    _ENABLED = False


def record_job(status: str, tool: str, fmt: str) -> None:
    if _ENABLED and _jobs is not None:
        _jobs.labels(status=status, tool=tool, format=fmt).inc()


def observe_processing_seconds(seconds: float) -> None:
    if _ENABLED and _processing is not None:
        _processing.observe(seconds)


def set_queue_depth(n: int) -> None:
    if _ENABLED and _queue_depth is not None:
        _queue_depth.set(n)


def observe_compression_ratio(ratio: float) -> None:
    if _ENABLED and _compression is not None:
        _compression.observe(ratio)


def render() -> bytes:
    """Return the Prometheus exposition payload (empty when disabled)."""
    if _ENABLED and _registry is not None:
        return _generate_latest(_registry)
    return b"# metrics disabled\n"
```

- [ ] **Step 4: Mount /metrics in main.py**

```python
from .metrics import render as render_metrics
from fastapi import Response


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus scrape endpoint. Tolerates no scraper; empty when disabled."""
    return Response(content=render_metrics(), media_type="text/plain; version=0.0.4")
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests/batch_api/test_metrics.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/metrics.py app/batch_api/main.py tests/batch_api/test_metrics.py
git commit -m "feat(e5.1): degradable /metrics endpoint + record helpers"
```

---

### Task 7: Instrument the orchestrator + queue depth

**Files:**
- Modify: `app/batch_api/orchestrator.py` (finalize block ~line 547-565)
- Modify: `app/batch_api/queue_manager.py` (`submit_batch` + worker loop)
- Test: `tests/batch_api/test_metrics.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/batch_api/test_metrics.py  (append)
def test_orchestrator_records_job_metrics(monkeypatch):
    from app.batch_api import metrics
    recorded = []
    monkeypatch.setattr(metrics, "record_job", lambda status, tool, fmt: recorded.append((status, tool, fmt)))
    from app.batch_api import orchestrator as orch_mod
    # Drive the small helper the orchestrator will call at finalize:
    orch_mod._emit_job_metrics(final_status="completed", executed_cells_tools=["ffmpeg"],
                               formats=["webp"], duration_s=1.2, savings_pct=60.0)
    assert ("completed", "ffmpeg", "webp") in recorded
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/batch_api/test_metrics.py::test_orchestrator_records_job_metrics -v`
Expected: FAIL (`_emit_job_metrics` missing).

- [ ] **Step 3: Add the emit helper + call it at finalize**

In `app/batch_api/orchestrator.py`, add a module-level helper near the top:

```python
def _emit_job_metrics(final_status, executed_cells_tools, formats, duration_s, savings_pct):
    """Record Prometheus counters for a finished batch (no-op when metrics off)."""
    from .metrics import record_job, observe_processing_seconds, observe_compression_ratio
    observe_processing_seconds(duration_s)
    # compression_ratio = output/input = (1 - savings/100)
    observe_compression_ratio(max(0.0, 1.0 - (savings_pct / 100.0)))
    for tool in set(executed_cells_tools):
        for fmt in set(formats):
            record_job(status=final_status, tool=tool, fmt=fmt)
```

In `execute_batch`'s finalize block, right after `final_status` is computed (~line 547) and the summary saved, call it:

```python
            _emit_job_metrics(
                final_status=final_status,
                executed_cells_tools=[c.tool for c in executed_cells],
                formats=[c.target_format for c in executed_cells],
                duration_s=duration_ms / 1000.0,
                savings_pct=metrics.get("savings_pct", 0.0),
            )
```

> `metrics` here is the local dict from `MetricsCollector.collect` — do not shadow it with the module. Import the module inside `_emit_job_metrics` only (as written) to avoid the name clash.

- [ ] **Step 4: Set queue depth gauge in queue_manager**

In `app/batch_api/queue_manager.py`, add a small depth refresher. In `submit_batch`/`submit_calibration` after setting `queued`, and at the top of each `_worker_loop` iteration, call:

```python
    def _refresh_queue_depth(self) -> None:
        try:
            from .metrics import set_queue_depth
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) AS n FROM batch_runs WHERE status = 'queued'")
                set_queue_depth(int(cur.fetchone()["n"]))
        except Exception:
            pass
```

Call `self._refresh_queue_depth()` after each `update_status(..., "queued")` and once per poll iteration in `_worker_loop`.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/batch_api/test_metrics.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/orchestrator.py app/batch_api/queue_manager.py tests/batch_api/test_metrics.py
git commit -m "feat(e5.1): instrument orchestrator jobs/ratio + queue_depth gauge"
```

- [ ] **Step 7: Close e5.1**

```bash
bd close pixelpivot_batch-h53.1
```

---

## e5.2 — Resource-aware chunk sizing

### Task 8: Pure `dynamic_max_files` + wire into ffmpeg batch path

**Files:**
- Create: `app/core/converters/chunk_sizing.py`
- Modify: `app/core/converters/ffmpeg_converter.py` (multi-IO batch path where `pack_chunks(..., max_files=FFMPEG_BATCH_MAX_FILES, ...)` is called)
- Test: `tests/converters/test_chunk_sizing.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/converters/test_chunk_sizing.py
from app.core.converters.chunk_sizing import dynamic_max_files


def test_higher_mp_gives_smaller_chunk_same_budget():
    budget = 1_000_000_000  # 1 GB
    small = dynamic_max_files(megapixels=1.0, ram_budget_bytes=budget, ceiling=20)
    large = dynamic_max_files(megapixels=25.0, ram_budget_bytes=budget, ceiling=20)
    assert large < small


def test_never_exceeds_ceiling():
    huge_budget = 10**12
    assert dynamic_max_files(megapixels=0.1, ram_budget_bytes=huge_budget, ceiling=20) == 20


def test_never_below_one():
    assert dynamic_max_files(megapixels=500.0, ram_budget_bytes=1, ceiling=20) == 1


def test_formula_matches_4x_rgba():
    # peak RAM ~= 4 * megapixels * 1e6 bytes per in-flight image.
    # budget for exactly 4 images of 10 MP: 4 * (4 * 10 * 1e6) = 160 MB
    budget = 4 * (4 * 10 * 1_000_000)
    assert dynamic_max_files(megapixels=10.0, ram_budget_bytes=budget, ceiling=20) == 4
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/converters/test_chunk_sizing.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement chunk_sizing.py**

```python
# app/core/converters/chunk_sizing.py
"""Pure resource-aware chunk sizing (e5.2).

Deterministic model: a decoded image costs ~4 bytes/pixel (raw RGBA), so an
in-flight chunk of N images at M megapixels needs ~= 4 * M * 1e6 * N bytes. Given
a RAM budget, the max chunk size is budget / (4 * M * 1e6), clamped to [1, ceiling].
"""
from __future__ import annotations

import math

_BYTES_PER_MP = 4 * 1_000_000  # 4 bytes/pixel * 1e6 pixels per megapixel


def dynamic_max_files(megapixels: float, ram_budget_bytes: float, ceiling: int) -> int:
    """Return the RAM-bounded max files per chunk, clamped to [1, ceiling]."""
    if megapixels <= 0:
        return ceiling
    per_image = _BYTES_PER_MP * megapixels
    fit = int(math.floor(ram_budget_bytes / per_image))
    return max(1, min(ceiling, fit))
```

- [ ] **Step 4: Wire into the ffmpeg batch path**

In `app/core/converters/ffmpeg_converter.py`, at the multi-input/multi-output chunking call site (where `pack_chunks(pairs, max_files=FFMPEG_BATCH_MAX_FILES, max_cmdline_bytes=FFMPEG_BATCH_MAX_CMDLINE_BYTES, ...)` is invoked), compute a dynamic ceiling first. The sub-group already has a known `(W, H)`:

```python
from ..config import CHUNK_RAM_BUDGET_FRACTION, FFMPEG_BATCH_MAX_FILES
from .chunk_sizing import dynamic_max_files
import psutil

# megapixels for this uniform sub-group:
mp = (w * h) / 1_000_000.0
ram_budget = psutil.virtual_memory().available * CHUNK_RAM_BUDGET_FRACTION
max_files = dynamic_max_files(mp, ram_budget, ceiling=FFMPEG_BATCH_MAX_FILES)
chunks = pack_chunks(pairs, max_files=max_files,
                     max_cmdline_bytes=FFMPEG_BATCH_MAX_CMDLINE_BYTES)
```

> Read the exact `pack_chunks` call site in `ffmpeg_converter.py` first (there may be more than one — the multi-IO chunked path, not the `image2` demuxer path). Apply only where a per-file `max_files` bound is used. `w, h` come from the sub-group's dimension key.

- [ ] **Step 5: Run tests + ffmpeg converter suite**

Run: `pytest tests/converters/test_chunk_sizing.py tests/converters -k "ffmpeg or chunk" -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/core/converters/chunk_sizing.py app/core/converters/ffmpeg_converter.py tests/converters/test_chunk_sizing.py
git commit -m "feat(e5.2): RAM-aware dynamic chunk sizing bounded by ceiling"
```

- [ ] **Step 7: Close e5.2**

```bash
bd close pixelpivot_batch-h53.2
```

---

## e5.3 — Disk-% backpressure

### Task 9: `disk_pct_over_threshold` probe (worker pause already wired in Task 4)

**Files:**
- Modify: `app/batch_api/image_guards.py`
- Test: `tests/batch_api/test_disk_backpressure.py` (create)

The worker-side pause loop (`_disk_backpressure_wait`) was added in Task 4; this task supplies the probe it calls.

- [ ] **Step 1: Write the failing tests**

```python
# tests/batch_api/test_disk_backpressure.py
from app.batch_api.image_guards import disk_pct_over_threshold
from app.batch_api import image_guards
import collections


def test_over_threshold_true_when_full(monkeypatch):
    Usage = collections.namedtuple("Usage", "total used free")
    # 95% used
    monkeypatch.setattr(image_guards.shutil, "disk_usage", lambda p: Usage(100, 95, 5))
    assert disk_pct_over_threshold("/some/target", 90.0) is True


def test_under_threshold_false(monkeypatch):
    Usage = collections.namedtuple("Usage", "total used free")
    monkeypatch.setattr(image_guards.shutil, "disk_usage", lambda p: Usage(100, 50, 50))
    assert disk_pct_over_threshold("/some/target", 90.0) is False


def test_probes_resolved_target_not_root(monkeypatch):
    seen = {}
    Usage = collections.namedtuple("Usage", "total used free")
    def _fake(path):
        seen["path"] = path
        return Usage(100, 10, 90)
    monkeypatch.setattr(image_guards.shutil, "disk_usage", _fake)
    disk_pct_over_threshold("D:/mount/out", 90.0)
    import os
    assert seen["path"] == os.path.abspath("D:/mount/out")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/batch_api/test_disk_backpressure.py -v`
Expected: FAIL (`disk_pct_over_threshold` missing).

- [ ] **Step 3: Implement the probe**

In `app/batch_api/image_guards.py` (ensure `import shutil, os` present):

```python
def disk_pct_over_threshold(target_dir: str, threshold_pct: float) -> bool:
    """True when the RESOLVED target_dir volume is at/above threshold_pct full.

    Probes shutil.disk_usage(os.path.abspath(target_dir)) so it reads the volume
    the outputs actually land on (a network mount / external drive / separate
    logical volume), never a static system root like C:/ or /.
    """
    try:
        usage = shutil.disk_usage(os.path.abspath(target_dir))
    except OSError:
        return False  # can't probe -> do not block pickup
    if usage.total <= 0:
        return False
    used_pct = 100.0 * usage.used / usage.total
    return used_pct >= threshold_pct
```

- [ ] **Step 4: Write the pause-resume integration test**

```python
# tests/batch_api/test_disk_backpressure.py  (append)
import time, threading
from app.batch_api.queue_manager import BatchQueueManager


class _Orch:
    def __init__(self):
        self.run_controls = {}


def test_worker_pauses_until_disk_frees(monkeypatch):
    from app.batch_api import queue_manager as qm_mod
    states = iter([True, True, False])  # over, over, then freed
    monkeypatch.setattr(qm_mod, "DISK_BACKPRESSURE_POLL_S", 0.01)
    monkeypatch.setattr("app.batch_api.image_guards.disk_pct_over_threshold",
                        lambda target, pct: next(states, False))
    qm = BatchQueueManager(_Orch(), max_workers=1)
    start = time.time()
    qm._disk_backpressure_wait("D:/out")  # returns once the iterator yields False
    assert time.time() - start >= 0.02  # waited at least two poll cycles
```

- [ ] **Step 5: Run to verify all pass**

Run: `pytest tests/batch_api/test_disk_backpressure.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/image_guards.py tests/batch_api/test_disk_backpressure.py
git commit -m "feat(e5.3): disk-% backpressure probe on resolved target volume"
```

- [ ] **Step 7: Close e5.3**

```bash
bd close pixelpivot_batch-h53.3
```

---

## e5.5 — OpenTelemetry spans (optional)

### Task 10: Lazy span contextmanager + instrument three sites

**Files:**
- Create: `app/core/otel.py`
- Modify: `app/core/heuristic_interpolator.py` (quality-curve calc), `app/core/converters/ffmpeg_batch_helpers.py` (staging), `app/core/converters/base.py` (backend exec)
- Test: `tests/core/test_otel.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_otel.py
import sys
import importlib


def test_span_is_noop_and_does_not_import_otel_when_disabled(monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_OTEL_ENABLED", "0")
    # Fresh import so the flag is read now.
    for m in [m for m in list(sys.modules) if m.startswith("app.core.otel")]:
        del sys.modules[m]
    import app.core.otel as otel
    importlib.reload(otel)
    with otel.span("quality_curve"):
        pass
    # Zero-overhead contract: opentelemetry must not have been imported.
    assert not any(m == "opentelemetry" or m.startswith("opentelemetry.") for m in sys.modules)


def test_span_yields_when_enabled_but_sdk_absent(monkeypatch):
    # Even if enabled, a missing SDK must degrade to a no-op (air-gapped host).
    monkeypatch.setenv("PIXELPIVOT_OTEL_ENABLED", "1")
    for m in [m for m in list(sys.modules) if m.startswith("app.core.otel")]:
        del sys.modules[m]
    import app.core.otel as otel
    importlib.reload(otel)
    with otel.span("staging"):
        pass  # must not raise regardless of SDK availability
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/core/test_otel.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement otel.py**

```python
# app/core/otel.py
"""Optional OpenTelemetry spans (e5.5).

Default off (PIXELPIVOT_OTEL_ENABLED=0): span() is a zero-overhead no-op and the
opentelemetry package is never imported. When enabled, the SDK/tracer is imported
lazily on first use; if the package is unavailable, it degrades back to a no-op.
"""
from __future__ import annotations

import os
from contextlib import contextmanager

_ENABLED = os.getenv("PIXELPIVOT_OTEL_ENABLED", "0") not in ("0", "false", "False")
_tracer = None
_init_failed = False


def _get_tracer():
    global _tracer, _init_failed
    if _tracer is not None or _init_failed:
        return _tracer
    try:
        from opentelemetry import trace  # imported only when enabled + first use
        _tracer = trace.get_tracer("pixelpivot")
    except Exception:
        _init_failed = True
        _tracer = None
    return _tracer


@contextmanager
def span(name: str):
    """Enter a tracing span named `name`, or a no-op context when disabled/unavailable."""
    if not _ENABLED:
        yield None
        return
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as s:
        yield s
```

- [ ] **Step 4: Instrument the three sites**

- Quality-curve calc — `app/core/heuristic_interpolator.py`, wrap the body of `get_interpolated_quality`:

```python
from .otel import span
# ...
    def get_interpolated_quality(self, ...):
        with span("quality_curve"):
            # existing body unchanged
```

- Staging — `app/core/converters/ffmpeg_batch_helpers.py`, wrap the hardlink/staging contextmanager body (the `image2` staging path):

```python
from ..otel import span
# inside the staging helper:
        with span("staging"):
            # existing hardlink/copy staging body
```

- Backend exec — `app/core/converters/base.py`, wrap the `subprocess.Popen`/communicate region in `_run_subprocess`:

```python
from ..otel import span
# ...
        with span("backend_exec"):
            with subprocess.Popen(...) as proc:
                # existing body (already brackets register/unregister from E4)
```

> Keep each existing body verbatim; only add the `with span(...):` wrapper (indent one level). The no-op path adds one attribute check per call when disabled.

- [ ] **Step 5: Run tests + smoke the wrapped modules**

Run: `pytest tests/core/test_otel.py -v`
Expected: PASS

Run: `pytest tests/test_base_converter.py -k interpolator -v` (or the interpolator suite) and `pytest tests/converters -k ffmpeg -v`
Expected: PASS (spans transparent when disabled).

- [ ] **Step 6: Commit**

```bash
git add app/core/otel.py app/core/heuristic_interpolator.py app/core/converters/ffmpeg_batch_helpers.py app/core/converters/base.py tests/core/test_otel.py
git commit -m "feat(e5.5): optional lazy OpenTelemetry spans (zero-overhead when off)"
```

- [ ] **Step 7: Close e5.5 and the epic**

```bash
bd close pixelpivot_batch-h53.5
bd close pixelpivot_batch-h53
bd dolt push
```

---

## Task 11: Full-suite verification

- [ ] **Step 1: Run the whole suite**

Run: `pytest`
Expected: PASS. Investigate any failure that touched the queue rewrite (Task 4) — the DB-poll model intentionally replaces `resume_queued_jobs`/sentinel semantics; update stale tests to assert queued-row pickup.

- [ ] **Step 2: Manual scrape smoke (optional, non-air-gapped)**

Run the API and `curl localhost:8000/metrics`; submit a batch; confirm `pixelpivot_jobs_total` and `pixelpivot_queue_depth` move.

- [ ] **Step 3: Final commit if any test fixups were needed**

```bash
git add -A
git commit -m "test(e5): reconcile suite with DB-polled queue + telemetry"
```

---

## Self-Review

**1. Spec coverage:**
- e5.1 metrics (jobs_total{status,tool,format}, processing_seconds, queue_depth, compression_ratio, `PIXELPIVOT_METRICS_ENABLED`, tolerates no scraper) -> Tasks 6-7. COVERED.
- e5.2 chunk sizing (peak RAM ~= 4*MP*chunk_size, bounded by `FFMPEG_BATCH_MAX_*`) -> Task 8 (`dynamic_max_files`, `test_formula_matches_4x_rgba`, `test_never_exceeds_ceiling`). COVERED.
- e5.3 disk backpressure (`shutil.disk_usage(os.path.abspath(target_dir))`, pause/resume, target volume not root) -> Task 9 + worker pause from Task 4. COVERED.
- e5.4 priority lanes (DB-driven, `priority` column, `ORDER BY priority DESC, created_at ASC`, crash-resilient, sqlite/postgres) -> Tasks 2-5. COVERED. Arch change (in-mem queue removed) explicit in Task 4.
- e5.5 OTel (`PIXELPIVOT_OTEL_ENABLED=0` default, lazy import, spans on quality-curve/staging/backend-exec, zero overhead when off) -> Task 10 (`test_span_is_noop_and_does_not_import_otel_when_disabled`). COVERED.

**2. Placeholder scan:** No TBD/TODO. Two "read the exact call site first" notes (Task 4 calibration branch, Task 8 pack_chunks site) are grounding caveats, not placeholders — the code to add is fully specified; the note only says where to place it.

**3. Type consistency:** `dynamic_max_files(megapixels, ram_budget_bytes, ceiling) -> int` consistent Task 8. `claim_next_queued(get_conn) -> Optional[dict]` consistent Tasks 3-4. `record_job(status, tool, fmt)` / `set_queue_depth(int)` / `observe_compression_ratio(float)` consistent Tasks 6-7. `disk_pct_over_threshold(target_dir, threshold_pct) -> bool` consistent Tasks 4/9. `span(name)` contextmanager consistent Task 10. `create_run(..., priority=0)` consistent Tasks 3/5.

**Flagged for the implementer:**
- Task 4: decide calibration routing (trigger_type branch vs separate path) — read `calibration_runner.py` signature first. The old `resume_queued_jobs` is intentionally gone; delete its tests or repoint them at DB pickup.
- Task 7: do not shadow the local `metrics` dict in `execute_batch` with the `metrics` module — import the module only inside `_emit_job_metrics`.
- Task 8: apply dynamic sizing only to the multi-IO chunked path, not the `image2` demuxer path; confirm the sub-group `(w, h)` variables in scope.
- Verify `Tool` enum member spelling in `models.py` before the priority-queue tests.

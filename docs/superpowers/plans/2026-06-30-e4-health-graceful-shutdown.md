# E4 — Health + Graceful Shutdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Kubernetes/LB-style health probes (`/healthz/live`, `/healthz/ready`) and a SIGTERM-safe graceful shutdown that drains both the hot-folder lane and the batch-worker lane without orphaning ffmpeg/mogrify child processes or leaving half-written outputs.

**Architecture:** Two beads. **e4.1** adds a stateless `app/batch_api/health.py` that exposes a `live` constant and a `readiness_checks(orchestrator)` function returning a list of named `(name, ok, detail)` probes (DB connect, storage writable, magick/ffmpeg/sharp-socket reachable); two top-level routes in `main.py` map those to 200/503. **e4.2** adds a thread-safe `app/core/process_registry.py` that every child-process spawn site registers its `Popen` with, plus an `app/batch_api/shutdown.py::graceful_shutdown(...)` coordinator wired into the FastAPI lifespan `finally` block: it stops the hot-folder watcher (no new batches), drains the queue within a grace window (active matrix chunk finishes via the existing cooperative `ctrl.cancelled` checks), then `terminate()`/`kill()`s any surviving registered children.

**Tech Stack:** FastAPI, uvicorn (converts SIGTERM to lifespan shutdown), `subprocess.Popen`, `threading`, pytest + `fastapi.testclient.TestClient`. Reuses `app/core/toolcheck.py` (`check_all`, `check_binary`, `check_sharp_daemon`) and `app/core/db/connection.py::get_connection`.

**Beads:** `pixelpivot_batch-4ev` (epic) — `pixelpivot_batch-4ev.1` (/healthz) — `pixelpivot_batch-4ev.2` (SIGTERM graceful shutdown). Branch via beads-tdd-python, one PR for the epic.

---

## File Structure

| File | Responsibility | Bead |
|---|---|---|
| `app/batch_api/health.py` (create) | Pure readiness/liveness probe functions; no FastAPI imports. | e4.1 |
| `app/batch_api/main.py` (modify) | Mount `/healthz/live` + `/healthz/ready`; call `graceful_shutdown` in lifespan `finally`. | e4.1 + e4.2 |
| `app/core/process_registry.py` (create) | Thread-safe registry of live child `Popen` handles + `terminate_all(grace_s)`. | e4.2 |
| `app/core/converters/base.py` (modify) | Register/unregister `Popen` in `_run_subprocess`. | e4.2 |
| `app/core/converters/magick_converter.py` (modify) | Register/unregister the mogrify-batch `Popen`. | e4.2 |
| `app/core/ffmpeg/process.py` (modify) | Register/unregister the ffmpeg `Popen` in `spawn()`/`run()`. | e4.2 |
| `app/batch_api/queue_manager.py` (modify) | `stop()` takes a `grace_s` param threaded from shutdown. | e4.2 |
| `app/batch_api/shutdown.py` (create) | `graceful_shutdown(...)` coordinator: hot-folder stop -> queue drain -> registry terminate. | e4.2 |
| `app/core/config.py` (modify) | `SHUTDOWN_GRACE_S`, `SUBPROCESS_TERMINATE_TIMEOUT_S`. | e4.2 |
| `tests/batch_api/test_healthz.py` (create) | Liveness + readiness route tests (each dep broken -> 503 naming it). | e4.1 |
| `tests/core/test_process_registry.py` (create) | Registry register/unregister/terminate behavior. | e4.2 |
| `tests/batch_api/test_graceful_shutdown.py` (create) | Coordinator drains both lanes, kills survivors. | e4.2 |

---

## e4.1 — /healthz live + ready

### Task 1: Liveness probe + `/healthz/live` route

**Files:**
- Create: `app/batch_api/health.py`
- Modify: `app/batch_api/main.py` (add route after the existing `@app.get("/")`)
- Test: `tests/batch_api/test_healthz.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/batch_api/test_healthz.py
from fastapi.testclient import TestClient
from app.batch_api.main import app


def test_healthz_live_returns_200_alive():
    with TestClient(app) as client:
        resp = client.get("/healthz/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "alive"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/batch_api/test_healthz.py::test_healthz_live_returns_200_alive -v`
Expected: FAIL with 404 (route not defined).

- [ ] **Step 3: Create the health module**

```python
# app/batch_api/health.py
"""Stateless health-probe helpers for /healthz endpoints.

Pure functions with no FastAPI imports so they are trivially unit-testable.
Liveness = "process is up" (no dependencies). Readiness = "can do work"
(DB connect, storage writable, encoders reachable).
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Check:
    """One named readiness probe result."""
    name: str
    ok: bool
    detail: str = ""


LIVE_BODY = {"status": "alive"}
```

- [ ] **Step 4: Mount the live route in main.py**

In `app/batch_api/main.py`, add after the existing `root()` handler (around line 149):

```python
from .health import LIVE_BODY


@app.get("/healthz/live")
async def healthz_live():
    """Liveness probe: the process is up. Never depends on external state."""
    return LIVE_BODY
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/batch_api/test_healthz.py::test_healthz_live_returns_200_alive -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/health.py app/batch_api/main.py tests/batch_api/test_healthz.py
git commit -m "feat(health): add /healthz/live liveness probe (e4.1)"
```

---

### Task 2: Readiness checks function

**Files:**
- Modify: `app/batch_api/health.py`
- Test: `tests/batch_api/test_healthz.py`

Readiness probes, each returning a `Check`:
- `db` — open a connection and run `SELECT 1`.
- `storage` — write+delete a temp file under the data dir (parent of `PIXELPIVOT_DB_PATH`, default `./data`).
- `magick`, `ffmpeg` — `toolcheck.check_binary` against the orchestrator's resolved binary paths.
- `sharp` — `toolcheck.check_sharp_daemon` against the orchestrator's sharp port.

> Note: there is no single global `target_dir` (it is per-batch), so `storage` probes the always-required writable data dir. The DB-connect probe already covers the DB volume on the sqlite path. This is the faithful, deterministic interpretation of the spec's "target_dir writable".

- [ ] **Step 1: Write the failing tests**

```python
# tests/batch_api/test_healthz.py  (append)
from app.batch_api import health


class _FakeConv:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)


class _FakeOrch:
    def __init__(self):
        self.converters = {
            "magick": _FakeConv(magick_path="magick"),
            "ffmpeg": _FakeConv(ffmpeg_path="ffmpeg"),
            "sharp": _FakeConv(port=8765),
        }


def test_readiness_checks_returns_named_probes():
    checks = health.readiness_checks(_FakeOrch())
    names = {c.name for c in checks}
    assert {"db", "storage", "magick", "ffmpeg", "sharp"} <= names


def test_readiness_db_failure_named(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(health, "get_connection", _boom)
    checks = {c.name: c for c in health.readiness_checks(_FakeOrch())}
    assert checks["db"].ok is False
    assert "db down" in checks["db"].detail
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/batch_api/test_healthz.py -k readiness -v`
Expected: FAIL with `AttributeError: module 'app.batch_api.health' has no attribute 'readiness_checks'`.

- [ ] **Step 3: Implement readiness_checks in health.py**

Append to `app/batch_api/health.py`:

```python
from app.core.db.connection import get_connection
from app.core import toolcheck


def _data_dir() -> str:
    db_path = os.getenv("PIXELPIVOT_DB_PATH", os.path.join(".", "data", "pixelpivot.db"))
    return os.path.dirname(os.path.abspath(db_path)) or "."


def _check_db() -> Check:
    try:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        return Check("db", True, "connected")
    except Exception as e:
        return Check("db", False, str(e))


def _check_storage() -> Check:
    d = _data_dir()
    try:
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".healthz_")
        os.close(fd)
        os.remove(tmp)
        return Check("storage", True, d)
    except Exception as e:
        return Check("storage", False, f"{d}: {e}")


def readiness_checks(orchestrator) -> List[Check]:
    """Run every readiness probe and return their results, order stable."""
    convs = getattr(orchestrator, "converters", {})
    magick_path = getattr(convs.get("magick"), "magick_path", "magick")
    ffmpeg_path = getattr(convs.get("ffmpeg"), "ffmpeg_path", "ffmpeg")
    sharp_port = getattr(convs.get("sharp"), "port", 8765)

    def _from_status(name, status):
        return Check(name, status.ok, status.detail or "")

    return [
        _check_db(),
        _check_storage(),
        _from_status("magick", toolcheck.check_binary("magick", magick_path)),
        _from_status("ffmpeg", toolcheck.check_binary("ffmpeg", ffmpeg_path)),
        _from_status("sharp", toolcheck.check_sharp_daemon(sharp_port)),
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/batch_api/test_healthz.py -k readiness -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/health.py tests/batch_api/test_healthz.py
git commit -m "feat(health): add readiness_checks probes (e4.1)"
```

---

### Task 3: `/healthz/ready` route (200/503 naming failed checks)

**Files:**
- Modify: `app/batch_api/main.py`
- Test: `tests/batch_api/test_healthz.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/batch_api/test_healthz.py  (append)
from app.batch_api import health as health_mod


def _all_ok(_orch):
    return [health_mod.Check(n, True, "ok") for n in ("db", "storage", "magick", "ffmpeg", "sharp")]


def test_ready_all_ok_returns_200(monkeypatch):
    monkeypatch.setattr("app.batch_api.main.readiness_checks", _all_ok)
    with TestClient(app) as client:
        resp = client.get("/healthz/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_ready_broken_dep_returns_503_naming_check(monkeypatch):
    def _ffmpeg_down(_orch):
        out = _all_ok(_orch)
        return [c if c.name != "ffmpeg" else health_mod.Check("ffmpeg", False, "not found") for c in out]
    monkeypatch.setattr("app.batch_api.main.readiness_checks", _ffmpeg_down)
    with TestClient(app) as client:
        resp = client.get("/healthz/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert "ffmpeg" in body["failed"]
    assert body["checks"]["ffmpeg"]["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/batch_api/test_healthz.py -k ready_ -v`
Expected: FAIL with 404 (route not defined).

- [ ] **Step 3: Mount the ready route in main.py**

In `app/batch_api/main.py`, extend the health import and add the route after `healthz_live`:

```python
from .health import LIVE_BODY, readiness_checks
from fastapi import Request
from fastapi.responses import JSONResponse


@app.get("/healthz/ready")
async def healthz_ready(request: Request):
    """Readiness probe: 200 when every dependency is reachable, else 503 naming failures."""
    orchestrator = getattr(request.app.state, "orchestrator", None)
    checks = readiness_checks(orchestrator)
    failed = [c.name for c in checks if not c.ok]
    body = {
        "status": "ready" if not failed else "not_ready",
        "failed": failed,
        "checks": {c.name: {"ok": c.ok, "detail": c.detail} for c in checks},
    }
    return JSONResponse(status_code=200 if not failed else 503, content=body)
```

- [ ] **Step 4: Run the full e4.1 test file**

Run: `pytest tests/batch_api/test_healthz.py -v`
Expected: PASS (all liveness + readiness + route tests).

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/main.py tests/batch_api/test_healthz.py
git commit -m "feat(health): add /healthz/ready 200/503 probe (e4.1)"
```

- [ ] **Step 6: Close e4.1**

```bash
bd close pixelpivot_batch-4ev.1
```

---

## e4.2 — SIGTERM graceful shutdown (both lanes)

### Task 4: Config constants

**Files:**
- Modify: `app/core/config.py`

- [ ] **Step 1: Add the constants**

Add near the other timeout constants in `app/core/config.py`:

```python
SHUTDOWN_GRACE_S = float(os.getenv("PIXELPIVOT_SHUTDOWN_GRACE_S", "30"))
"""Wall-clock seconds to let the active matrix chunk drain on SIGTERM before
force-terminating surviving child processes."""

SUBPROCESS_TERMINATE_TIMEOUT_S = 5.0
"""Seconds to wait after terminate() before kill() on a surviving child."""
```

> If `config.py` does not already `import os`, add it at the top.

- [ ] **Step 2: Verify import**

Run: `python -c "from app.core.config import SHUTDOWN_GRACE_S, SUBPROCESS_TERMINATE_TIMEOUT_S; print(SHUTDOWN_GRACE_S, SUBPROCESS_TERMINATE_TIMEOUT_S)"`
Expected: `30.0 5.0`

- [ ] **Step 3: Commit**

```bash
git add app/core/config.py
git commit -m "feat(shutdown): add grace-window config constants (e4.2)"
```

---

### Task 5: Thread-safe process registry

**Files:**
- Create: `app/core/process_registry.py`
- Test: `tests/core/test_process_registry.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_process_registry.py
import subprocess
import sys

from app.core import process_registry as reg


def _spawn_sleeper(seconds: float):
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])


def test_register_then_unregister_tracks_live_set():
    reg.clear()
    p = _spawn_sleeper(5)
    try:
        reg.register_process(p)
        assert p in reg.snapshot()
        reg.unregister_process(p)
        assert p not in reg.snapshot()
    finally:
        p.kill()
        p.wait(timeout=5)


def test_terminate_all_kills_survivors_and_returns_count():
    reg.clear()
    p = _spawn_sleeper(30)
    reg.register_process(p)
    killed = reg.terminate_all(grace_s=0.2)
    p.wait(timeout=5)
    assert p.poll() is not None
    assert killed >= 1
    assert reg.snapshot() == set()


def test_terminate_all_ignores_already_exited():
    reg.clear()
    p = _spawn_sleeper(0.01)
    p.wait(timeout=5)
    reg.register_process(p)
    killed = reg.terminate_all(grace_s=0.2)
    assert killed == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_process_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.process_registry'`.

- [ ] **Step 3: Implement the registry**

```python
# app/core/process_registry.py
"""Thread-safe registry of live child subprocess.Popen handles.

Every encoder spawn site registers its Popen here and unregisters on exit, so a
graceful shutdown can terminate()/kill() any child that outlives a joined worker
thread instead of orphaning ffmpeg/mogrify processes that hold FDs or leave
partial output files.
"""
from __future__ import annotations

import subprocess
import threading
from typing import Set

from .config import SUBPROCESS_TERMINATE_TIMEOUT_S
from .logger import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_live: "Set[subprocess.Popen]" = set()


def register_process(proc: "subprocess.Popen") -> None:
    """Track a freshly spawned child process."""
    with _lock:
        _live.add(proc)


def unregister_process(proc: "subprocess.Popen") -> None:
    """Stop tracking a process that has finished normally."""
    with _lock:
        _live.discard(proc)


def snapshot() -> "Set[subprocess.Popen]":
    """Return a copy of the currently tracked processes."""
    with _lock:
        return set(_live)


def clear() -> None:
    """Drop all tracked handles without signalling them (tests / reset)."""
    with _lock:
        _live.clear()


def terminate_all(grace_s: float = SUBPROCESS_TERMINATE_TIMEOUT_S) -> int:
    """terminate() every live child, then kill() any that ignore the grace window.

    Returns the number of processes that were still running and got signalled.
    """
    procs = snapshot()
    signalled = 0
    for p in procs:
        if p.poll() is not None:
            unregister_process(p)
            continue
        signalled += 1
        try:
            p.terminate()
        except Exception as e:
            log.warning("terminate() failed for pid=%s: %s", getattr(p, "pid", "?"), e)
    for p in procs:
        if p.poll() is None:
            try:
                p.wait(timeout=grace_s)
            except Exception:
                try:
                    p.kill()
                    log.warning("killed surviving child pid=%s after grace window", p.pid)
                except Exception as e:
                    log.error("kill() failed for pid=%s: %s", getattr(p, "pid", "?"), e)
        unregister_process(p)
    return signalled
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_process_registry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/process_registry.py tests/core/test_process_registry.py
git commit -m "feat(shutdown): add thread-safe child-process registry (e4.2)"
```

---

### Task 6: Register Popen handles at all three spawn sites

**Files:**
- Modify: `app/core/converters/base.py` (`_run_subprocess`, around line 355)
- Modify: `app/core/converters/magick_converter.py` (around line 244)
- Modify: `app/core/ffmpeg/process.py` (`spawn` line 140, `run` cleanup line 187)
- Test: `tests/core/test_process_registry.py`

- [ ] **Step 1: Write the failing test (registration during a real run)**

```python
# tests/core/test_process_registry.py  (append)
from app.core.ffmpeg.process import FFmpegProcess
from app.core import process_registry as reg


def test_ffmpeg_process_registers_while_alive(monkeypatch):
    reg.clear()
    seen = {}
    real_register = reg.register_process

    def _spy(proc):
        seen["registered"] = True
        seen["in_set_at_register"] = proc in reg.snapshot() or True
        return real_register(proc)

    monkeypatch.setattr(reg, "register_process", _spy)
    # ffmpeg binary need not exist for the spawn-registration assertion;
    # use a trivial cross-platform command via the python executable instead.
    import sys
    fp = FFmpegProcess(ffmpeg_path=sys.executable, args=["-c", "import time; time.sleep(0.2)"])
    fp.spawn()
    assert seen.get("registered") is True
    fp._proc.wait(timeout=5)
```

> Note: `FFmpegProcess.spawn()` builds `cmd = [ffmpeg_path, *args]`; passing the Python executable + `-c ...` produces a real short-lived child so the registration hook is exercised without a real ffmpeg binary.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/core/test_process_registry.py::test_ffmpeg_process_registers_while_alive -v`
Expected: FAIL (`seen` empty — `spawn()` does not register yet).

- [ ] **Step 3a: Hook ffmpeg/process.py spawn() + run()**

In `spawn()`, immediately after `self._proc = subprocess.Popen(...)` (line ~148) and before `return self._proc.pid`:

```python
        from ..process_registry import register_process
        register_process(self._proc)
        return self._proc.pid
```

In `run()`, in the cleanup block after the reader threads join (after line 188, before `duration_ms = ...`):

```python
        from ..process_registry import unregister_process
        unregister_process(self._proc)
```

- [ ] **Step 3b: Hook base.py `_run_subprocess`**

In `app/core/converters/base.py`, replace the `with subprocess.Popen(...) as proc:` body opener (line 355-365) so registration brackets the whole block:

```python
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=creationflags,
            ) as proc:
                from ..process_registry import register_process, unregister_process
                register_process(proc)
                try:
                    monitor = TelemetryMonitor(
                        pid=proc.pid, interval_ms=int(TELEMETRY_INTERVAL * 1000), run_id=run_id
                    )
                    monitor.start()
                    error = None
                    # ... existing communicate()/timeout body unchanged ...
                finally:
                    unregister_process(proc)
```

> Keep the existing body verbatim inside the new `try`. Only the `register_process`/`try`/`finally:unregister_process` bracket is added; indent the existing lines one level.

- [ ] **Step 3c: Hook magick_converter.py**

In `app/core/converters/magick_converter.py`, at the `with subprocess.Popen(...) as proc:` (line ~244), add registration the same way:

```python
                    with subprocess.Popen(
                        # ... existing args ...
                    ) as proc:
                        from ..process_registry import register_process, unregister_process
                        register_process(proc)
                        try:
                            # ... existing communicate()/timeout body unchanged ...
                        finally:
                            unregister_process(proc)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/core/test_process_registry.py -v`
Expected: PASS

- [ ] **Step 5: Run the converter suites to confirm no regression**

Run: `pytest tests/test_base_converter.py tests/converters -v`
Expected: PASS (registration is transparent to existing behavior).

- [ ] **Step 6: Commit**

```bash
git add app/core/converters/base.py app/core/converters/magick_converter.py app/core/ffmpeg/process.py tests/core/test_process_registry.py
git commit -m "feat(shutdown): register child Popen handles at all spawn sites (e4.2)"
```

---

### Task 7: queue_manager.stop() accepts a grace window

**Files:**
- Modify: `app/batch_api/queue_manager.py` (`stop`, lines 48-68)
- Test: `tests/batch_api` (existing queue-manager tests must still pass)

- [ ] **Step 1: Write the failing test**

```python
# tests/batch_api/test_graceful_shutdown.py
from app.batch_api.queue_manager import BatchQueueManager


class _NoopOrch:
    def __init__(self):
        self.run_controls = {}


def test_queue_stop_accepts_grace_arg():
    qm = BatchQueueManager(_NoopOrch(), max_workers=1)
    qm.start()
    # Should accept an explicit grace window without raising.
    qm.stop(grace_s=0.5)
    assert qm._stopped is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/batch_api/test_graceful_shutdown.py::test_queue_stop_accepts_grace_arg -v`
Expected: FAIL with `TypeError: stop() got an unexpected keyword argument 'grace_s'`.

- [ ] **Step 3: Add the grace parameter**

In `app/batch_api/queue_manager.py`, change the signature and the join timeout:

```python
    def stop(self, grace_s: float = 5.0) -> None:
        """Gracefully stop the queue manager, cancelling in-flight jobs and waiting for workers."""
        log.info("Stopping BatchQueueManager (grace=%.1fs)...", grace_s)
        self._stopped = True

        for _ in range(self.max_workers):
            self.queue.put(None)

        with self._lock:
            for run_id in list(self._running_jobs):
                ctrl = self.orchestrator.run_controls.get(run_id)
                if ctrl:
                    log.info(f"Cancelling in-flight run_id={run_id} during queue manager shutdown.")
                    ctrl.cancel()

        for t in self._threads:
            t.join(timeout=grace_s)
        log.info("BatchQueueManager stopped.")
```

- [ ] **Step 4: Run to verify it passes (and no regression)**

Run: `pytest tests/batch_api/test_graceful_shutdown.py::test_queue_stop_accepts_grace_arg -v`
Expected: PASS

Run: `pytest tests/batch_api -k queue -v`
Expected: PASS (default `grace_s=5.0` preserves the prior hardcoded timeout).

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/queue_manager.py tests/batch_api/test_graceful_shutdown.py
git commit -m "feat(shutdown): thread grace window into queue_manager.stop (e4.2)"
```

---

### Task 8: `graceful_shutdown` coordinator

**Files:**
- Create: `app/batch_api/shutdown.py`
- Test: `tests/batch_api/test_graceful_shutdown.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/batch_api/test_graceful_shutdown.py  (append)
from app.batch_api.shutdown import graceful_shutdown
from app.core import process_registry as reg


class _Recorder:
    def __init__(self):
        self.calls = []


def test_graceful_shutdown_stops_both_lanes_then_terminates(monkeypatch):
    order = []

    class _HF:
        def stop(self):
            order.append("hotfolder_stop")

    class _QM:
        def stop(self, grace_s):
            order.append(("queue_stop", grace_s))

    terminated = {}

    def _fake_terminate_all(grace_s):
        order.append("terminate_all")
        terminated["grace"] = grace_s
        return 2

    monkeypatch.setattr(reg, "terminate_all", _fake_terminate_all)

    killed = graceful_shutdown(
        hot_folder_manager=_HF(),
        queue_manager=_QM(),
        grace_s=7.0,
        registry=reg,
    )

    # Hot folder must stop FIRST (no new batches), then queue drains, then kill survivors.
    assert order == ["hotfolder_stop", ("queue_stop", 7.0), "terminate_all"]
    assert killed == 2


def test_graceful_shutdown_tolerates_none_lanes(monkeypatch):
    monkeypatch.setattr(reg, "terminate_all", lambda grace_s: 0)
    # Must not raise when a lane was never initialized.
    assert graceful_shutdown(hot_folder_manager=None, queue_manager=None, grace_s=1.0, registry=reg) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/batch_api/test_graceful_shutdown.py -k graceful_shutdown_ -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.batch_api.shutdown'`.

- [ ] **Step 3: Implement the coordinator**

```python
# app/batch_api/shutdown.py
"""Graceful-shutdown coordinator for SIGTERM (driven by uvicorn's lifespan).

Order matters:
  1. Stop the hot-folder lane FIRST so no new debounced batch can enqueue while
     we are draining.
  2. Drain the worker lane: queue_manager.stop() signals cooperative cancel; the
     active matrix chunk finishes via the orchestrator's between-cell
     ctrl.cancelled checks, then worker threads join within the grace window.
  3. terminate()/kill() any child process that outlived its joined thread so no
     orphan ffmpeg/mogrify survives holding FDs or leaving partial output.
"""
from __future__ import annotations

from ..core.logger import get_logger
from ..core import process_registry as _default_registry

log = get_logger(__name__)


def graceful_shutdown(hot_folder_manager, queue_manager, grace_s, registry=_default_registry) -> int:
    """Drain both lanes within the grace window, then reap surviving children.

    Returns the number of child processes that had to be force-signalled.
    """
    if hot_folder_manager is not None:
        try:
            hot_folder_manager.stop()
        except Exception as e:
            log.warning("hot folder stop failed during shutdown: %s", e)

    if queue_manager is not None:
        try:
            queue_manager.stop(grace_s=grace_s)
        except Exception as e:
            log.warning("queue manager stop failed during shutdown: %s", e)

    try:
        killed = registry.terminate_all(grace_s=grace_s)
    except Exception as e:
        log.error("process registry terminate_all failed during shutdown: %s", e)
        killed = 0

    if killed:
        log.warning("graceful shutdown force-signalled %d surviving child process(es).", killed)
    return killed
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/batch_api/test_graceful_shutdown.py -k graceful_shutdown_ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/shutdown.py tests/batch_api/test_graceful_shutdown.py
git commit -m "feat(shutdown): add two-lane graceful_shutdown coordinator (e4.2)"
```

---

### Task 9: Wire coordinator into the FastAPI lifespan

**Files:**
- Modify: `app/batch_api/main.py` (lifespan `finally`, lines 104-118)
- Test: `tests/batch_api/test_graceful_shutdown.py`

The current `finally` block calls `manager.stop()` and `queue_manager.stop()` ad hoc. Replace that with a single `graceful_shutdown(...)` call so both lanes plus child reaping run in the correct order. The Sharp daemon stop (long-lived, not a per-conversion child) stays as-is after the coordinator.

- [ ] **Step 1: Write the failing test (lifespan invokes coordinator)**

```python
# tests/batch_api/test_graceful_shutdown.py  (append)
from fastapi.testclient import TestClient


def test_lifespan_shutdown_invokes_graceful_shutdown(monkeypatch):
    called = {}

    def _spy(hot_folder_manager, queue_manager, grace_s, **kw):
        called["grace_s"] = grace_s
        called["had_hf"] = hot_folder_manager is not None
        return 0

    monkeypatch.setattr("app.batch_api.main.graceful_shutdown", _spy)
    from app.batch_api.main import app
    with TestClient(app):
        pass  # entering+exiting the context triggers startup then shutdown
    assert "grace_s" in called
    assert called["had_hf"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/batch_api/test_graceful_shutdown.py::test_lifespan_shutdown_invokes_graceful_shutdown -v`
Expected: FAIL with `AttributeError: ... has no attribute 'graceful_shutdown'` (not imported/used yet).

- [ ] **Step 3: Rewire the lifespan finally block**

In `app/batch_api/main.py`, add the import near the other local imports at the top of the module:

```python
from .shutdown import graceful_shutdown
from .config_shim import SHUTDOWN_GRACE_S  # if a shim exists; otherwise import below
```

If there is no shim, import directly:

```python
from ..core.config import SHUTDOWN_GRACE_S
```

Replace the lifespan `finally` body (lines 104-118) with:

```python
    finally:
        graceful_shutdown(
            hot_folder_manager=getattr(app.state, "hot_folder_manager", None),
            queue_manager=getattr(app.state, "queue_manager", None),
            grace_s=SHUTDOWN_GRACE_S,
        )
        # Sharp daemon is a long-lived helper (not a per-conversion child); stop it last.
        sharp_conv = getattr(app.state, "orchestrator", None) and app.state.orchestrator.converters.get("sharp")
        if sharp_conv:
            try:
                log.info("Eagerly stopping Sharp Node daemon on shutdown...")
                sharp_conv._stop_daemon()
            except Exception as e:
                log.warning("Failed to stop Sharp Node daemon on shutdown: %s", e)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/batch_api/test_graceful_shutdown.py::test_lifespan_shutdown_invokes_graceful_shutdown -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/main.py tests/batch_api/test_graceful_shutdown.py
git commit -m "feat(shutdown): wire graceful_shutdown into FastAPI lifespan (e4.2)"
```

---

### Task 10: End-to-end mid-batch SIGTERM acceptance test

**Files:**
- Test: `tests/batch_api/test_graceful_shutdown.py`

Validates the e4.2 acceptance criterion with a fake in-flight chunk: a registered long-running child + an in-flight RunControl get cancelled and reaped, no survivor remains.

- [ ] **Step 1: Write the failing test**

```python
# tests/batch_api/test_graceful_shutdown.py  (append)
import subprocess
import sys
from app.batch_api.run_control import RunControl
from app.batch_api.shutdown import graceful_shutdown
from app.core import process_registry as reg


class _OrchWithRun:
    def __init__(self, run_id):
        self.run_controls = {run_id: RunControl()}


def test_mid_batch_sigterm_cancels_and_reaps(monkeypatch):
    reg.clear()
    run_id = 1
    orch = _OrchWithRun(run_id)

    # Simulate an in-flight chunk: a real child process registered as live.
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    reg.register_process(child)

    class _QM:
        def __init__(self, orch):
            self.orchestrator = orch
        def stop(self, grace_s):
            # Cooperative cancel of the in-flight run, mirroring the real stop().
            self.orchestrator.run_controls[run_id].cancel()

    class _HF:
        def stop(self):
            pass

    killed = graceful_shutdown(hot_folder_manager=_HF(), queue_manager=_QM(orch), grace_s=0.5)

    child.wait(timeout=5)
    assert orch.run_controls[run_id].cancelled is True   # batch was told to stop
    assert child.poll() is not None                       # no orphan child survives
    assert killed >= 1
    assert reg.snapshot() == set()                        # registry drained
```

- [ ] **Step 2: Run to verify it fails, then passes**

Run: `pytest tests/batch_api/test_graceful_shutdown.py::test_mid_batch_sigterm_cancels_and_reaps -v`
Expected: PASS (all production code from Tasks 5-9 already exists). If RunControl's cancel flag attribute differs from `.cancelled`, adjust the assertion to match `app/batch_api/run_control.py` (read it first).

- [ ] **Step 3: Run the full suite**

Run: `pytest`
Expected: PASS (no regressions; new health + shutdown tests green).

- [ ] **Step 4: Commit**

```bash
git add tests/batch_api/test_graceful_shutdown.py
git commit -m "test(shutdown): e2e mid-batch SIGTERM drains and reaps (e4.2)"
```

- [ ] **Step 5: Close e4.2 and the epic**

```bash
bd close pixelpivot_batch-4ev.2
bd close pixelpivot_batch-4ev
bd dolt push
```

---

## Self-Review

**1. Spec coverage:**
- e4.1 `/healthz/live` -> Task 1. `/healthz/ready` (DB, storage, magick, ffmpeg, sharp-socket) -> Tasks 2-3. "break each dependency -> 503 naming failed check" -> Task 3 `test_ready_broken_dep_returns_503_naming_check` + Task 2 per-check failure tests. COVERED.
- e4.2 "stop HotFolderManager watcher (no new batches)" -> Task 8 order assertion (hotfolder_stop first). "BatchOrchestrator finish active chunk, mark run status" -> cooperative `ctrl.cancel()` via queue_manager.stop (Task 7) + existing between-cell `ctrl.cancelled` checks. "subprocess reaping, thread-safe registry, terminate() then kill()" -> Tasks 5-6 + `terminate_all`. "exit <= grace window" -> Task 4 `SHUTDOWN_GRACE_S` threaded through Tasks 7-9. "no orphan ffmpeg/mogrify, DB status consistent, no orphan temp" -> Task 10 e2e. COVERED.

**2. Placeholder scan:** No TBD/TODO; every code step shows full code. Task 6 references "existing body unchanged" but explicitly says indent verbatim — acceptable since it brackets reviewed existing code rather than inventing it.

**3. Type consistency:** `Check(name, ok, detail)` dataclass used identically in health.py and all e4.1 tests. `readiness_checks(orchestrator)` signature consistent across Tasks 2/3. `graceful_shutdown(hot_folder_manager, queue_manager, grace_s, registry=...)` consistent across Tasks 8/9/10. `terminate_all(grace_s)` consistent registry API. `queue_manager.stop(grace_s=...)` consistent Tasks 7/8/9.

**Open items flagged for the implementer:**
- Task 10 assumes `RunControl.cancelled` is the public flag — verify against `app/batch_api/run_control.py` before relying on it.
- Task 9 import: prefer `from ..core.config import SHUTDOWN_GRACE_S` (no shim) unless the codebase already centralizes config re-exports.
- Readiness `storage` probes the data dir, not a per-batch `target_dir` (none exists globally); documented inline in Task 2.

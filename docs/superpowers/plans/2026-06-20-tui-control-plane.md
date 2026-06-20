# TUI + Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a prompt_toolkit TUI that supervises the FastAPI API + sharp daemon, submits batches, controls them (pause/resume/stop/restart), shows live telemetry and logs, and edits settings.

**Architecture:** TUI is the parent process. It spawns uvicorn (and on-demand the sharp node daemon) as children, captures their stdout/stderr into a log panel, and drives the API over REST. Backend gains per-run pause/cancel control (cell-boundary granularity) and an in-memory live-progress endpoint. prompt_toolkit owns the terminal; Rich renders tables/progress to ANSI strings embedded in prompt_toolkit windows.

**Tech Stack:** Python 3.11+, FastAPI, httpx, prompt_toolkit, Rich, psutil, sqlite3, pytest. No new heavyweight deps (air-gap constraint): TOML read via stdlib `tomllib`, write via a small in-repo serializer.

**Source spec:** `docs/superpowers/specs/2026-06-20-tui-control-plane-design.md`

**Build order (bead deps):** `ctrl` + `prog` (parallelizable) → `sup` → `wct` → `4bg`.

**Conventions observed in this repo:**
- API tests use `from fastapi.testclient import TestClient; client = TestClient(app)` and patch `app.batch_api.routes.repo` / `app.batch_api.routes.get_connection`; override deps via `app.dependency_overrides[get_orchestrator]`.
- No emoji/icons in tests (Python console encoding).
- `pytest` per `pytest.ini` (verbose, short tracebacks).
- Orchestrator is a singleton on `app.state.orchestrator`; control + progress state live **on the orchestrator instance** (routes reach it via the `get_orchestrator` dependency), not on `app.state` directly.

---

## Part A — Bead `ctrl`: batch pause / resume / cancel + restart

**File structure:**
- Create: `app/batch_api/run_control.py` — `RunControl` primitive + registry type.
- Modify: `app/batch_api/orchestrator.py` — own a `run_controls` registry; check it at each matrix-cell boundary; finalize `cancelled`.
- Modify: `app/batch_api/routes.py` — `POST /batch/{id}/control`, `POST /batch/{id}/restart`.
- Modify: `app/batch_api/models.py` — `ControlRequest` model.
- Test: `tests/api/test_run_control.py`, `tests/test_orchestrator_cancel.py`.

### Task A1: `RunControl` primitive

**Files:**
- Create: `app/batch_api/run_control.py`
- Test: `tests/api/test_run_control.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_run_control.py
import threading
import time
from app.batch_api.run_control import RunControl

def test_runs_by_default():
    c = RunControl()
    assert c.cancelled is False
    assert c.paused is False
    # wait_if_paused returns immediately when running
    c.wait_if_paused(timeout=0.1)

def test_pause_blocks_until_resume():
    c = RunControl()
    c.pause()
    assert c.paused is True
    released = []
    def worker():
        c.wait_if_paused()
        released.append(True)
    t = threading.Thread(target=worker)
    t.start()
    time.sleep(0.05)
    assert released == []          # still blocked
    c.resume()
    t.join(timeout=1.0)
    assert released == [True]

def test_cancel_unblocks_paused_waiter():
    c = RunControl()
    c.pause()
    c.cancel()
    assert c.cancelled is True
    c.wait_if_paused(timeout=1.0)  # must not hang
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/api/test_run_control.py -v`
Expected: FAIL — `ModuleNotFoundError: app.batch_api.run_control`.

- [ ] **Step 3: Write minimal implementation**

```python
# app/batch_api/run_control.py
"""Per-run cooperative pause/cancel control for batch execution.

A RunControl is checked by the orchestrator at matrix-cell boundaries. Pause
blocks the executing thread on an Event; cancel sets a flag and releases any
paused waiter so the loop can observe cancellation and exit.
"""
import threading
from typing import Dict


class RunControl:
    """Cooperative pause/resume/cancel signal for a single batch run."""

    def __init__(self) -> None:
        self._resume = threading.Event()
        self._resume.set()          # running by default
        self.paused = False
        self.cancelled = False

    def pause(self) -> None:
        self.paused = True
        self._resume.clear()

    def resume(self) -> None:
        self.paused = False
        self._resume.set()

    def cancel(self) -> None:
        self.cancelled = True
        self._resume.set()          # release a paused waiter so it can exit

    def wait_if_paused(self, timeout: float | None = None) -> None:
        self._resume.wait(timeout)


# run_id -> RunControl
RunControlRegistry = Dict[int, RunControl]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/api/test_run_control.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/run_control.py tests/api/test_run_control.py
git commit -m "feat(ctrl): add RunControl pause/resume/cancel primitive"
```

### Task A2: Orchestrator honors RunControl at cell boundaries

**Files:**
- Modify: `app/batch_api/orchestrator.py` (`__init__` ~line 128; matrix loop `for cell in plan:` ~line 336; finalize ~line 448)
- Test: `tests/test_orchestrator_cancel.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_cancel.py
from unittest.mock import patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool

class _FakeConverter:
    is_broken = False
    def _reset_failures(self): pass
    def convert_batch(self, paths, target_dir, fmt, qualities, **kw):
        return {"success_count": len(paths), "failure_count": 0, "errors": [], "telemetry": None}

def _req(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "a.jpg").write_bytes(b"x")
    dst = tmp_path / "dst"
    return BatchRequest(source_dir=str(src), target_dir=str(dst),
                        target_format=["webp", "avif"], tool=[Tool.magick], category=["general"])

def test_cancel_before_run_marks_cancelled(tmp_path):
    orch = BatchOrchestrator()
    orch.converters = {"magick": _FakeConverter()}
    req = _req(tmp_path)
    # Pre-register a cancelled control for run 7
    from app.batch_api.run_control import RunControl
    ctrl = RunControl(); ctrl.cancel()
    orch.run_controls[7] = ctrl
    captured = {}
    def fake_update(conn, run_id, status, total_images=None):
        captured["status"] = status
    with patch.object(orch.repo, "update_status", side_effect=fake_update), \
         patch.object(orch, "_probe_all_dimensions", return_value={str(tmp_path / "src" / "a.jpg"): (10, 10)}), \
         patch.object(orch, "_preflight_resources"):
        orch.execute_batch(7, req)
    assert captured["status"] == "cancelled"
    assert 7 not in orch.run_controls   # cleaned up
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_orchestrator_cancel.py -v`
Expected: FAIL — `AttributeError: 'BatchOrchestrator' object has no attribute 'run_controls'`.

- [ ] **Step 3: Implement**

In `app/batch_api/orchestrator.py`:

Add import near the top (after the other `.` imports):
```python
from .run_control import RunControl, RunControlRegistry
```

In `BatchOrchestrator.__init__`, after `self.repo = BatchRepository()`:
```python
        self.run_controls: RunControlRegistry = {}
```

At the very start of `execute_batch`, after `start_time = time.time()`:
```python
        ctrl = self.run_controls.setdefault(run_id, RunControl())
        cancelled = False
```

Inside the matrix loop, replace the existing top of `for cell in plan:` block so the first lines are:
```python
            for cell in plan:
                if abort_matrix:
                    break
                ctrl.wait_if_paused()
                if ctrl.cancelled:
                    cancelled = True
                    break
```

After the matrix loop, before "4. Save Summary", short-circuit on cancel:
```python
            if cancelled:
                def _mark_cancelled():
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "cancelled",
                                                total_images=total_conversions)
                with_busy_retry(_mark_cancelled, attempts=SQLITE_BUSY_ATTEMPTS,
                                base_delay_s=SQLITE_BUSY_BASE_DELAY_S)
                if all_failure_count > 0:
                    try:
                        with get_connection() as conn:
                            self.repo.save_errors(conn, run_id, all_errors)
                    except Exception as e:
                        log.warning(f"save_errors dropped {len(all_errors)} rows: {e}")
                return
```

Wrap the body so the registry is always cleaned: change the outer
`try:` / `except Exception as e:` of `execute_batch` to add a `finally`:
```python
        finally:
            self.run_controls.pop(run_id, None)
```
(The existing `except Exception` block stays; just add the `finally` after it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_orchestrator_cancel.py -v`
Expected: PASS.

- [ ] **Step 5: Run the orchestrator suite to confirm no regression**

Run: `pytest tests/test_orchestrator.py tests/test_orchestrator_multi_tool.py tests/test_orchestrator_summary_survives_failures.py -v`
Expected: PASS (same pass count as before the change).

- [ ] **Step 6: Commit**

```bash
git add app/batch_api/orchestrator.py tests/test_orchestrator_cancel.py
git commit -m "feat(ctrl): orchestrator honors RunControl at cell boundaries"
```

### Task A3: `ControlRequest` model

**Files:**
- Modify: `app/batch_api/models.py`
- Test: `tests/api/test_run_control.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
# tests/api/test_run_control.py  (append)
import pytest
from pydantic import ValidationError
from app.batch_api.models import ControlRequest

def test_control_request_accepts_valid_actions():
    for a in ("pause", "resume", "stop"):
        assert ControlRequest(action=a).action == a

def test_control_request_rejects_unknown_action():
    with pytest.raises(ValidationError):
        ControlRequest(action="explode")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_run_control.py -k control_request -v`
Expected: FAIL — `ImportError: cannot import name 'ControlRequest'`.

- [ ] **Step 3: Implement** — add to `app/batch_api/models.py`:

```python
class ControlRequest(BaseModel):
    """Request schema for controlling an in-flight batch run."""
    action: Literal["pause", "resume", "stop"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/api/test_run_control.py -k control_request -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/models.py tests/api/test_run_control.py
git commit -m "feat(ctrl): add ControlRequest model"
```

### Task A4: Control + restart endpoints

**Files:**
- Modify: `app/batch_api/routes.py`
- Test: `tests/api/test_control_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_control_endpoints.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.batch_api.routes import get_orchestrator
from app.batch_api.run_control import RunControl

client = TestClient(app)

def test_control_pause_sets_paused_and_status():
    orch = MagicMock()
    ctrl = RunControl()
    orch.run_controls = {5: ctrl}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        with patch("app.batch_api.routes.get_connection") as gc, \
             patch("app.batch_api.routes.repo") as repo:
            gc.return_value.__enter__.return_value = MagicMock()
            r = client.post("/api/v1/batch/5/control", json={"action": "pause"})
            assert r.status_code == 200
            assert ctrl.paused is True
            repo.update_status.assert_called_with(repo.update_status.call_args[0][0], 5, "paused")
    finally:
        app.dependency_overrides.clear()

def test_control_unknown_run_404():
    orch = MagicMock(); orch.run_controls = {}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        r = client.post("/api/v1/batch/999/control", json={"action": "stop"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()

def test_restart_clones_config_and_queues():
    orch = MagicMock(); orch.run_controls = {}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        with patch("app.batch_api.routes.get_connection") as gc, \
             patch("app.batch_api.routes.repo") as repo:
            gc.return_value.__enter__.return_value = MagicMock()
            repo.get_run.return_value = {
                "id": 5, "source_dir": "/src", "target_dir": "/dst",
                "target_format": "webp,avif", "tool": "magick,ffmpeg",
            }
            repo.create_run.return_value = 6
            r = client.post("/api/v1/batch/5/restart")
            assert r.status_code == 200
            assert r.json()["run_id"] == 6
            orch.execute_batch.assert_called_once()
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_control_endpoints.py -v`
Expected: FAIL — 404/405 (routes not defined).

- [ ] **Step 3: Implement** — add to `app/batch_api/routes.py`:

Add import: `from .models import ControlRequest` (extend existing models import line).

```python
@router.post("/batch/{run_id}/control")
async def control_batch(
    run_id: int,
    req: ControlRequest,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Pause, resume, or stop an in-flight batch run."""
    ctrl = orchestrator.run_controls.get(run_id)
    if ctrl is None:
        raise HTTPException(status_code=404, detail="No active run with that id")
    if req.action == "pause":
        ctrl.pause()
        new_status = "paused"
    elif req.action == "resume":
        ctrl.resume()
        new_status = "running"
    else:  # stop
        ctrl.cancel()
        new_status = None  # orchestrator marks 'cancelled' when the loop exits
    if new_status is not None:
        with get_connection() as conn:
            repo.update_status(conn, run_id, new_status)
    return {"run_id": run_id, "action": req.action}


@router.post("/batch/{run_id}/restart")
async def restart_batch(
    run_id: int,
    bg_tasks: BackgroundTasks,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Re-run a finished batch using its originally stored configuration.

    Note: category is not persisted on batch_runs, so a restart re-runs with
    the default category ['general'].
    """
    from .models import BatchRequest, Tool
    with get_connection() as conn:
        run = repo.get_run(conn, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Batch run not found")
        new_req = BatchRequest(
            source_dir=run["source_dir"],
            target_dir=run["target_dir"],
            target_format=[f for f in run["target_format"].split(",") if f],
            tool=[Tool(t) for t in run["tool"].split(",") if t],
            category=["general"],
            trigger_type="restart",
        )
        new_id = repo.create_run(
            conn,
            source_dir=new_req.source_dir,
            target_dir=new_req.target_dir,
            target_format=",".join(new_req.target_format),
            tool=",".join([t.value for t in new_req.tool]),
            trigger_type="restart",
            heuristic_version=orchestrator.interpolator.version,
        )
    bg_tasks.add_task(orchestrator.execute_batch, new_id, new_req)
    return {"run_id": new_id, "status": "queued"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/api/test_control_endpoints.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/routes.py tests/api/test_control_endpoints.py
git commit -m "feat(ctrl): add /batch/{id}/control and /batch/{id}/restart endpoints"
```

---

## Part B — Bead `prog`: live progress endpoint

**File structure:**
- Modify: `app/batch_api/orchestrator.py` — publish in-flight progress to `self.progress`.
- Modify: `app/batch_api/routes.py` — `GET /batch/{id}/progress`.
- Test: `tests/test_orchestrator_progress.py`, `tests/api/test_progress_endpoint.py`.

### Task B1: Orchestrator publishes in-flight progress

**Files:**
- Modify: `app/batch_api/orchestrator.py` (`__init__`; matrix loop)
- Test: `tests/test_orchestrator_progress.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_orchestrator_progress.py
from unittest.mock import patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool

class _FakeConverter:
    is_broken = False
    def _reset_failures(self): pass
    def convert_batch(self, paths, target_dir, fmt, qualities, **kw):
        return {"success_count": len(paths), "failure_count": 0, "errors": [], "telemetry": None}

def test_progress_published_during_run(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "a.jpg").write_bytes(b"x")
    req = BatchRequest(source_dir=str(src), target_dir=str(tmp_path / "dst"),
                       target_format=["webp", "avif"], tool=[Tool.magick], category=["general"])
    orch = BatchOrchestrator()
    orch.converters = {"magick": _FakeConverter()}
    seen = {}
    real_cb = _FakeConverter.convert_batch
    def spy(self, *a, **k):
        # capture progress snapshot mid-run
        seen.update(dict(orch.progress.get(1, {})))
        return real_cb(self, *a, **k)
    with patch.object(_FakeConverter, "convert_batch", spy), \
         patch.object(orch.repo, "update_status"), \
         patch.object(orch.repo, "save_summary"), \
         patch.object(orch, "_preflight_resources"), \
         patch.object(orch, "_probe_all_dimensions",
                      return_value={str(src / "a.jpg"): (10, 10)}):
        orch.execute_batch(1, req)
    assert seen.get("cells_total") == 2
    assert "current_cell" in seen
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_orchestrator_progress.py -v`
Expected: FAIL — `orch.progress` does not exist / KeyError.

- [ ] **Step 3: Implement** — in `app/batch_api/orchestrator.py`:

In `__init__`, after `self.run_controls = {}`:
```python
        self.progress: dict[int, dict] = {}
```

After `total_conversions = len(input_paths) * len(plan)` (the matrix config block), initialize:
```python
            self.progress[run_id] = {
                "cells_done": 0,
                "cells_total": len(plan),
                "current_cell": None,
                "ok": 0,
                "fail": 0,
                "started_at": start_time,
            }
```

At the top of the matrix `for cell in plan:` loop (after the cancel check), set current cell:
```python
                self.progress[run_id]["current_cell"] = f"{cell.category}/{cell.tool}/{cell.target_format}"
```

After `cells_processed += 1` (cell completed), update counters:
```python
                p = self.progress[run_id]
                p["cells_done"] = cells_processed
                p["ok"] = all_success_count
                p["fail"] = all_failure_count
```

In the `finally:` added in Task A2, also drop progress:
```python
        finally:
            self.run_controls.pop(run_id, None)
            self.progress.pop(run_id, None)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_orchestrator_progress.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/orchestrator.py tests/test_orchestrator_progress.py
git commit -m "feat(prog): orchestrator publishes in-flight progress"
```

### Task B2: `GET /batch/{id}/progress`

**Files:**
- Modify: `app/batch_api/routes.py`
- Test: `tests/api/test_progress_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_progress_endpoint.py
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.batch_api.main import app
from app.batch_api.routes import get_orchestrator

client = TestClient(app)

def test_progress_returns_state_and_sample():
    orch = MagicMock()
    orch.progress = {3: {"cells_done": 1, "cells_total": 4, "current_cell": "general/magick/webp", "ok": 5, "fail": 0, "started_at": 0.0}}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        with patch("app.batch_api.routes.psutil") as ps:
            ps.cpu_percent.return_value = 42.0
            ps.virtual_memory.return_value = MagicMock(used=2 * 1024 * 1024 * 1024)
            r = client.get("/api/v1/batch/3/progress")
            assert r.status_code == 200
            data = r.json()
            assert data["cells_total"] == 4
            assert data["cpu_pct"] == 42.0
            assert data["ram_mb"] == 2048.0
    finally:
        app.dependency_overrides.clear()

def test_progress_404_when_not_live():
    orch = MagicMock(); orch.progress = {}
    app.dependency_overrides[get_orchestrator] = lambda: orch
    try:
        r = client.get("/api/v1/batch/77/progress")
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/api/test_progress_endpoint.py -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Implement** — in `app/batch_api/routes.py`:

Add at top: `import psutil`.

```python
@router.get("/batch/{run_id}/progress")
async def get_batch_progress(
    run_id: int,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Return live in-flight progress for a running batch plus a resource sample."""
    state = orchestrator.progress.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No live progress for that run")
    sample = {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_mb": round(psutil.virtual_memory().used / (1024 * 1024), 1),
    }
    return {**state, **sample}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/api/test_progress_endpoint.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/routes.py tests/api/test_progress_endpoint.py
git commit -m "feat(prog): add GET /batch/{id}/progress endpoint"
```

---

## Part C — Bead `sup`: process supervision + structured tool checks

**File structure:**
- Create: `app/core/toolcheck.py` — pure structured tool probes (`ToolStatus`, `check_*`, `check_all`).
- Modify: `app/cli.py` — reuse `toolcheck` (keep CLI prints).
- Create: `app/tui/__init__.py`, `app/tui/supervisor.py` — `ProcessSupervisor`.
- Test: `tests/core/test_toolcheck.py`, `tests/tui/test_supervisor.py`.

### Task C1: `toolcheck` structured probes

**Files:**
- Create: `app/core/toolcheck.py`
- Test: `tests/core/test_toolcheck.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_toolcheck.py
from app.core.toolcheck import ToolStatus, check_binary, check_sharp_daemon

def test_check_binary_missing(tmp_path):
    st = check_binary("ffmpeg", str(tmp_path / "nope.exe"))
    assert isinstance(st, ToolStatus)
    assert st.name == "ffmpeg"
    assert st.ok is False

def test_check_binary_present(tmp_path):
    fake = tmp_path / "magick.exe"
    fake.write_text("x")
    st = check_binary("magick", str(fake))
    assert st.ok is True
    assert st.detail and str(fake) in st.detail

def test_check_sharp_daemon_down_on_closed_port():
    # Port 1 is privileged/unused; connection must fail fast.
    st = check_sharp_daemon(port=1, timeout=0.2)
    assert st.name == "sharp"
    assert st.ok is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/core/test_toolcheck.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/core/toolcheck.py
"""Structured (print-free) tool availability probes.

Shared by the CLI (which formats output) and the TUI Tools screen (which renders
a status board). Mirrors the legacy check_* helpers in app/cli.py but returns
data instead of printing.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolStatus:
    name: str
    ok: bool
    version: Optional[str] = None
    detail: Optional[str] = None


def check_binary(name: str, path_str: str) -> ToolStatus:
    """Check a binary at an explicit path, falling back to PATH lookup."""
    resolved = path_str if os.path.exists(path_str) else shutil.which(name)
    if not resolved:
        return ToolStatus(name, ok=False, detail="not found")
    version = None
    try:
        out = subprocess.run([resolved, "--version"], capture_output=True,
                             text=True, timeout=5)
        version = (out.stdout or out.stderr).splitlines()[0].strip() if (out.stdout or out.stderr) else None
    except Exception:
        version = None
    return ToolStatus(name, ok=True, version=version, detail=resolved)


def check_pyvips() -> ToolStatus:
    """Check that pyvips/libvips imports and its native library loads."""
    try:
        import pyvips
        ver = f"{pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}"
        return ToolStatus("vips", ok=True, version=ver)
    except Exception as e:
        return ToolStatus("vips", ok=False, detail=str(e))


def check_sharp_daemon(port: int = 8765, timeout: float = 1.0) -> ToolStatus:
    """Check whether the sharp daemon is accepting connections on its port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return ToolStatus("sharp", ok=True, detail=f"listening :{port}")
    except Exception as e:
        return ToolStatus("sharp", ok=False, detail=f"down ({e})")


def check_all(ffmpeg_path: str, magick_path: str, sharp_port: int = 8765) -> list[ToolStatus]:
    """Probe all four tools and return their statuses in display order."""
    return [
        check_binary("magick", magick_path),
        check_binary("ffmpeg", ffmpeg_path),
        check_pyvips(),
        check_sharp_daemon(sharp_port),
    ]
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/core/test_toolcheck.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/toolcheck.py tests/core/test_toolcheck.py
git commit -m "feat(sup): add structured toolcheck probes"
```

### Task C2: CLI reuses `toolcheck` (no behavior change)

**Files:**
- Modify: `app/cli.py` (`check_binary`, `check_pyvips`, `check_sharp_daemon`)
- Test: `tests/test_cli.py` (run existing, confirm still green)

- [ ] **Step 1: Implement** — replace the three `check_*` bodies in `app/cli.py` with thin wrappers that print and delegate:

```python
from app.core import toolcheck

def check_binary(name: str, path_str: str) -> bool:
    print(f"Checking {name}...", end="", flush=True)
    st = toolcheck.check_binary(name, path_str)
    print(f" OK ({st.detail})" if st.ok else " FAILED (not found)")
    return st.ok

def check_pyvips() -> bool:
    print("Checking pyvips/libvips...", end="", flush=True)
    st = toolcheck.check_pyvips()
    print(f" OK (libvips version {st.version})" if st.ok else f" FAILED ({st.detail})")
    return st.ok

def check_sharp_daemon(port: int = 8765) -> bool:
    print(f"Checking Sharp daemon (port {port})...", end="", flush=True)
    st = toolcheck.check_sharp_daemon(port)
    print(" OK (connected)" if st.ok else f" WARNING (could not connect: {st.detail})")
    return st.ok
```

- [ ] **Step 2: Run existing CLI tests**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (existing tests still green; if any assert exact old strings, adjust the wrapper print to match the asserted text).

- [ ] **Step 3: Commit**

```bash
git add app/cli.py
git commit -m "refactor(sup): cli check_* delegate to toolcheck"
```

### Task C3: `ProcessSupervisor` — children + pipe capture

**Files:**
- Create: `app/tui/__init__.py` (empty), `app/tui/supervisor.py`
- Test: `tests/tui/__init__.py` (empty), `tests/tui/test_supervisor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_supervisor.py
import sys
import time
from app.tui.supervisor import ProcessSupervisor

def test_start_capture_stop_dummy_child():
    sup = ProcessSupervisor()
    # A child that prints one line then sleeps so we can observe capture + stop.
    cmd = [sys.executable, "-u", "-c",
           "import time; print('HELLO_CHILD', flush=True); time.sleep(30)"]
    sup.start("api", cmd)
    deadline = time.time() + 5
    while time.time() < deadline and not any("HELLO_CHILD" in l for l in sup.get_logs()):
        time.sleep(0.05)
    assert any("HELLO_CHILD" in l for l in sup.get_logs())
    assert sup.status()["api"] == "running"
    sup.stop("api")
    assert sup.status()["api"] == "stopped"

def test_restart_replaces_process():
    sup = ProcessSupervisor()
    cmd = [sys.executable, "-u", "-c", "import time; time.sleep(30)"]
    sup.start("api", cmd)
    first = sup._procs["api"].pid
    sup.restart("api", cmd)
    assert sup._procs["api"].pid != first
    sup.stop("api")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_supervisor.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/tui/supervisor.py
"""ProcessSupervisor — spawn, stop, restart, and tail child processes.

The TUI is the parent of the FastAPI API and (on demand) the sharp node daemon.
Each child's stdout/stderr is drained by a reader thread into a bounded, tagged
ring buffer that the log panel renders.
"""
from __future__ import annotations

import subprocess
import threading
import time
from collections import deque
from typing import Deque, Dict, List, Optional

import httpx


class ProcessSupervisor:
    """Manages named child processes and a merged, bounded log ring buffer."""

    def __init__(self, log_capacity: int = 2000) -> None:
        self._procs: Dict[str, subprocess.Popen] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._logs: Deque[str] = deque(maxlen=log_capacity)
        self._lock = threading.Lock()

    def start(self, name: str, cmd: List[str]) -> None:
        """Spawn a named child and begin draining its output."""
        if self._procs.get(name) and self._procs[name].poll() is None:
            return  # already running
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        self._procs[name] = proc
        t = threading.Thread(target=self._drain, args=(name, proc), daemon=True)
        t.start()
        self._threads[name] = t

    def _drain(self, name: str, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        tag = name.upper()
        for line in proc.stdout:
            with self._lock:
                self._logs.append(f"[{tag}] {line.rstrip()}")

    def stop(self, name: str, timeout: float = 5.0) -> None:
        """Terminate a named child, escalating to kill on timeout."""
        proc = self._procs.get(name)
        if not proc:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=timeout)

    def restart(self, name: str, cmd: List[str]) -> None:
        self.stop(name)
        self.start(name, cmd)

    def status(self) -> Dict[str, str]:
        """Return {name: 'running'|'stopped'} for every known child."""
        out: Dict[str, str] = {}
        for name, proc in self._procs.items():
            out[name] = "running" if proc.poll() is None else "stopped"
        return out

    def get_logs(self) -> List[str]:
        with self._lock:
            return list(self._logs)

    def wait_ready(self, url: str, timeout: float = 15.0, interval: float = 0.25) -> bool:
        """Poll an HTTP URL until it returns 200 or the timeout elapses."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if httpx.get(url, timeout=interval).status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False

    def shutdown(self) -> None:
        for name in list(self._procs):
            self.stop(name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_supervisor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tui/__init__.py app/tui/supervisor.py tests/tui/__init__.py tests/tui/test_supervisor.py
git commit -m "feat(sup): add ProcessSupervisor with pipe capture and readiness probe"
```

---

## Part D — Bead `wct`: TUI app

**File structure:**
- Create: `app/tui/api_client.py` — REST client (incl. progress/control/restart).
- Create: `app/tui/settings.py` — load/save `settings.toml`, precedence + live/restart classification.
- Create: `app/tui/render.py` — Rich-renderable → ANSI string helpers.
- Create: `app/tui/state.py` — UI state + pure reducers (active tab, selected tools/formats, submit payload builder).
- Create: `app/tui/app.py` — prompt_toolkit Application wiring screens, log panel, status bar, keybindings.
- Create: `app/tui/screens/__init__.py` + one module per screen (submit/telemetry/history/tools/settings).
- Test: `tests/tui/test_api_client.py`, `tests/tui/test_settings.py`, `tests/tui/test_state.py`, `tests/tui/test_render.py`, `tests/tui/test_app_smoke.py`.

> UI rendering (prompt_toolkit layout) is exercised by a smoke test only; all
> decision logic lives in `state.py` / `settings.py` and is unit-tested directly.

### Task D1: REST client

**Files:**
- Create: `app/tui/api_client.py`
- Test: `tests/tui/test_api_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_api_client.py
import httpx
from app.tui.api_client import TuiApiClient

def _client_with(handler):
    transport = httpx.MockTransport(handler)
    api = TuiApiClient("http://test/api/v1")
    api._transport = transport     # injected for tests
    return api

def test_start_batch_posts_payload():
    seen = {}
    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"run_id": 9, "status": "queued"})
    api = _client_with(handler)
    out = api.start_batch("/s", "/d", ["webp"], ["magick"], ["general"])
    assert out["run_id"] == 9
    assert seen["url"].endswith("/batch/start")

def test_get_progress():
    def handler(request):
        return httpx.Response(200, json={"cells_done": 1, "cells_total": 2, "cpu_pct": 10.0, "ram_mb": 1.0})
    api = _client_with(handler)
    assert api.get_progress(9)["cells_total"] == 2

def test_control_and_restart():
    def handler(request):
        if request.url.path.endswith("/control"):
            return httpx.Response(200, json={"run_id": 9, "action": "pause"})
        return httpx.Response(200, json={"run_id": 10, "status": "queued"})
    api = _client_with(handler)
    assert api.control(9, "pause")["action"] == "pause"
    assert api.restart(9)["run_id"] == 10
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_api_client.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/tui/api_client.py
"""HTTP client for the TUI -> FastAPI backend.

Mirrors app/web/batch_gui/api_client.py but adds the progress, control, and
restart endpoints. A pluggable httpx transport (_transport) keeps it testable
without a live server.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class TuiApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport: Optional[httpx.BaseTransport] = None

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=10.0)

    def _get(self, path: str) -> Any:
        with self._client() as c:
            r = c.get(f"{self.base_url}{path}")
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        with self._client() as c:
            r = c.post(f"{self.base_url}{path}", json=json)
            r.raise_for_status()
            return r.json()

    def start_batch(self, source_dir: str, target_dir: str,
                    target_format: List[str], tool: List[str],
                    category: List[str]) -> Dict[str, Any]:
        return self._post("/batch/start", {
            "source_dir": source_dir, "target_dir": target_dir,
            "target_format": target_format, "tool": tool, "category": category,
        })

    def get_status(self, run_id: int) -> Dict[str, Any]:
        return self._get(f"/batch/status/{run_id}")

    def get_progress(self, run_id: int) -> Dict[str, Any]:
        return self._get(f"/batch/{run_id}/progress")

    def get_history(self) -> List[Dict[str, Any]]:
        return self._get("/batch/history")

    def get_errors(self, run_id: int) -> List[Dict[str, Any]]:
        return self._get(f"/batch/{run_id}/errors")

    def control(self, run_id: int, action: str) -> Dict[str, Any]:
        return self._post(f"/batch/{run_id}/control", {"action": action})

    def restart(self, run_id: int) -> Dict[str, Any]:
        return self._post(f"/batch/{run_id}/restart")
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_api_client.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tui/api_client.py tests/tui/test_api_client.py
git commit -m "feat(wct): add TUI REST client"
```

### Task D2: Settings load/save + classification

**Files:**
- Create: `app/tui/settings.py`
- Test: `tests/tui/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_settings.py
from app.tui.settings import (
    DEFAULTS, load_settings, save_settings, classify, dumps_toml,
)

def test_defaults_when_no_file(tmp_path):
    cfg = load_settings(tmp_path / "settings.toml")
    assert cfg["api"]["port"] == DEFAULTS["api"]["port"]

def test_env_overrides_default(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", "/env/db.sqlite")
    cfg = load_settings(tmp_path / "settings.toml")
    assert cfg["paths"]["db"] == "/env/db.sqlite"

def test_file_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", "/env/db.sqlite")
    path = tmp_path / "settings.toml"
    path.write_text('[paths]\ndb = "/file/db.sqlite"\n', encoding="utf-8")
    cfg = load_settings(path)
    assert cfg["paths"]["db"] == "/file/db.sqlite"

def test_roundtrip_save_load(tmp_path):
    path = tmp_path / "settings.toml"
    cfg = load_settings(path)
    cfg["batch"]["default_format"] = "jxl"
    save_settings(path, cfg)
    assert load_settings(path)["batch"]["default_format"] == "jxl"

def test_classify_live_vs_restart():
    assert classify("batch", "default_format") == "live"
    assert classify("api", "port") == "restart"

def test_dumps_toml_handles_types():
    out = dumps_toml({"s": {"a": "x", "b": 3, "c": 1.5, "d": True, "e": ["m", "n"]}})
    assert '[s]' in out and 'a = "x"' in out and 'b = 3' in out
    assert 'd = true' in out and 'e = ["m", "n"]' in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_settings.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/tui/settings.py
"""TUI settings: defaults, file<-env<-default precedence, and a tiny TOML writer.

Read uses stdlib tomllib (3.11+). Write uses an in-repo serializer to avoid a
new dependency (air-gap constraint). Precedence (highest first): file, env,
DEFAULTS. Each key is classified 'live' (applied immediately) or 'restart'
(needs API/daemon restart).
"""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any, Dict

DEFAULTS: Dict[str, Dict[str, Any]] = {
    "api":      {"host": "127.0.0.1", "port": 8000},
    "paths":    {"db": "./data/pixelpivot.db", "sharp_port": 8765},
    "tools":    {"ffmpeg": "", "magick": "",
                 "sharp_script": "app/scripts/sharp_daemon.js",
                 "enabled": ["magick", "ffmpeg", "vips", "sharp"]},
    "security": {"allowed_root": ""},
    "limits":   {"max_workers": 0},
    "batch":    {"default_tool": "ffmpeg", "default_format": "avif", "default_quality": 90},
}

# (section, key) -> env var that may override the default.
_ENV_MAP = {
    ("paths", "db"): "PIXELPIVOT_DB_PATH",
    ("security", "allowed_root"): "PIXELPIVOT_ALLOWED_ROOT",
    ("limits", "max_workers"): "PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS",
}

# Keys safe to apply without restarting the API/daemon.
_LIVE = {("batch", "default_tool"), ("batch", "default_format"),
         ("batch", "default_quality"), ("security", "allowed_root"),
         ("tools", "enabled")}


def classify(section: str, key: str) -> str:
    return "live" if (section, key) in _LIVE else "restart"


def _apply_env(cfg: Dict[str, Dict[str, Any]]) -> None:
    for (section, key), env in _ENV_MAP.items():
        val = os.getenv(env)
        if val is None:
            continue
        if isinstance(DEFAULTS[section][key], int):
            try:
                val = int(val)
            except ValueError:
                continue
        cfg[section][key] = val


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> None:
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def load_settings(path: str | Path) -> Dict[str, Dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULTS)
    _apply_env(cfg)
    p = Path(path)
    if p.exists():
        with open(p, "rb") as f:
            _deep_merge(cfg, tomllib.load(f))
    return cfg


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dumps_toml(cfg: Dict[str, Dict[str, Any]]) -> str:
    lines: list[str] = []
    for section, body in cfg.items():
        lines.append(f"[{section}]")
        for key, val in body.items():
            lines.append(f"{key} = {_fmt(val)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def save_settings(path: str | Path, cfg: Dict[str, Dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_toml(cfg), encoding="utf-8")
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tui/settings.py tests/tui/test_settings.py
git commit -m "feat(wct): add settings load/save with precedence and classification"
```

### Task D3: UI state + reducers

**Files:**
- Create: `app/tui/state.py`
- Test: `tests/tui/test_state.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_state.py
import pytest
from app.tui.state import UiState, build_batch_payload

def test_toggle_tool_respects_enabled():
    s = UiState(enabled_tools=["magick", "ffmpeg"])
    s.toggle_tool("magick")
    assert "magick" in s.selected_tools
    s.toggle_tool("magick")
    assert "magick" not in s.selected_tools

def test_toggle_disabled_tool_is_noop():
    s = UiState(enabled_tools=["ffmpeg"])
    s.toggle_tool("vips")          # not enabled
    assert "vips" not in s.selected_tools

def test_build_payload_requires_selections():
    s = UiState(enabled_tools=["magick"])
    s.source_dir = "/s"; s.target_dir = "/d"
    with pytest.raises(ValueError):
        build_batch_payload(s)     # no tool/format selected
    s.toggle_tool("magick"); s.toggle_format("webp")
    payload = build_batch_payload(s)
    assert payload["tool"] == ["magick"]
    assert payload["target_format"] == ["webp"]
    assert payload["category"] == ["general"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_state.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/tui/state.py
"""Pure UI state for the TUI: selections and the submit payload builder.

No prompt_toolkit imports here — keep decision logic unit-testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

TABS = ["submit", "telemetry", "history", "tools", "settings"]
FORMATS = ["webp", "avif", "jxl"]


@dataclass
class UiState:
    enabled_tools: List[str] = field(default_factory=lambda: ["magick", "ffmpeg", "vips", "sharp"])
    selected_tools: List[str] = field(default_factory=list)
    selected_formats: List[str] = field(default_factory=list)
    source_dir: str = ""
    target_dir: str = ""
    category: str = "general"
    active_tab: str = "submit"
    active_run_id: int | None = None

    def toggle_tool(self, tool: str) -> None:
        if tool not in self.enabled_tools:
            return
        if tool in self.selected_tools:
            self.selected_tools.remove(tool)
        else:
            self.selected_tools.append(tool)

    def toggle_format(self, fmt: str) -> None:
        if fmt not in FORMATS:
            return
        if fmt in self.selected_formats:
            self.selected_formats.remove(fmt)
        else:
            self.selected_formats.append(fmt)


def build_batch_payload(s: UiState) -> Dict[str, object]:
    """Validate selections and produce a /batch/start payload."""
    if not s.source_dir or not s.target_dir:
        raise ValueError("source_dir and target_dir are required")
    if not s.selected_tools:
        raise ValueError("select at least one tool")
    if not s.selected_formats:
        raise ValueError("select at least one format")
    return {
        "source_dir": s.source_dir,
        "target_dir": s.target_dir,
        "tool": list(s.selected_tools),
        "target_format": list(s.selected_formats),
        "category": [s.category or "general"],
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tui/state.py tests/tui/test_state.py
git commit -m "feat(wct): add UI state and batch payload builder"
```

### Task D4: Rich → ANSI render helpers

**Files:**
- Create: `app/tui/render.py`
- Test: `tests/tui/test_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_render.py
from app.tui.render import progress_table, tools_table, history_table

def test_progress_table_contains_counts():
    out = progress_table({"cells_done": 1, "cells_total": 4, "current_cell": "general/magick/webp",
                           "ok": 5, "fail": 0, "cpu_pct": 42.0, "ram_mb": 2048.0})
    assert "1/4" in out
    assert "general/magick/webp" in out
    assert "42" in out

def test_tools_table_renders_rows():
    from app.core.toolcheck import ToolStatus
    out = tools_table([ToolStatus("magick", True, "7.1", "/x"), ToolStatus("sharp", False, None, "down")])
    assert "magick" in out and "sharp" in out

def test_history_table_handles_empty():
    assert isinstance(history_table([]), str)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_render.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/tui/render.py
"""Render Rich renderables to ANSI strings for embedding in prompt_toolkit.

prompt_toolkit owns the terminal; we render Rich tables to a string via a
StringIO-backed Console (force_terminal=True) and feed the ANSI text into a
prompt_toolkit window with ANSI() formatting.
"""
from __future__ import annotations

from io import StringIO
from typing import Dict, List

from rich.console import Console
from rich.table import Table

from app.core.toolcheck import ToolStatus


def _render(renderable) -> str:
    buf = StringIO()
    Console(file=buf, force_terminal=True, color_system="standard", width=100).print(renderable)
    return buf.getvalue()


def progress_table(p: Dict) -> str:
    t = Table(title="Batch Progress")
    t.add_column("metric"); t.add_column("value")
    t.add_row("cells", f"{p.get('cells_done', 0)}/{p.get('cells_total', 0)}")
    t.add_row("current", str(p.get("current_cell", "-")))
    t.add_row("ok / fail", f"{p.get('ok', 0)} / {p.get('fail', 0)}")
    t.add_row("cpu %", f"{p.get('cpu_pct', 0):.0f}")
    t.add_row("ram MB", f"{p.get('ram_mb', 0):.0f}")
    return _render(t)


def tools_table(statuses: List[ToolStatus]) -> str:
    t = Table(title="Tools")
    t.add_column("tool"); t.add_column("status"); t.add_column("version/detail")
    for s in statuses:
        t.add_row(s.name, "OK" if s.ok else "DOWN", s.version or s.detail or "")
    return _render(t)


def history_table(runs: List[Dict]) -> str:
    t = Table(title="History")
    for col in ("run_id", "status", "tool", "target_format", "savings_pct"):
        t.add_column(col)
    for r in runs:
        t.add_row(str(r.get("run_id", "")), str(r.get("status", "")),
                  str(r.get("tool", "")), str(r.get("target_format", "")),
                  f"{r.get('savings_pct') or 0:.1f}")
    return _render(t)
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tui/render.py tests/tui/test_render.py
git commit -m "feat(wct): add Rich-to-ANSI render helpers"
```

### Task D5: prompt_toolkit application + screens (smoke-tested)

**Files:**
- Create: `app/tui/screens/__init__.py`, `app/tui/screens/submit.py`, `screens/telemetry.py`, `screens/history.py`, `screens/tools.py`, `screens/settings.py`
- Create: `app/tui/app.py`
- Test: `tests/tui/test_app_smoke.py`

- [ ] **Step 1: Write the failing smoke test**

```python
# tests/tui/test_app_smoke.py
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from app.tui.app import build_application
from app.tui.state import UiState

def test_application_builds_and_renders():
    with create_pipe_input() as inp, create_app_session(input=inp, output=DummyOutput()):
        app = build_application(UiState(), api=None, supervisor=None)
        # Layout must contain the tab bar and log panel containers.
        assert app.layout is not None
        # Tab switch reducer works without a running event loop.
        app.state.active_tab = "tools"
        assert app.state.active_tab == "tools"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_app_smoke.py -v`
Expected: FAIL — `app.tui.app` missing.

- [ ] **Step 3: Implement**

Create each screen module exporting a `render(state, api, supervisor) -> str`
function (returns ANSI text for the body window). Minimal bodies:

```python
# app/tui/screens/submit.py
from app.tui.state import UiState, FORMATS

def render(state: UiState, api, supervisor) -> str:
    tools = " ".join(f"[{'x' if t in state.selected_tools else ' '}]{t}" for t in state.enabled_tools)
    fmts = " ".join(f"[{'x' if f in state.selected_formats else ' '}]{f}" for f in FORMATS)
    return (f"Source: {state.source_dir or '<unset>'}\n"
            f"Target: {state.target_dir or '<unset>'}\n"
            f"Tools:   {tools}\nFormats: {fmts}\n"
            f"Category: {state.category}\n\n[Enter] start batch")
```

```python
# app/tui/screens/telemetry.py
from app.tui.render import progress_table

def render(state, api, supervisor) -> str:
    if state.active_run_id is None or api is None:
        return "No active run. Submit a batch first."
    try:
        return progress_table(api.get_progress(state.active_run_id))
    except Exception:
        try:
            s = api.get_status(state.active_run_id)
            return f"Run {state.active_run_id}: {s.get('status')}"
        except Exception as e:
            return f"(progress unavailable: {e})"
```

```python
# app/tui/screens/history.py
from app.tui.render import history_table

def render(state, api, supervisor) -> str:
    if api is None:
        return "(no api)"
    try:
        return history_table(api.get_history())
    except Exception as e:
        return f"(history unavailable: {e})"
```

```python
# app/tui/screens/tools.py
from app.tui.render import tools_table
from app.core import toolcheck

def render(state, api, supervisor) -> str:
    statuses = toolcheck.check_all(ffmpeg_path="ffmpeg", magick_path="magick")
    hint = "\n[s] start sharp  [x] stop sharp  [r] restart sharp"
    return tools_table(statuses) + hint
```

```python
# app/tui/screens/settings.py
def render(state, api, supervisor) -> str:
    return "Settings: edit in settings.toml (PROJ_ROOT/data/settings.toml).\nLive keys apply on save; others need restart."
```

```python
# app/tui/screens/__init__.py
from . import submit, telemetry, history, tools, settings
RENDERERS = {
    "submit": submit.render, "telemetry": telemetry.render,
    "history": history.render, "tools": tools.render, "settings": settings.render,
}
```

```python
# app/tui/app.py
"""prompt_toolkit application shell: tab bar, body, log panel, status bar.

build_application() wires the layout and keybindings against a UiState, an
optional TuiApiClient, and an optional ProcessSupervisor (both optional so the
smoke test can build the app without a backend).
"""
from __future__ import annotations

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from app.tui.state import UiState, TABS
from app.tui.screens import RENDERERS


def build_application(state: UiState, api=None, supervisor=None) -> Application:
    def tab_bar() -> ANSI:
        return ANSI(" | ".join((f"*{t}*" if t == state.active_tab else t) for t in TABS))

    def body() -> ANSI:
        return ANSI(RENDERERS[state.active_tab](state, api, supervisor))

    def log_panel() -> ANSI:
        lines = supervisor.get_logs()[-12:] if supervisor else []
        return ANSI("\n".join(lines))

    def status_bar() -> ANSI:
        st = supervisor.status() if supervisor else {}
        run = state.active_run_id if state.active_run_id is not None else "-"
        return ANSI(f"API:{st.get('api','?')} SHARP:{st.get('sharp','?')} run:{run}  [Tab] switch  [q] quit")

    kb = KeyBindings()

    @kb.add("tab")
    def _(event):
        i = TABS.index(state.active_tab)
        state.active_tab = TABS[(i + 1) % len(TABS)]

    @kb.add("q")
    def _(event):
        event.app.exit()

    root = HSplit([
        Window(FormattedTextControl(tab_bar), height=1),
        Window(FormattedTextControl(body)),
        Window(FormattedTextControl(log_panel), height=12),
        Window(FormattedTextControl(status_bar), height=1),
    ])
    app = Application(layout=Layout(root), key_bindings=kb, full_screen=True)
    app.state = state          # attach for tests and screen callbacks
    return app
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_app_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole TUI test group**

Run: `pytest tests/tui/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/tui/app.py app/tui/screens/ tests/tui/test_app_smoke.py
git commit -m "feat(wct): add prompt_toolkit app shell and screens"
```

---

## Part E — Bead `4bg`: cli dispatcher (serve / convert / tui)

**File structure:**
- Modify: `app/cli.py` — argparse subcommands; keep validation under `convert`.
- Create: `app/tui/launcher.py` — wires supervisor + app for `tui`.
- Test: `tests/test_cli_dispatch.py`; update `tests/test_cli.py`.

### Task E1: Subcommand dispatcher

**Files:**
- Modify: `app/cli.py`
- Test: `tests/test_cli_dispatch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_dispatch.py
from unittest.mock import patch
import app.cli as cli

def test_serve_invokes_uvicorn():
    with patch("app.cli._run_serve") as serve:
        cli.main(["serve", "--host", "0.0.0.0", "--port", "8001"])
        serve.assert_called_once()

def test_tui_invokes_launcher():
    with patch("app.cli._run_tui") as tui:
        cli.main(["tui"])
        tui.assert_called_once()

def test_convert_runs_validation():
    with patch("app.cli._run_convert") as conv:
        cli.main(["convert", "--source", "/s", "--target", "/d", "--dry-run"])
        conv.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_cli_dispatch.py -v`
Expected: FAIL — `cli.main` takes no argv / subcommands absent.

- [ ] **Step 3: Implement** — restructure `app/cli.py` `main` into subcommands.
Keep existing `check_*` and the validation body, moved into `_run_convert`.

```python
def main(argv=None):
    parser = argparse.ArgumentParser(description="PixelPivot Batch Engine.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the FastAPI API server.")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)

    p_conv = sub.add_parser("convert", help="Validate environment / run conversion.")
    p_conv.add_argument("--source", "-s", required=True)
    p_conv.add_argument("--target", "-t", required=True)
    p_conv.add_argument("--dry-run", action="store_true")

    sub.add_parser("tui", help="Launch the terminal UI (supervises the API).")

    args = parser.parse_args(argv)
    if args.command == "serve":
        _run_serve(args.host, args.port)
    elif args.command == "convert":
        _run_convert(args.source, args.target, args.dry_run)
    elif args.command == "tui":
        _run_tui()


def _run_serve(host: str, port: int) -> None:
    import uvicorn
    uvicorn.run("app.batch_api.main:app", host=host, port=port)


def _run_convert(source: str, target: str, dry_run: bool) -> None:
    # (existing validation body from the old main(): banners, check_paths,
    #  check_binary x2, check_pyvips, check_sharp_daemon, sys.exit on result)
    ...  # move the existing lines here verbatim


def _run_tui() -> None:
    from app.tui.launcher import run_tui
    run_tui()


if __name__ == "__main__":
    main()
```

> When moving the validation body into `_run_convert`, copy the existing lines
> from the old `main()` (the `======` banners, `check_paths`, the two
> `check_binary` calls with the ffmpeg/magick path resolution, `check_pyvips`,
> `check_sharp_daemon`, and the PASSED/FAILED `sys.exit`). Do not paraphrase —
> move them verbatim.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_cli_dispatch.py -v`
Expected: PASS.

- [ ] **Step 5: Update legacy CLI test**

`tests/test_cli.py` calls the old `--source/--target` interface. Update those
invocations to the `convert` subcommand (e.g. `cli.main(["convert", "-s", ..., "-t", ..., "--dry-run"])`),
keeping the original assertions. Run: `pytest tests/test_cli.py -v` → PASS.

- [ ] **Step 6: Commit**

```bash
git add app/cli.py tests/test_cli_dispatch.py tests/test_cli.py
git commit -m "feat(4bg): cli serve/convert/tui subcommand dispatcher"
```

### Task E2: TUI launcher wiring

**Files:**
- Create: `app/tui/launcher.py`
- Test: `tests/tui/test_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_launcher.py
from unittest.mock import MagicMock, patch
from app.tui import launcher

def test_run_tui_starts_api_then_runs_app():
    fake_sup = MagicMock()
    fake_sup.wait_ready.return_value = True
    fake_app = MagicMock()
    with patch.object(launcher, "ProcessSupervisor", return_value=fake_sup), \
         patch.object(launcher, "build_application", return_value=fake_app), \
         patch.object(launcher, "TuiApiClient", return_value=MagicMock()):
        launcher.run_tui()
    fake_sup.start.assert_called()          # API child spawned
    fake_sup.wait_ready.assert_called_once()
    fake_app.run.assert_called_once()
    fake_sup.shutdown.assert_called_once()  # cleaned up on exit
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/tui/test_launcher.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
# app/tui/launcher.py
"""Wire the supervisor + API client + prompt_toolkit app for `cli tui`.

Spawns the API as a child, waits for readiness, builds the app, and guarantees
child shutdown on exit. Sharp is started on demand from the Tools screen, not
here (on-demand policy).
"""
from __future__ import annotations

import sys

from app.tui.app import build_application
from app.tui.api_client import TuiApiClient
from app.tui.settings import load_settings
from app.tui.state import UiState
from app.tui.supervisor import ProcessSupervisor
from app.core.paths import PROJ_ROOT


def run_tui() -> None:
    cfg = load_settings(PROJ_ROOT / "data" / "settings.toml")
    host, port = cfg["api"]["host"], cfg["api"]["port"]
    base_url = f"http://{host}:{port}/api/v1"

    sup = ProcessSupervisor()
    sup.start("api", [sys.executable, "-m", "app.cli", "serve",
                      "--host", str(host), "--port", str(port)])
    ready = sup.wait_ready(f"http://{host}:{port}/")
    api = TuiApiClient(base_url)

    state = UiState(enabled_tools=list(cfg["tools"]["enabled"]))
    app = build_application(state, api=api, supervisor=sup)
    if not ready:
        sup_logs = "\n".join(sup.get_logs()[-5:])
        print(f"API did not become ready; recent logs:\n{sup_logs}", file=sys.stderr)
    try:
        app.run()
    finally:
        sup.shutdown()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/tui/test_launcher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/tui/launcher.py tests/tui/test_launcher.py
git commit -m "feat(4bg): add TUI launcher wiring supervisor + app"
```

---

## Final verification

- [ ] **Run the full suite**

Run: `pytest`
Expected: all previously-passing tests still pass plus the new
`tests/api/`, `tests/core/test_toolcheck.py`, and `tests/tui/` tests.
The 5 known pre-existing failures (4 real-asset E2E needing external bins +
flaky `test_task_002`) remain out of scope.

- [ ] **Add deps to packaging**

Add `prompt_toolkit` and `rich` to `pyproject.toml` (and the air-gap wheel
manifest if one is maintained). `httpx`, `psutil`, `rich` may already be present —
confirm before adding duplicates.

- [ ] **Manual smoke (optional, not in CI)**

Run: `python -m app.cli tui`
Expected: API child starts, Tools tab shows tool board, Submit can launch a
batch, Telemetry shows live progress, `q` quits and the API child is reaped.

---

## Notes / accepted limitations

- **Pause/stop granularity** = matrix-cell boundary. A cell already inside
  `convert_batch` finishes before the signal takes effect.
- **Restart loses category** (`general` default) because `batch_runs` does not
  persist category. Adding a column is out of scope.
- **Single active batch** only — no queue/concurrency (per spec §10).
- **Rich is render-to-string only**; prompt_toolkit owns the terminal.
- **Sharp is on-demand**; the launcher starts only the API.

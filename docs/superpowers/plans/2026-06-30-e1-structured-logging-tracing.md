# Epic E1: Structured Logging + Tracing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every log line in the app is a single-line ECS-JSON object (toggleable) carrying a `trace.id` that ties one batch request together across API/hotfolder/CLI entry points, worker threads, ffmpeg/mogrify subprocesses, and the Sharp daemon.

**Architecture:** A new `app/core/tracing.py` owns a `trace_id` `ContextVar` plus helpers (`new_trace_id(prefix)`, `get_trace_id()`, `run_in_context()`) and a `TraceIdFilter` that fallback-generates a `system-`-prefixed id when none is set. `logger.py` gains an `EcsJsonFormatter` selected by `PIXELPIVOT_LOG_FORMAT` (default `text`) and attaches the filter to all handlers. Entry points stamp a prefixed trace id; `ThreadPoolExecutor` work is submitted through `contextvars.copy_context()` so worker threads inherit it; the Sharp request frame gains a `trace_id` field.

**Tech Stack:** Python stdlib `logging` + `contextvars`, pytest, existing `subprocess.Popen` converter path, Node `sharp_daemon.js`.

**Spec:** `docs/superpowers/specs/2026-06-30-production-readiness-design.md` (E1 section).

**Beads:** epic `pixelpivot_batch-zhr`; children `.1` (e1.1), `.2` (e1.2), `.3` (e1.3), `.4` (e1.4).

---

## File Structure

- **Create** `app/core/tracing.py` — `ContextVar` + `new_trace_id`/`get_trace_id`/`set_trace_id`/`run_in_context`/`bind_context` + `TraceIdFilter`. Single responsibility: trace identity.
- **Create** `tests/test_tracing.py`, `tests/test_logger_ecs.py`, `tests/test_subprocess_wrapping.py`, `tests/test_trace_propagation.py`.
- **Modify** `app/core/logger.py` — `EcsJsonFormatter`, `PIXELPIVOT_LOG_FORMAT` toggle, attach `TraceIdFilter` to handlers.
- **Modify** `app/batch_api/main.py` — ASGI middleware stamps `new_trace_id("req-")` per request.
- **Modify** `app/batch_api/hot_folder.py` — stamp `new_trace_id("hotfolder-")` in `_trigger_batch`.
- **Modify** `app/cli.py` — stamp `new_trace_id("cli-")` at batch entry.
- **Modify** `app/core/converters/base.py` — `_run_subprocess` wraps captured stderr into a structured `subprocess.*` payload; `convert_batch` submits `worker` through `copy_context()`.
- **Modify** `app/batch_api/orchestrator.py` — wrap its `ThreadPoolExecutor` submissions through `copy_context()`.
- **Modify** `app/core/converters/sharp_converter.py` — add `trace_id` to the request dict at `:321` and `:491`.
- **Modify** `app/scripts/sharp_daemon.js` — echo received `trace_id` in daemon log lines.

---

## Task 1 (e1.1): trace_id ContextVar + fallback filter

**Files:**
- Create: `app/core/tracing.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tracing.py
import logging
from app.core import tracing


def test_new_trace_id_uses_prefix_and_sets_contextvar():
    tid = tracing.new_trace_id("req-")
    assert tid.startswith("req-")
    assert tracing.get_trace_id() == tid


def test_get_trace_id_is_none_when_unset():
    tracing.reset_trace_id()
    assert tracing.get_trace_id() is None


def test_filter_injects_current_trace_id():
    tid = tracing.new_trace_id("req-")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    assert tracing.TraceIdFilter().filter(rec) is True
    assert rec.trace_id == tid


def test_filter_fallback_generates_when_unset():
    tracing.reset_trace_id()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    tracing.TraceIdFilter().filter(rec)
    assert rec.trace_id.startswith("system-")
    # fallback also pins it so subsequent lines in the same context match
    assert tracing.get_trace_id() == rec.trace_id
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tracing.py -v`
Expected: FAIL — `ModuleNotFoundError: app.core.tracing` / attributes missing.

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/tracing.py
"""Trace identity: one id per logical request, propagated across threads.

A ContextVar holds the current trace_id. Entry points (API/hotfolder/CLI)
call new_trace_id(prefix). A logging filter injects it onto every record and
fallback-generates a `system-` id when unset, so no record ever lacks trace.id.
"""
import contextvars
import logging
import uuid
from typing import Callable, Optional, TypeVar

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)

T = TypeVar("T")


def new_trace_id(prefix: str = "") -> str:
    """Generate a fresh trace id, store it in the current context, and return it."""
    tid = f"{prefix}{uuid.uuid4().hex}"
    _trace_id.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    _trace_id.set(tid)


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def reset_trace_id() -> None:
    _trace_id.set(None)


def run_in_context(func: Callable[..., T], *args, **kwargs) -> T:
    """Run func with a *copy* of the current context (captures trace_id for threads)."""
    ctx = contextvars.copy_context()
    return ctx.run(func, *args, **kwargs)


class TraceIdFilter(logging.Filter):
    """Attach `trace_id` to every record; fallback-generate when unset."""

    def filter(self, record: logging.LogRecord) -> bool:
        tid = _trace_id.get()
        if tid is None:
            tid = new_trace_id("system-")
        record.trace_id = tid
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tracing.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/tracing.py tests/test_tracing.py
git commit -m "feat(tracing): trace_id contextvar + fallback logging filter (e1.1)"
```

---

## Task 2 (e1.1 cont.): stamp trace_id at all three entry points

**Files:**
- Modify: `app/batch_api/main.py` (add middleware)
- Modify: `app/batch_api/hot_folder.py:92` (`_trigger_batch`)
- Modify: `app/cli.py` (batch command entry)
- Test: `tests/test_trace_propagation.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_propagation.py
from app.core import tracing


def test_hotfolder_trigger_stamps_hotfolder_prefix(monkeypatch):
    tracing.reset_trace_id()
    captured = {}

    def fake_restart(self):
        captured["tid"] = tracing.get_trace_id()

    # _trigger_batch must stamp a hotfolder- trace before doing work
    from app.batch_api import hot_folder
    handler = hot_folder.HotFolderHandler.__new__(hot_folder.HotFolderHandler)
    monkeypatch.setattr(handler, "_run", lambda: captured.__setitem__("tid", tracing.get_trace_id()), raising=False)
    handler._stamp_trace()  # helper added in impl
    assert tracing.get_trace_id().startswith("hotfolder-")
```

> If `HotFolderHandler` differs, adapt the call site — the assertion that matters is: after `_trigger_batch` begins, `get_trace_id()` starts with `hotfolder-`.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trace_propagation.py::test_hotfolder_trigger_stamps_hotfolder_prefix -v`
Expected: FAIL — `_stamp_trace` not defined.

- [ ] **Step 3: Write minimal implementation**

In `app/batch_api/main.py`, add an HTTP middleware (after `app = FastAPI(...)`):

```python
from app.core import tracing

@app.middleware("http")
async def trace_id_middleware(request, call_next):
    tracing.new_trace_id("req-")
    return await call_next(request)
```

In `app/batch_api/hot_folder.py`, top of `_trigger_batch` (line ~92):

```python
from ..core import tracing

def _stamp_trace(self):
    tracing.new_trace_id("hotfolder-")

def _trigger_batch(self):
    self._stamp_trace()
    # ... existing body unchanged ...
```

In `app/cli.py`, at the start of the batch-running command function:

```python
from app.core import tracing
tracing.new_trace_id("cli-")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trace_propagation.py::test_hotfolder_trigger_stamps_hotfolder_prefix -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/main.py app/batch_api/hot_folder.py app/cli.py tests/test_trace_propagation.py
git commit -m "feat(tracing): stamp prefixed trace_id at api/hotfolder/cli entry points (e1.1)"
```

---

## Task 3 (e1.2): ECS JSON formatter + PIXELPIVOT_LOG_FORMAT toggle

**Files:**
- Modify: `app/core/logger.py`
- Test: `tests/test_logger_ecs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_logger_ecs.py
import json
import logging
from app.core.logger import EcsJsonFormatter
from app.core import tracing


def test_ecs_formatter_emits_single_line_json_with_ecs_keys():
    tracing.new_trace_id("req-")
    fmt = EcsJsonFormatter(service_name="pixelpivot-api")
    rec = logging.LogRecord("core.test", logging.INFO, __file__, 10, "hello", None, None)
    rec.trace_id = tracing.get_trace_id()
    rec.batch = {"run_id": 1042, "tool": "ffmpeg", "format": "avif"}
    line = fmt.format(rec)
    assert "\n" not in line
    obj = json.loads(line)
    assert obj["log.level"] == "INFO"
    assert obj["message"] == "hello"
    assert obj["service.name"] == "pixelpivot-api"
    assert obj["trace.id"].startswith("req-")
    assert obj["batch.run_id"] == 1042
    assert "@timestamp" in obj


def test_text_is_default_format(monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_LOG_FORMAT", raising=False)
    from app.core import logger as logmod
    assert logmod._selected_formatter().__class__.__name__ == "Formatter"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_logger_ecs.py -v`
Expected: FAIL — `EcsJsonFormatter` / `_selected_formatter` not defined.

- [ ] **Step 3: Write minimal implementation**

Add to `app/core/logger.py` (imports `os`, `datetime`; reuse existing `json`):

```python
import os
from datetime import datetime, timezone


class EcsJsonFormatter(logging.Formatter):
    """Single-line Elastic Common Schema JSON formatter."""

    # record attrs that are NOT extra fields
    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
        "trace_id", "batch", "subprocess", "performance", "message", "asctime"
    }

    def __init__(self, service_name: str = "pixelpivot"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "@timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "log.level": record.levelname,
            "message": record.getMessage(),
            "service.name": self.service_name,
            "trace.id": getattr(record, "trace_id", None),
            "log.logger": record.name,
        }
        for prefix in ("batch", "performance", "subprocess"):
            payload = getattr(record, prefix, None)
            if isinstance(payload, dict):
                for k, v in payload.items():
                    out[f"{prefix}.{k}"] = v
        if record.exc_info:
            out["error.stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=True)


def _selected_formatter() -> logging.Formatter:
    if os.environ.get("PIXELPIVOT_LOG_FORMAT", "text").lower() == "json":
        return EcsJsonFormatter(
            service_name=os.environ.get("PIXELPIVOT_SERVICE_NAME", "pixelpivot")
        )
    return logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(filename)s:%(funcName)s] - %(message)s"
    )
```

Then in `_configure_root_once`, replace the local `formatter = logging.Formatter(...)` with `formatter = _selected_formatter()` and, after building each handler, attach the filter:

```python
from .tracing import TraceIdFilter
_trace_filter = TraceIdFilter()
file_handler.setFormatter(formatter)
file_handler.addFilter(_trace_filter)
stream_handler.setFormatter(formatter)
stream_handler.addFilter(_trace_filter)
```

> `ensure_ascii=True` keeps emoji/non-ASCII out of log lines (project rule: tests/logs avoid raw unicode).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_logger_ecs.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add app/core/logger.py tests/test_logger_ecs.py
git commit -m "feat(logging): ECS JSON formatter + PIXELPIVOT_LOG_FORMAT toggle (e1.2)"
```

---

## Task 4 (e1.3): subprocess output wrapping

**Files:**
- Modify: `app/core/converters/base.py` (`_run_subprocess`, ~line 303–415)
- Test: `tests/test_subprocess_wrapping.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_subprocess_wrapping.py
import logging
from app.core.converters.base import build_subprocess_log_payload


def test_payload_nests_raw_output_and_parsed_error():
    payload = build_subprocess_log_payload(
        tool_name="ffmpeg",
        returncode=1,
        stderr="frame=  1\n[error] Invalid data found when processing input\n",
    )
    assert payload["tool"] == "ffmpeg"
    assert payload["returncode"] == 1
    assert "Invalid data found" in payload["error"]
    assert "frame=" in payload["raw_output"]
    assert "\n" not in payload["error"]  # single concise error line


def test_payload_no_error_on_success():
    payload = build_subprocess_log_payload("ffmpeg", 0, "frame=  1\n")
    assert payload["returncode"] == 0
    assert payload.get("error") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_subprocess_wrapping.py -v`
Expected: FAIL — `build_subprocess_log_payload` not defined.

- [ ] **Step 3: Write minimal implementation**

Add a pure helper near the top of `app/core/converters/base.py`:

```python
def build_subprocess_log_payload(tool_name: str, returncode: int, stderr: str) -> dict:
    """Structured payload for a finished subprocess; raw text stays nested."""
    raw = (stderr or "").strip()
    error = None
    if returncode != 0 and raw:
        # pick the most error-like line, else the last non-empty line
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        err_lines = [ln for ln in lines if "error" in ln.lower() or "invalid" in ln.lower()]
        error = (err_lines[-1] if err_lines else lines[-1]).strip()
    return {"tool": tool_name, "returncode": returncode, "raw_output": raw, "error": error}
```

Then, in `_run_subprocess` where the failed/non-zero branch currently logs raw stderr, replace the raw log call with a structured one:

```python
payload = build_subprocess_log_payload(tool_name, proc.returncode, stderr_text)
if payload["returncode"] != 0:
    log.warning("subprocess failed", extra={"subprocess": payload})
```

> Do NOT `log.error(stderr_text)` anywhere in this method — the multi-line dump is exactly what e1.3 removes. The `extra={"subprocess": ...}` dict is picked up by `EcsJsonFormatter` (Task 3) and ignored harmlessly by the text formatter.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_subprocess_wrapping.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run full converter suite (no regressions)**

Run: `pytest tests/test_base_converter.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/converters/base.py tests/test_subprocess_wrapping.py
git commit -m "feat(logging): wrap subprocess stderr into structured payload (e1.3)"
```

---

## Task 5 (e1.4): propagate trace_id into worker threads

**Files:**
- Modify: `app/core/converters/base.py:610` (`convert_batch` ThreadPoolExecutor)
- Modify: `app/batch_api/orchestrator.py:257,403` (ThreadPoolExecutor blocks)
- Test: `tests/test_trace_propagation.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_propagation.py  (append)
from concurrent.futures import ThreadPoolExecutor
from app.core import tracing


def test_copy_context_carries_trace_id_into_worker_thread():
    tracing.new_trace_id("req-")
    parent = tracing.get_trace_id()
    seen = []

    def work(_):
        seen.append(tracing.get_trace_id())

    # plain executor.map LOSES the contextvar; run_in_context preserves it
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(lambda a: tracing.run_in_context(work, a), [1, 2]))

    assert seen == [parent, parent]
```

- [ ] **Step 2: Run test to verify it fails**

First prove the bug exists, then the fix. Temporarily assert the naive path loses it:

Run: `pytest tests/test_trace_propagation.py::test_copy_context_carries_trace_id_into_worker_thread -v`
Expected: PASS once `run_in_context` (Task 1) is used — but BEFORE wiring the real call sites, the converter-level propagation test below fails.

- [ ] **Step 3: Wire real call sites**

In `app/core/converters/base.py` at the `convert_batch` executor (line ~610), wrap the worker:

```python
from ..tracing import run_in_context
# was: results = list(executor.map(worker, zip(input_paths, qualities)))
results = list(executor.map(
    lambda args: run_in_context(worker, args), zip(input_paths, qualities)
))
```

In `app/batch_api/orchestrator.py`, for each `ThreadPoolExecutor` map/submit (lines ~257 and ~403), wrap the submitted callable identically with `run_in_context`:

```python
from ..core.tracing import run_in_context
# example: ex.submit(run_in_context, fn, arg)  /  ex.map(lambda a: run_in_context(fn, a), items)
```

- [ ] **Step 4: Add the converter-level propagation test**

```python
# tests/test_trace_propagation.py  (append)
def test_convert_batch_worker_inherits_trace_id(monkeypatch):
    from app.core.converters import base as basemod
    tracing.new_trace_id("req-")
    expected = tracing.get_trace_id()
    seen = []

    def fake_convert(self, in_path, out_path, fmt, q, run_id=None):
        seen.append(tracing.get_trace_id())
        return {"success": True, "bytes_written": 1, "telemetry": {}}

    monkeypatch.setattr(basemod.BaseConverter, "convert", fake_convert, raising=False)
    # construct/obtain a concrete converter instance per existing test helpers,
    # call convert_batch over 2 fake paths, then:
    assert seen and all(t == expected for t in seen)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_trace_propagation.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/converters/base.py app/batch_api/orchestrator.py tests/test_trace_propagation.py
git commit -m "feat(tracing): carry trace_id into ThreadPoolExecutor workers via copy_context (e1.4)"
```

---

## Task 6 (e1.4 cont.): propagate trace_id to the Sharp daemon

**Files:**
- Modify: `app/core/converters/sharp_converter.py:321,491` (request dicts)
- Modify: `app/scripts/sharp_daemon.js` (log received trace_id)
- Test: `tests/test_trace_propagation.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trace_propagation.py  (append)
import json
from app.core.converters.sharp_converter import build_sharp_request
from app.core import tracing


def test_sharp_request_includes_current_trace_id():
    tracing.new_trace_id("req-")
    req = build_sharp_request(in_path="a.png", out_path="a.webp", fmt="webp", quality=80)
    assert req["trace_id"] == tracing.get_trace_id()
    # frame is newline-delimited JSON
    assert "\n" not in json.dumps(req)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trace_propagation.py::test_sharp_request_includes_current_trace_id -v`
Expected: FAIL — `build_sharp_request` not defined.

- [ ] **Step 3: Implement**

Extract request-dict construction in `sharp_converter.py` into a helper used by both `convert` (`:321`) and `convert_batch` (`:491`):

```python
from ..tracing import get_trace_id

def build_sharp_request(in_path, out_path, fmt, quality, **extra) -> dict:
    req = {"input": in_path, "output": out_path, "format": fmt, "quality": quality}
    req.update(extra)
    tid = get_trace_id()
    if tid:
        req["trace_id"] = tid
    return req
```

Replace the inline `request = {...}` dicts at both sites with `request = build_sharp_request(...)`, preserving their existing keys via `**extra`.

In `app/scripts/sharp_daemon.js`, where each request is parsed, include the trace id in any log line:

```javascript
const traceId = req.trace_id || "system-daemon";
console.error(JSON.stringify({ "log.level": "INFO", "trace.id": traceId,
  message: `sharp convert ${req.format}` }));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_trace_propagation.py::test_sharp_request_includes_current_trace_id -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/converters/sharp_converter.py app/scripts/sharp_daemon.js tests/test_trace_propagation.py
git commit -m "feat(tracing): pass trace_id to sharp daemon over TCP frame (e1.4)"
```

---

## Task 7: Full-suite regression + epic close

- [ ] **Step 1: Run the whole suite**

Run: `pytest`
Expected: PASS (pre-existing real-asset E2E failures that need external bins are acceptable; no NEW failures).

- [ ] **Step 2: Manual JSON smoke**

Run (PowerShell): `$env:PIXELPIVOT_LOG_FORMAT="json"; uvicorn app.batch_api.main:app --port 8000`
Then POST a small batch; confirm stdout lines are single-line JSON sharing one `trace.id`.

- [ ] **Step 3: Close beads**

```bash
bd close pixelpivot_batch-zhr.1 pixelpivot_batch-zhr.2 pixelpivot_batch-zhr.3 pixelpivot_batch-zhr.4 pixelpivot_batch-zhr
```

- [ ] **Step 4: Open PR for the epic branch** (per beads-tdd-python flow).

---

## Self-Review

**Spec coverage (E1):**
- e1.1 contextvar + fallback → Task 1; entry-point stamping → Task 2. ✓
- e1.2 ECS formatter + `PIXELPIVOT_LOG_FORMAT` toggle → Task 3. ✓
- e1.3 subprocess wrapping (no multiline dump) → Task 4. ✓
- e1.4 thread propagation (copy_context) → Task 5; ffmpeg log prefix via the structured `subprocess` payload (Task 4 carries trace via record filter) + Sharp daemon frame field → Task 6. ✓

**Placeholder scan:** all code steps contain real code; no TBD/TODO. Task 2 and Task 5 Step 4 note "adapt to existing helper" — these are call-site adaptations, the asserted behavior is concrete.

**Type consistency:** `new_trace_id`, `get_trace_id`, `reset_trace_id`, `run_in_context`, `TraceIdFilter`, `EcsJsonFormatter`, `_selected_formatter`, `build_subprocess_log_payload`, `build_sharp_request` — names used identically across tasks.

**Note for executor:** Task 5 Step 2 expectation is soft (depends on test ordering); the binding assertion is the converter-level test in Step 4. If a concrete converter constructor is unclear, mirror the instantiation already used in `tests/test_base_converter.py`.

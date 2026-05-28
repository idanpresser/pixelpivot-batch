# Task 021 — Stop the per-logger RotatingFileHandler race on Windows

**Severity:** HIGH (observability; on the air-gap Win10 target the user has no
remote log access, so unreliable rotation = blind operations.)
**Feature:** G1 (logging on the air-gap target)
**Air-gap relevance:** **Critical** — the deployment OS is Windows Sandbox
running the same `RotatingFileHandler` race; operators have no way to triage
encoder failures because the log file rotation crashes on every threshold
crossing.

## Reproduction (the breadcrumb)

Run the audit harness; the log spam appears across the entire output:
```
$env:PYTHONIOENCODING='utf-8'
python tests\audit_threads\harness_01_api_orch_db.py
```
Excerpt:
```
--- Logging error ---
Traceback (most recent call last):
  File "C:\Python314\Lib\logging\handlers.py", line 80, in emit
    self.doRollover()
  File "C:\Python314\Lib\logging\handlers.py", line 185, in doRollover
    self.rotate(self.baseFilename, dfn)
  File "C:\Python314\Lib\logging\handlers.py", line 121, in rotate
    os.rename(source, dest)
PermissionError: [WinError 32] The process cannot access the file because it
is being used by another process:
'F:\\DEV\\PixelPivot_202605\\pixelpivot_batch\\pixelpivot.log' ->
'F:\\DEV\\PixelPivot_202605\\pixelpivot_batch\\pixelpivot.log.1'
```

Trigger any path that produces enough log volume to cross `maxBytes=1_000_000`
(in this audit it triggered while `pixelpivot.log` was over 1MB from previous
sessions; a quick way to reproduce on a clean box is to set `maxBytes=1024`
and warm a single batch).

## Root cause (from the code, not a doc)

`app/core/logger.py:55-72`:
```python
def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        ...
        file_handler = RotatingFileHandler(log_file, maxBytes=1_000_000,
                                           backupCount=10, delay=True)
        ...
        logger.addHandler(file_handler)
```

Every distinct `name` (e.g. `app.batch_api.orchestrator`,
`app.batch_api.hot_folder`, `app.core.telemetry`,
`app.core.db.repositories.telemetry`, ...) gets **its own**
`RotatingFileHandler` instance pointed at the **same `pixelpivot.log` file**.
On Windows, `os.rename(source, dest)` requires that no process — including
the same process via another file handle — holds an open handle on `source`.
When handler A tries to rotate, handlers B/C/D have the file open, so the
rename fails with WinError 32.

`delay=True` only postpones the *first* open; it does not address the
multi-handler-same-file collision.

The orchestrator also runs background watchdog/uvicorn threads that hold
file handles via *their* loggers (since `uvicorn.error`, `watchdog`, etc. all
call `logging.getLogger(...)` and add file handlers too if propagation lands).

## Required behavior

A single in-process file sink, one handler instance, no rename races. The
canonical idiom is to attach the `RotatingFileHandler` once to the **root**
logger (or to a single named app logger) and let propagation deliver every
record. Module loggers should NOT add the file handler themselves.

After fix: a warmed run should produce zero `--- Logging error ---` blocks
even after crossing `maxBytes` (verify by tailing the log file growth and
checking for `.1` / `.2` rotated files appearing cleanly).

## TDD plan

RED — `tests/test_task_021.py` (ASCII only):

1. Use `tmp_path` to point the log file to a fresh temp file (set the
   `PROJ_ROOT` env var or monkeypatch `app.core.paths.PROJ_ROOT`).
2. Call `get_logger("a")`, `get_logger("b")`, `get_logger("c")`. Assert that
   **at most ONE** `RotatingFileHandler` references the target file across
   the propagation chain (walk the root logger's handlers; the per-module
   loggers should not own one).
3. Set `maxBytes` to a tiny value (e.g. 256) via monkeypatching the constant
   or via a fixture, then emit ~50 WARNING records from each of `a`, `b`,
   `c` concurrently from 3 threads.
4. Assert (a) no `PermissionError` was raised during the rotation, and
   (b) the log file plus `.1` exist and are non-empty.

To verify (a) reliably on Windows: wrap the `logging` module's
`Handler.handleError` in a mock that *re-raises* instead of swallowing — or
install a hook that captures `sys.stderr` and assert no `WinError 32` text.

GREEN — minimal change in `app/core/logger.py`:

- Add a module-level `_root_configured = False` flag.
- On first `get_logger`, configure the root logger (or a single app-wide
  named logger like `"pixelpivot"`) with the `RotatingFileHandler` and
  `StreamHandler`, then return a child of it. Subsequent `get_logger("foo")`
  calls return `logging.getLogger("pixelpivot.foo")` (or use
  `logger.propagate = True` and avoid adding handlers).
- Optionally consider `concurrent_log_handler.ConcurrentRotatingFileHandler`
  if it is in `vendor/wheels` -- but the air-gap closure does not currently
  bundle that wheel, so prefer the propagation fix.

## Acceptance criteria

- [ ] Three calls to `get_logger(...)` produce at most one
      `RotatingFileHandler` against the log file (assert via
      `logging.Manager` walk).
- [ ] A multi-threaded write-then-rotate harness completes with no
      `--- Logging error ---` lines on stderr.
- [ ] `pixelpivot.log` and `pixelpivot.log.1` exist after rotation;
      `pixelpivot.log` is non-empty and ASCII-decodes.
- [ ] Full `pytest` suite green.
- [ ] Any new tunable in `app/core/config.py`.
- [ ] `convert_batch()` return shape unchanged.
- [ ] No `int()` cast of `quality` inside converter code.
- [ ] ASCII-only test code/messages.

## Constraints for the implementer (Sonnet)

TDD only (red before green, paste failing output first). No destructive ops,
no `git push` / force / amend / `--no-verify`. Fix exactly this defect — no
drive-by refactors. Behavior identical on Python 3.12 and 3.14.

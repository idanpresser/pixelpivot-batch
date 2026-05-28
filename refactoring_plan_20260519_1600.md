# Refactoring Plan — 2026-05-19 16:00

Snapshot taken after `28c17e7` (FFmpeg subprocess refactor) and `382ed52`
(SQLite migration + cleanup). 50/50 tests pass; the DB layer and FFmpeg path
are healthy. The items below are the open work surfaced by the top-down
inspection.

## Conventions

- **Lists, tables, and tunable constants** must live in `app/core/config.py`
  (project rule — see memory `config-centralization`). Do not declare
  module-level constants alongside the feature code.
- **All new SQL DDL** goes in `app/core/db/schema.py`'s `_DDL_STATEMENTS`
  tuple. The schema is SQLite-only; no dialect branches.
- **Per-batch lifecycle writes** (`batch_runs.status`,
  `batch_summary.duration_ms`) are **must-succeed**. **Per-tick telemetry
  samples** (`pipeline_telemetry`) are **best-effort** — drop with a
  `WARNING` log, never raise.
- **Lists/tables in tests** that mirror config values (fatal markers,
  timeout tables) should import from config instead of duplicating.

## Task index

| # | Priority | Summary | Effort |
|---|---|---|---|
| T1 | Critical | Persist per-file failure details (`batch_errors` table + `save_errors`) | ~80 LOC across 6 files |
| T2 | High     | GUI history panel `id` → `run_id` | ~5 LOC, 1 file |
| T3 | Medium   | Validate `HotFolderRequest` at registration | ~20 LOC, 2 files |
| T4 | Medium   | Chunk Magick `mogrify` + Sharp pipelined batches | ~40 LOC, 2 files + config |
| T5 | Medium   | JXL-safe `_probe_quality` fallback + telemetry-survives-failure test | ~10 LOC + 1 new test |
| T6 | Low      | Remove phantom `setup_sharp_portable.ps1` references | ~5 LOC, 1 file |
| T7 | Low      | Guard legacy `psycopg` imports | ~10 LOC, 7 files |
| T8 | Low      | Fix docker-compose Streamlit launch syntax | ~3 LOC, 1 file |
| T9 | Low      | Reduce `TelemetryMonitor.children()` walk frequency | ~30 LOC, 1 file |
| T10 | Low     | Failures sub-panel in GUI exposing `batch_errors` | ~50 LOC, 1 new panel |

T1–T5 are the recommended next-five sprint. T6–T10 are smaller polish items
that can land independently.

---

## T1 — Persist per-file failure details (CRITICAL)

### Why this is critical

`app/batch_api/orchestrator.py:155` calls `self.repo.save_errors(conn, run_id, error_list)`.
`BatchRepository` has no such method. `app/core/db/schema.py` has no `batch_errors`
table.

**Effect:** any batch with `failure_count > 0` raises `AttributeError`
inside the orchestrator's success path. The outer `except Exception` on
line 159 catches it and marks the run `"failed"`, but the in-flight
`with get_connection() as conn` block rolls back, so `batch_summary` is
**not saved**. The `duration_ms` value the user defined as the primary
telemetry signal is lost on exactly the runs where telemetry matters most.

### Files affected

| Path | Change |
|---|---|
| `app/core/db/schema.py` | Append `batch_errors` DDL to `_DDL_STATEMENTS` |
| `app/core/db/repositories/batch.py` | New method `BatchRepository.save_errors` |
| `app/core/converters/base.py` | `BaseConverter._default_batch_convert` returns `errors: list[dict]` |
| `app/core/converters/magick_converter.py` | `MagickConverter.convert_batch` returns same shape |
| `app/core/converters/sharp_converter.py` | `SharpConverter.convert_batch` returns same shape |
| `app/batch_api/orchestrator.py` | Build structured errors before calling `save_errors` |
| `tests/test_batch_repo_sqlite.py` | Extend cycle test to cover `save_errors` |
| `tests/test_orchestrator_summary_survives_failures.py` | **NEW** invariant test |

### Schema

Append to `_DDL_STATEMENTS` in `app/core/db/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS batch_errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
    input_path  TEXT,
    error       TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### Repository method

In `app/core/db/repositories/batch.py`, on `BatchRepository`:

```python
def save_errors(self, conn: sqlite3.Connection, batch_id: int,
                errors: list[dict]) -> None:
    """Persist per-file failure rows. Empty list is a no-op."""
    if not errors:
        return
    cur = conn.cursor()
    try:
        cur.executemany(
            "INSERT INTO batch_errors (batch_id, input_path, error) "
            "VALUES (?, ?, ?)",
            [(batch_id, e.get("path"), str(e.get("error", "unknown")))
             for e in errors],
        )
    finally:
        cur.close()
```

### Converter contract change

All `convert_batch` implementations must return `errors` as
`list[dict[str, str]]` shaped `{"path": str, "error": str}`. Today Magick
and Sharp return `list[str]`; the orchestrator's bridge on lines 147-154
guesses by index alignment, which is fragile.

- `BaseConverter._default_batch_convert` (`app/core/converters/base.py:233`): change
  the existing `errors.append(res.get("error") or "Unknown error")` to
  `errors.append({"path": in_path, "error": res.get("error") or "Unknown error"})`.
- `MagickConverter.convert_batch` (`app/core/converters/magick_converter.py:104`): the
  two `errors.append(...)` call sites (lines ~189, ~205) need the same shape.
- `SharpConverter.convert_batch` (`app/core/converters/sharp_converter.py:312`):
  same for `errors.append(...)` on lines ~371, ~374.

### Orchestrator change

`app/batch_api/orchestrator.py:142-155` — replace the index-guessing bridge
with direct pass-through (converters now emit structured dicts):

```python
if result.get("failure_count", 0) > 0:
    raw_errors = result.get("errors", [])
    # Tolerate legacy str entries for safety
    error_list = [
        e if isinstance(e, dict) else {"path": None, "error": str(e)}
        for e in raw_errors
    ]
    self.repo.save_errors(conn, run_id, error_list)
```

### Tests

Extend `tests/test_batch_repo_sqlite.py::test_batch_repository_sqlite_full_cycle`
to insert two error rows and assert they round-trip:

```python
repo.save_errors(conn, batch_id=run_id, errors=[
    {"path": "a.jpg", "error": "boom"},
    {"path": "b.jpg", "error": "boom"},
])
cur = conn.cursor()
cur.execute("SELECT input_path, error FROM batch_errors WHERE batch_id=?", (run_id,))
rows = cur.fetchall()
assert {(r["input_path"], r["error"]) for r in rows} == {("a.jpg", "boom"), ("b.jpg", "boom")}
```

Add **new** file `tests/test_orchestrator_summary_survives_failures.py` —
the invariant test. A batch with all-failing inputs must still write
`batch_summary.duration_ms`:

```python
@pytest.mark.asyncio
async def test_summary_persists_when_all_files_fail(tmp_path):
    """Primary telemetry (duration_ms) MUST survive even when every file fails."""
    ...
    # mock converter returns {"success_count": 0, "failure_count": 3,
    #                         "duration_ms": 250.0, "errors": [...]}
    # assert batch_summary row exists with non-null duration_ms
    # assert batch_errors has 3 rows for run_id
```

### Acceptance

- Existing 50 tests still pass.
- New test passes.
- `git grep save_errors` shows the method exists on `BatchRepository`.
- `sqlite3 pixelpivot.db ".schema batch_errors"` returns the new table.

---

## T2 — GUI history panel column rename (HIGH)

### Why

The repo now aliases the joined column as `run_id` (fixing the audit's
`r.*, s.*` column-collision finding). `app/web/batch_gui/panels/history_panel.py:44`
still references `"id"`, and `:55` configures the display column as
`"id": "ID"`. **First click on the History tab raises `KeyError: 'id'`.**

### Files affected

| Path | Change |
|---|---|
| `app/web/batch_gui/panels/history_panel.py` | Rename `id` → `run_id` in two places |

### Fix

`render_history_panel` in `app/web/batch_gui/panels/history_panel.py`:

```python
# line ~43
display_df = df[[
    "run_id", "status", "target_format", "tool",
    "total_images", "success_count", "failure_count",
    "duration_ms", "created_at"
]].copy()

# line ~54 column_config
column_config={
    "run_id": "ID",
    "status": st.column_config.StatusColumn("Status"),
    "success_count": "Success",
    "failure_count": "Failure",
    "duration_s": "Duration (s)",
    "created_at": "Started At",
},
```

### Acceptance

- Open the GUI's **HISTORY** tab — no `KeyError`.
- Run a batch, refresh — the new row appears with the correct ID.

---

## T3 — Hot folder request validation (MEDIUM)

### Why

`HotFolderManager.add_hot_folder` (`app/batch_api/hot_folder.py:86`)
passes `config["source_dir"]` straight into `watchdog.observer.schedule`.
Watchdog tolerates nonexistent paths — the `Watch` is created but never
fires. Users see "registered" with no follow-up activity.

`routes.py:69-74` catches the generic `Exception` and returns `500`
instead of distinguishing client-side validation errors (`400`).

### Files affected

| Path | Change |
|---|---|
| `app/batch_api/hot_folder.py` | `HotFolderManager.add_hot_folder` validates via Pydantic |
| `app/batch_api/routes.py` | Map `ValueError` → `HTTPException(400)` |

### Fix

`HotFolderManager.add_hot_folder` in `app/batch_api/hot_folder.py:86`:

```python
def add_hot_folder(self, config: Dict[str, Any]) -> str:
    from pathlib import Path
    cfg = HotFolderRequest(**config)             # eager enum/type validation
    src = Path(cfg.source_dir)
    if not src.is_dir():
        raise ValueError(f"source_dir does not exist or is not a directory: {src}")

    cfg_dict = cfg.model_dump()
    handler = HotFolderHandler(self.orchestrator, self.loop, cfg_dict)
    watch = self.observer.schedule(handler, str(src), recursive=False)
    watcher_id = uuid.uuid4().hex
    self.watchers[watcher_id] = {"handler": handler, "watch": watch, "config": cfg_dict}
    return watcher_id
```

`register_hot_folder` in `app/batch_api/routes.py:66-74`:

```python
@router.post("/hotfolder/register")
async def register_hot_folder(req: HotFolderRequest):
    try:
        manager = get_hot_folder_manager()
        watcher_id = manager.add_hot_folder(req.model_dump())
        return {"watcher_id": watcher_id, "status": "active"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

### Tests

Extend `tests/test_hot_folder_api.py`:

```python
def test_register_rejects_nonexistent_source_dir(client):
    resp = client.post("/api/v1/hotfolder/register", json={
        "source_dir": "/definitely/does/not/exist",
        "target_dir": "/tmp/out", "target_format": "webp",
        "tool": "magick", "category": "general",
    })
    assert resp.status_code == 400
    assert "does not exist" in resp.json()["detail"]
```

### Acceptance

- Bad source dir → `400` with a useful message, no silent "active" watcher.
- Valid source dir → still returns the `watcher_id` and starts watching.

---

## T4 — Chunk Magick `mogrify` and Sharp pipelined batches (MEDIUM)

### Why

Both converters have latent failure modes at production scale:

- **Magick:** `MagickConverter.convert_batch` builds
  `[magick, mogrify, -path, output, -format, fmt, *params, *paths]`.
  A 1 000-file batch easily exceeds Windows' 8 191-char `CreateProcess`
  limit and Linux's typical `execve` ~128 KB. On overflow the fast path
  silently falls back to per-file `magick`, destroying the throughput
  claim.

- **Sharp:** `SharpConverter.convert_batch` sends every request first,
  then reads every response. With > ~200 files the kernel send buffer
  fills, the Node daemon's write side blocks waiting for Python to drain,
  Python is still inside the send loop — deadlock until the 60s+ timeout.

### Files affected

| Path | Change |
|---|---|
| `app/core/config.py` | Add `MAGICK_MOGRIFY_CHUNK`, `SHARP_PIPELINE_CHUNK` |
| `app/core/converters/magick_converter.py` | Chunk `mogrify` calls |
| `app/core/converters/sharp_converter.py` | Interleave send/recv in chunks |

### Constants (in `app/core/config.py`)

```python
# Magick batch chunking — keep cmdline under Windows' 8191-char CreateProcess limit.
# At ~80 chars/path: 200 files * 80 = 16 KB headroom on Linux; on Windows tune lower.
MAGICK_MOGRIFY_CHUNK = 200

# Sharp daemon in-flight cap. Kernel send buffer is typically 64 KB; small JSON
# requests are ~200 bytes, so 64 files * 200 = 13 KB — well under the buffer.
SHARP_PIPELINE_CHUNK = 64
```

### Magick fix (sketch)

`MagickConverter.convert_batch` in `app/core/converters/magick_converter.py:104`:

```python
from ..config import MAGICK_MOGRIFY_CHUNK
...
for q, paths in groups.items():
    for i in range(0, len(paths), MAGICK_MOGRIFY_CHUNK):
        chunk = paths[i : i + MAGICK_MOGRIFY_CHUNK]
        # existing cmd-build using `chunk` instead of `paths`
        # existing subprocess.Popen + telemetry + fallback-to-individual
```

### Sharp fix (sketch)

`SharpConverter.convert_batch` in `app/core/converters/sharp_converter.py:312`:

```python
from ..config import SHARP_PIPELINE_CHUNK
...
for chunk_start in range(0, len(input_paths), SHARP_PIPELINE_CHUNK):
    chunk_inputs = input_paths[chunk_start : chunk_start + SHARP_PIPELINE_CHUNK]
    chunk_qualities = qualities[chunk_start : chunk_start + SHARP_PIPELINE_CHUNK]
    # send all requests in this chunk
    for in_path, q in zip(chunk_inputs, chunk_qualities):
        sock.sendall(...)
    # drain exactly len(chunk_inputs) responses before sending more
    self._drain_responses(sock, expected=len(chunk_inputs), buf=accumulator)
```

Extract `_drain_responses` as a helper that takes the socket, expected
count, and the running success/failure/errors accumulators.

### Tests

- Existing `tests/test_magick_batch.py` should continue passing.
- Add a test that exercises chunking: 500 fake paths with mocked `Popen`,
  assert `Popen.call_count == ceil(500 / MAGICK_MOGRIFY_CHUNK)` per
  quality group.
- For Sharp, mock the socket with a `BytesIO`-backed fake; assert the
  send/recv interleaving never sends more than `SHARP_PIPELINE_CHUNK`
  in-flight.

### Acceptance

- Tests cover chunking math.
- No behavior regression on small batches (< `*_CHUNK`).

---

## T5 — JXL-safe `_probe_quality` fallback + telemetry-survives-failure test (MEDIUM)

### Why

`BatchOrchestrator._probe_quality` in `app/batch_api/orchestrator.py:41-43`
returns hard-coded `80.0` on any metadata read error. For JXL, where
`quality` is `distance` in the range `0.0–15.0`, that's out-of-bounds and
will surface as a converter exception.

The interpolator's own fallback was already fixed (`heuristic_interpolator.py:57`
returns `1.0` for JXL). This is the exception path that wasn't updated.

### Files affected

| Path | Change |
|---|---|
| `app/batch_api/orchestrator.py` | Format-aware fallback in `_probe_quality` |
| `tests/test_orchestrator_summary_survives_failures.py` | (Created in T1) — extend |

### Fix

```python
def _probe_quality(self, path: str, req: BatchRequest) -> float:
    from PIL import Image
    try:
        with Image.open(path) as img:
            w, h = img.size
        return self.interpolator.get_interpolated_quality(
            req.category, req.target_format, req.tool, w, h
        )
    except Exception as e:
        log.error(f"Failed to read metadata for {path}: {e}")
        return 1.0 if req.target_format == "jxl" else 80.0
```

### Tests

Inside `tests/test_orchestrator_summary_survives_failures.py` (created in T1),
add a JXL-specific case:

```python
@pytest.mark.asyncio
async def test_probe_quality_falls_back_safely_for_jxl(...):
    # patch PIL.Image.open to raise; assert returned quality <= 15.0 for jxl
```

### Acceptance

- The orchestrator never feeds an out-of-range `distance` to a JXL encoder
  even when image headers are corrupt.

---

## T6 — Remove phantom `setup_sharp_portable.ps1` references (LOW)

### Why

`SharpConverter._ensure_daemon_running` references
`scripts\setup_sharp_portable.ps1` in three error strings:
`app/core/converters/sharp_converter.py:127, :167, :181`. The file does
not exist in the repo. Operators chase a phantom remediation.

### Fix

Replace the three error messages with a real remediation pointer to
INSTRUCTIONS.md §3.1 (Windows native prereqs) and the `npm install` step.
Sample:

```python
raise RuntimeError(
    "Sharp daemon requires Node.js. See INSTRUCTIONS.md §3.1 — install "
    "Node 20+ and run `npm install` from the project root."
)
```

### Acceptance

- `git grep setup_sharp_portable` returns no hits.

---

## T7 — Guard legacy `psycopg` imports (LOW)

### Why

Legacy modules at `app/core/calibrator.py`, `app/core/engine/phases/*`,
`app/core/db/repositories/{conversions,images,metrics,priors,pipeline,users}.py`,
`app/core/db/analytics.py`, `app/core/db/analytics_api.py`,
`app/core/db/export.py` all `import psycopg` unconditionally. The `[legacy]`
optional extra exists so anyone reviving the old monolith can install it,
but a future contributor adding `from ..calibrator import …` at the wrong
layer surfaces a confusing `ModuleNotFoundError`.

### Files affected

Each of:

- `app/core/db/repositories/conversions.py`
- `app/core/db/repositories/images.py`
- `app/core/db/repositories/metrics.py`
- `app/core/db/repositories/priors.py`
- `app/core/db/repositories/pipeline.py`
- `app/core/db/repositories/users.py`
- `app/core/db/analytics.py`
- `app/core/db/analytics_api.py`
- `app/core/db/export.py`
- `app/core/engine/phases/metrics.py`
- `app/core/engine/context.py`

### Fix

Replace `import psycopg` with:

```python
try:
    import psycopg
except ImportError:
    psycopg = None  # legacy module — install pip extras [legacy] to enable
```

Any module-level use of `psycopg.Connection` as a type annotation should
move under `if TYPE_CHECKING:` or become `"psycopg.Connection"` string
annotations.

### Acceptance

- `pip install -e .` (no extras) — imports of these modules now raise an
  intelligible error at first use, not at import.
- `pip install -e ".[legacy]"` — the legacy code still functions
  identically.

---

## T8 — Fix docker-compose Streamlit launch (LOW)

### Why

`docker-compose.yml` runs the GUI with
`["streamlit", "run", "-m", "app.web.batch_gui.main", "--server.port=8503"]`.
Streamlit's CLI does not accept `-m` as a flag. The native invocation in
INSTRUCTIONS.md §3.4 uses the correct pattern (`python -m streamlit run`).

### Fix

Either (preferred) create a thin wrapper script at repo root, e.g.
`gui_entry.py`:

```python
from app.web.batch_gui.main import main
main()
```

…and change the compose command to:

```yaml
command: ["streamlit", "run", "gui_entry.py", "--server.port=8503"]
```

Or change the compose command to the working `python -m streamlit run`
form:

```yaml
command: ["python", "-m", "streamlit", "run",
          "app/web/batch_gui/main.py", "--server.port=8503"]
```

### Acceptance

- `docker compose up pixelpivot-batch-gui` brings up the GUI without
  `ImportError: attempted relative import with no known parent package`.

---

## T9 — Reduce `TelemetryMonitor.children()` walk frequency (LOW)

### Why

`TelemetryMonitor._get_recursive_resources` in `app/core/telemetry.py:67`
calls `root.children(recursive=True)` every tick (default 250 ms). For
single-PID converters (Sharp daemon, `FFmpegProcess`) the tree is stable,
so the recursive walk is wasted overhead.

### Files affected

| Path | Change |
|---|---|
| `app/core/telemetry.py` | Snapshot children once, refresh on a slow cadence |

### Fix sketch

```python
class TelemetryMonitor:
    def __init__(self, ..., children_refresh_s: float = 1.0):
        ...
        self._children_refresh_s = children_refresh_s
        self._last_children_ts = 0.0
        self._cached_pids: set[int] = set()

    def _get_recursive_resources(self, pid):
        now = time.monotonic()
        if now - self._last_children_ts > self._children_refresh_s:
            try:
                root = psutil.Process(pid)
                self._cached_pids = {root.pid} | {c.pid for c in root.children(recursive=True)}
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._cached_pids = set()
            self._last_children_ts = now
        # use cached pids for this tick
        ...
```

Add `TELEMETRY_CHILDREN_REFRESH_S = 1.0` to `app/core/config.py`.

### Acceptance

- No regression in existing telemetry data shapes.
- Profiling shows fewer `psutil.children` calls per second.

---

## T10 — Failures sub-panel in GUI exposing `batch_errors` (LOW)

### Why

Once T1 lands, the new `batch_errors` table contains per-file failure
records. The GUI currently has no way to inspect them — making the data
write-only.

### Files affected

| Path | Change |
|---|---|
| `app/web/batch_gui/panels/history_panel.py` | New expander per row |
| `app/web/batch_gui/api_client.py` | `APIClient.get_failures(run_id)` |
| `app/batch_api/routes.py` | New endpoint `GET /api/v1/batch/{run_id}/errors` |
| `app/core/db/repositories/batch.py` | `BatchRepository.get_errors(run_id)` |

### Sketch

Repo:
```python
def get_errors(self, conn, batch_id: int, limit: int = 100) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT input_path, error, created_at FROM batch_errors "
            "WHERE batch_id = ? ORDER BY id LIMIT ?", (batch_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]
    finally:
        cur.close()
```

Route:
```python
@router.get("/batch/{run_id}/errors")
async def get_batch_errors(run_id: int):
    with get_connection() as conn:
        return repo.get_errors(conn, run_id)
```

History panel: for each row with `failure_count > 0`, render an
`st.expander("Failures (N)")` that lazy-loads the failures via the API
client.

### Acceptance

- New endpoint returns failure rows.
- GUI surfaces them on demand, without auto-loading for every batch.

---

## Dependency graph

```
T1 ── (enables) ──► T10 (GUI failures panel)
T1 ── (touches)  ──► converter contract — coordinate with T4
T2 ── (independent — fix any time)
T3 ── (independent — fix any time)
T4 ── (touches)  ──► converter contract — coordinate with T1
T5 ── (uses)     ──► T1's new test file
T6, T7, T8, T9   ── (independent)
```

Recommended landing order: **T2 → T3 → T1 (with T5's invariant test
appended) → T4 → T10 → T6/T7/T8/T9 in any order.**

T2 first because it's a five-line fix that closes a visible regression
introduced by the SQLite migration. T3 next because it's quick and the
behavior is currently silently wrong. T1 is the headline; ship the
invariant test (T5) inside the same PR. T4 then so the converter changes
land together if you want them in one PR. T10 surfaces what T1 wrote. The
LOW items mop up later.

---

## Out-of-scope (not actionable yet)

These were flagged in the audit but are either resolved or punted:

- `init_db()` startup wiring — **resolved** in `382ed52` (lifespan calls
  it, with structured error logging).
- Heuristic JXL fallback in the interpolator — **resolved** in `cabeed3`
  (returns `1.0` for JXL when no data).
- PyAV/PyAV-padding gymnastics in `FFmpegConverter` — **resolved** in
  `28c17e7`.
- Datetime SQLite-adapter deprecation warning — **resolved** in `382ed52`.
- Pydantic `req.dict()` deprecation — **resolved** in `382ed52`.

## Verification baseline

Before starting any task, current state must be:

- `git log -3 --oneline` shows `28c17e7 refactor(ffmpeg)...` on top of
  `382ed52 refactor(db)...` on top of `cabeed3 fix:...`.
- `python -m pytest tests/ --ignore=tests/test_avif_real_images.py --ignore=tests/test_real_assets_end_to_end.py --ignore=tests/test_integration_real_assets.py --ignore=tests/test_heuristic_gen.py` → 50 passed.
- `python -m pytest tests/ ... -W error::DeprecationWarning -W "error::pydantic.warnings.PydanticDeprecatedSince20"` → 50 passed.

Anything else means the tree has moved underneath this plan; re-baseline
before continuing.

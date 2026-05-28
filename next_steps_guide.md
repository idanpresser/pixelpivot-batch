# PixelPivot Batch — Next Steps Guide

Actionable work order derived from the architectural audit of `pixelpivot_batch/` against `CLAUDE.md`, `GEMINI.md`, and the root `README.md`.

Each item is independently mergeable. Items are listed in suggested execution order. Every fix lists the **file**, the **defect**, the **change**, and a **verify** step you can run before moving on.

---

## Context: where we are

- Two projects coexist in the repo:
  - `/app/` — legacy monolithic Streamlit calibration pipeline (described by root `README.md`)
  - `/pixelpivot_batch/` — new FastAPI + Streamlit microservice rewrite (described by `pixelpivot_batch/CLAUDE.md` and `GEMINI.md`)
- The rewrite is **untracked in git** (`git status` shows `?? pixelpivot_batch/`). Treat the whole directory as work-in-progress.
- This guide scopes to the rewrite only. Legacy cleanup is in Phase 4.

---

## Phase 0 — Unblock local & Docker builds (must do first)

### 0.1 Make `pyproject.toml` the dependency source of truth

**File:** `pixelpivot_batch/pyproject.toml`
**Defect:** `dependencies = []` while `app/requirements.txt` lists the real deps. A `pip install .` or `uv sync` installs nothing.

**Change:**
```toml
[project]
name = "pixelpivot-batch"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.27",
    "httpx>=0.27",
    "pydantic>=2.6",
    "psycopg[binary]>=3.1",
    "psycopg-pool>=3.2",
    "watchdog>=4.0",
    "streamlit>=1.32",
    "Pillow>=10.0",
    "numpy>=1.26",
    "pyvips>=2.2",
    "pandas>=2.2",
    "av>=12.0",
    "psutil>=5.9",
    "nvidia-ml-py>=12.535",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23", "respx>=0.21", "wand>=0.6"]
```
Delete `pixelpivot_batch/app/requirements.txt`.

In `pixelpivot_batch/Dockerfile`, replace:
```dockerfile
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir pytest pytest-asyncio respx psutil wand
```
with:
```dockerfile
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev]"
```

**Verify:**
```bash
cd pixelpivot_batch
uv sync   # or: pip install -e ".[dev]"
python -c "import fastapi, psycopg, streamlit, pyvips, av; print('ok')"
docker compose build
```

---

### 0.2 Stop bind-mount from clobbering `node_modules`

**File:** `pixelpivot_batch/docker-compose.yml` (services `pixelpivot-batch-api`, `pixelpivot-cli`)
**Defect:** `- .:/app` mounts the host working tree over the image, overwriting the Linux-built `node_modules` with whatever (often a Windows-native build) is on the host. The Sharp daemon will fail to load `sharp.node`.

**Change:** add an anonymous volume *after* the bind-mount so it wins:
```yaml
pixelpivot-batch-api:
  ...
  volumes:
    - .:/app
    - /app/node_modules    # <- preserve image contents
    - ./test_examples:/app/test_examples
    - ./out:/app/out
```
Apply the same line to `pixelpivot-cli`.

**Verify:**
```bash
docker compose up -d pixelpivot-batch-api
docker compose exec pixelpivot-batch-api node -e "require('sharp'); console.log('sharp ok')"
```

---

### 0.3 Add a real healthcheck to the API service

**File:** `pixelpivot_batch/docker-compose.yml` (service `pixelpivot-batch-api`)

**Change:**
```yaml
pixelpivot-batch-api:
  ...
  healthcheck:
    test: ["CMD-SHELL", "curl -fsS http://localhost:8000/ || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 10s
```
And make the GUI depend on it being healthy:
```yaml
pixelpivot-batch-gui:
  depends_on:
    pixelpivot-batch-api:
      condition: service_healthy
```

**Verify:** `docker compose up -d && docker compose ps` — API should show `(healthy)` before GUI starts.

---

## Phase 1 — Fix Sev-1 wiring defects (functional correctness)

### 1.1 Replace deprecated lifecycle hooks with `lifespan`, capture loop for hot-folder dispatch

**Files:**
- `pixelpivot_batch/app/batch_api/main.py`
- `pixelpivot_batch/app/batch_api/hot_folder.py`

**Defect:**
- `@app.on_event("startup"|"shutdown")` is deprecated in Starlette/FastAPI.
- `HotFolderHandler._trigger_batch` runs on a `threading.Timer` thread. `asyncio.get_event_loop()` in a non-main thread raises `RuntimeError` on Python 3.12+. The current `except RuntimeError` creates a fresh ephemeral event loop in the worker thread, which is *not* the uvicorn loop — `asyncio.run_coroutine_threadsafe` to that loop is meaningless. Hot-folder triggers are unreliable.

**Change — `main.py`:**
```python
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI

from .routes import router
from .hot_folder import init_hot_folder_manager
from .orchestrator import BatchOrchestrator


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    app.state.orchestrator = BatchOrchestrator()
    manager = init_hot_folder_manager(app.state.orchestrator, loop)
    manager.start()
    app.state.hot_folder_manager = manager
    try:
        yield
    finally:
        manager.stop()


app = FastAPI(title="PixelPivot Batch Engine", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")


@app.get("/")
async def root():
    return {"message": "PixelPivot Batch Engine API is running"}
```

**Change — `hot_folder.py`:**
```python
import threading
import asyncio
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from typing import Dict, Any, List

from .models import BatchRequest
from ..core.logger import get_logger
from ..core.db.repositories.batch import BatchRepository
from ..core.db.connection import get_connection

log = get_logger(__name__)


class HotFolderHandler(FileSystemEventHandler):
    def __init__(self, orchestrator, loop: asyncio.AbstractEventLoop,
                 config: Dict[str, Any], debounce_seconds: float = 5.0):
        self.orchestrator = orchestrator
        self.loop = loop
        self.config = config
        self.debounce_seconds = debounce_seconds
        self.timer = None
        self.lock = threading.Lock()
        self.repo = BatchRepository()

    def on_created(self, event):
        if event.is_directory:
            return
        self._reset_timer()

    def _reset_timer(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.debounce_seconds, self._trigger_batch)
            self.timer.daemon = True
            self.timer.start()

    def _trigger_batch(self):
        try:
            with get_connection() as conn:
                run_id = self.repo.create_run(
                    conn,
                    source_dir=self.config["source_dir"],
                    target_dir=self.config["target_dir"],
                    target_format=self.config["target_format"],
                    tool=self.config["tool"],
                    trigger_type="hot_folder",
                )
            request = BatchRequest(
                source_dir=self.config["source_dir"],
                target_dir=self.config["target_dir"],
                target_format=self.config["target_format"],
                tool=self.config["tool"],
                category=self.config.get("category", "general"),
                trigger_type="hot_folder",
            )
            asyncio.run_coroutine_threadsafe(
                self.orchestrator.execute_batch(run_id, request),
                self.loop,
            )
        except Exception as e:
            log.error(f"Failed to trigger hot folder batch: {e}")


class HotFolderManager:
    def __init__(self, orchestrator, loop: asyncio.AbstractEventLoop):
        self.orchestrator = orchestrator
        self.loop = loop
        self.observer = Observer()
        self.configs: List[Dict[str, Any]] = []
        self.handlers: List[HotFolderHandler] = []

    def add_hot_folder(self, config: Dict[str, Any]) -> int:
        handler = HotFolderHandler(self.orchestrator, self.loop, config)
        self.observer.schedule(handler, config["source_dir"], recursive=False)
        self.handlers.append(handler)
        self.configs.append(config)
        return len(self.configs) - 1

    def list_hot_folders(self) -> List[Dict[str, Any]]:
        return self.configs

    def start(self):
        log.info("Starting Hot Folder Watchdog...")
        self.observer.start()

    def stop(self):
        self.observer.stop()
        if self.observer.is_alive():
            self.observer.join()


_manager: "HotFolderManager | None" = None


def init_hot_folder_manager(orchestrator, loop: asyncio.AbstractEventLoop) -> HotFolderManager:
    global _manager
    _manager = HotFolderManager(orchestrator, loop)
    return _manager


def get_hot_folder_manager() -> HotFolderManager:
    if _manager is None:
        raise RuntimeError("HotFolderManager not initialized.")
    return _manager
```

**Verify:**
```bash
# Start the stack
docker compose up -d
# Register a hot folder
curl -X POST http://localhost:8000/api/v1/hotfolder/register \
  -H 'Content-Type: application/json' \
  -d '{"source_dir":"/app/test_examples/hot","target_dir":"/app/out","target_format":"webp","tool":"magick"}'
# Drop an image into the folder, wait 5s, then:
curl http://localhost:8000/api/v1/batch/history | jq '.[0]'
# Expect a row with trigger_type="hot_folder" and status="completed"
```

---

### 1.2 Inject a single `BatchOrchestrator` via DI

**File:** `pixelpivot_batch/app/batch_api/routes.py`
**Defect:** `BatchOrchestrator()` is constructed on every `/batch/start` call — reloads `heuristic_table.json`, rebuilds all converters, and produces a parallel instance whose circuit-breaker state diverges from the hot-folder orchestrator built at startup.

**Change:**
```python
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from .models import BatchRequest, HotFolderRequest
from ..core.db.repositories.batch import BatchRepository
from ..core.db.connection import get_connection
from .hot_folder import get_hot_folder_manager
from .orchestrator import BatchOrchestrator

router = APIRouter()
repo = BatchRepository()


def get_orchestrator(request: Request) -> BatchOrchestrator:
    return request.app.state.orchestrator


@router.post("/batch/start")
async def start_batch(
    req: BatchRequest,
    bg_tasks: BackgroundTasks,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    try:
        with get_connection() as conn:
            run_id = repo.create_run(
                conn,
                source_dir=req.source_dir,
                target_dir=req.target_dir,
                target_format=req.target_format,
                tool=req.tool,
                trigger_type=req.trigger_type,
            )
        bg_tasks.add_task(orchestrator.execute_batch, run_id, req)
        return {"run_id": run_id, "status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Verify:** existing `tests/api/test_routes.py` should still pass with one update — replace `patch("app.batch_api.routes.BatchOrchestrator")` with overriding the dependency:
```python
from app.batch_api.main import app
from app.batch_api.routes import get_orchestrator

app.dependency_overrides[get_orchestrator] = lambda: mock_orchestrator
```

---

### 1.3 Register all converters and constrain the request with enums

**Files:**
- `pixelpivot_batch/app/batch_api/orchestrator.py`
- `pixelpivot_batch/app/batch_api/models.py`
- `pixelpivot_batch/app/core/heuristic_table.json`

**Defect:**
- The orchestrator registers only `magick`, `ffmpeg`, `vips`. `SharpConverter` and `FFmpegNvencConverter` are implemented but unreachable.
- `run_panel.py` offers `sharp` in its dropdown → 500 on submit.
- `BatchRequest.tool: str` accepts arbitrary strings.
- `heuristic_table.json` has no `sharp` or `ffmpeg_nvenc` rows → interpolator falls back to default 80.

**Change — `orchestrator.py`:**
```python
from ..core.converters.magick_converter import MagickConverter
from ..core.converters.ffmpeg_converter import FFmpegConverter
from ..core.converters.vips_converter import VipsConverter
from ..core.converters.sharp_converter import SharpConverter
from ..core.converters.ffmpeg_nvenc_converter import FFmpegNvencConverter

class BatchOrchestrator:
    def __init__(self):
        self.repo = BatchRepository()
        self.interpolator = HeuristicInterpolator(APP_ROOT / "core" / "heuristic_table.json")
        self.converters = {
            "magick":       MagickConverter(magick_path="magick"),
            "ffmpeg":       FFmpegConverter(ffmpeg_path="ffmpeg"),
            "vips":         VipsConverter(),
            "sharp":        SharpConverter(port=8765),
            "ffmpeg_nvenc": FFmpegNvencConverter(ffmpeg_path="ffmpeg"),
        }
```

**Change — `models.py`:**
```python
from enum import Enum
from typing import Literal, Optional
from datetime import datetime
from pydantic import BaseModel


class Tool(str, Enum):
    magick = "magick"
    ffmpeg = "ffmpeg"
    vips = "vips"
    sharp = "sharp"
    ffmpeg_nvenc = "ffmpeg_nvenc"


TargetFormat = Literal["webp", "avif", "jxl"]


class BatchRequest(BaseModel):
    source_dir: str
    target_dir: str
    target_format: TargetFormat
    tool: Tool
    category: str = "general"
    trigger_type: str = "manual"


class HotFolderRequest(BaseModel):
    source_dir: str
    target_dir: str
    target_format: TargetFormat
    tool: Tool
    category: str = "general"


class BatchStatusResponse(BaseModel):
    run_id: int
    status: str
    total_images: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    summary: Optional[dict] = None
```

**Change — `heuristic_table.json`:** Add `sharp` and `ffmpeg_nvenc` entries to every `category × bucket × format` cell that already has the other tools. Use the existing `vips` values as a placeholder until real calibration data lands. Example for `general/small/avif`:
```json
"avif": {"ffmpeg": 28, "magick": 82, "vips": 82, "sharp": 82, "ffmpeg_nvenc": 30}
```
(Repeat for every cell — script-generate it rather than hand-edit.)

**Verify:**
```bash
curl -X POST http://localhost:8000/api/v1/batch/start \
  -H 'Content-Type: application/json' \
  -d '{"source_dir":"/app/test_examples","target_dir":"/app/out","target_format":"webp","tool":"sharp"}'
# Expect: 200 with run_id, NOT 500. Poll status → completed.
# Try an invalid tool:
curl -X POST http://localhost:8000/api/v1/batch/start \
  -H 'Content-Type: application/json' \
  -d '{"source_dir":"/x","target_dir":"/y","target_format":"webp","tool":"bogus"}'
# Expect: 422 (pydantic validation error), NOT 500.
```

---

### 1.4 Move Sharp default port off 8000

**File:** `pixelpivot_batch/app/core/converters/sharp_converter.py:19`
**Defect:** default `port=8000` collides with the FastAPI server.

**Change:**
```python
def __init__(self, port: int = 8765):
    super().__init__()
    self.port = port
    self.host = "127.0.0.1"
    self.daemon_process = None
    self._socket = None
```

**Verify:** `docker compose exec pixelpivot-batch-api ss -ltn` (or `netstat -ln`) shows FastAPI on 8000 and the Sharp daemon on 8765 after a `sharp` batch is invoked.

---

### 1.5 Compute `yield_mb_sec` and `savings_pct` honestly

**File:** `pixelpivot_batch/app/batch_api/orchestrator.py:94-113`
**Defect:** dashboard receives hardcoded zeros (`yield_mb_sec=0.0`, `savings_pct=0.0`).

**Change — after the converter returns, before `save_summary`:**
```python
input_bytes = 0
for p in input_paths:
    try:
        input_bytes += os.path.getsize(p)
    except OSError:
        pass

output_bytes = 0
target_dir_path = Path(request.target_dir)
for p in input_paths:
    out = target_dir_path / f"{Path(p).stem}.{request.target_format}"
    if out.exists():
        output_bytes += out.stat().st_size

duration_s = max(duration_ms / 1000.0, 1e-3)
yield_mb_sec = (output_bytes / (1024 * 1024)) / duration_s
savings_pct = (1.0 - output_bytes / input_bytes) * 100.0 if input_bytes else 0.0

with get_connection() as conn:
    self.repo.save_summary(
        conn,
        batch_id=run_id,
        duration_ms=duration_ms,
        cpu_avg_pct=telemetry.get("cpu_avg", 0.0),
        cpu_peak_pct=telemetry.get("cpu_peak", 0.0),
        ram_peak_mb=telemetry.get("ram_peak", 0.0),
        yield_mb_sec=yield_mb_sec,
        savings_pct=savings_pct,
        success_count=result["success_count"],
        failure_count=result["failure_count"],
    )
    self.repo.update_status(conn, run_id, "completed", total_images=len(input_paths))
```

**Verify:** run any batch, then `SELECT yield_mb_sec, savings_pct FROM batch_summary;` should show non-zero values. The History panel's bar chart will show real data.

---

## Phase 2 — Hardening (architectural correctness + perf)

### 2.1 Unify GUI imports

**Files:** `pixelpivot_batch/app/web/batch_gui/main.py` and `panels/*.py`
**Defect:** `main.py` uses `from api_client import APIClient` (flat); panels use `from ..api_client import APIClient` (package-relative). Works only because `streamlit run` happens to inject paths that make both resolve.

**Change — `main.py`:**
```python
from .api_client import APIClient
from .panels.run_panel import render_run_panel
from .panels.history_panel import render_history_panel
from .panels.hot_folder_panel import render_hot_folder_panel
from .theme_engine import inject_theme_css, ORANGE
```

**Change — `docker-compose.yml` (and `CLAUDE.md` snippet for local dev):**
```yaml
pixelpivot-batch-gui:
  command: ["streamlit", "run", "-m", "app.web.batch_gui.main", "--server.port=8503"]
```
For local dev:
```bash
PYTHONPATH=. streamlit run -m app.web.batch_gui.main --server.port=8503
```

**Verify:** delete every `__pycache__/` under `app/web/batch_gui/`, then start the GUI fresh and load each tab.

---

### 2.2 Parallelize image-header probing

**File:** `pixelpivot_batch/app/batch_api/orchestrator.py:59-71`
**Defect:** 1,000 sequential `PIL.Image.open()` calls block the worker thread before any conversion begins.

**Change:**
```python
from concurrent.futures import ThreadPoolExecutor
from PIL import Image

def _probe_quality(self, path: str, req: BatchRequest) -> float:
    try:
        with Image.open(path) as img:
            w, h = img.size
        return self.interpolator.get_interpolated_quality(
            req.category, req.target_format, req.tool, w, h
        )
    except Exception as e:
        log.error(f"Failed to read metadata for {path}: {e}")
        return 80.0

# inside execute_batch, replace the qualities-building loop with:
probe_workers = min(32, (os.cpu_count() or 4) * 4)
with ThreadPoolExecutor(max_workers=probe_workers) as ex:
    qualities = list(ex.map(lambda p: self._probe_quality(p, request), input_paths))
```

**Verify:** time the request for a 500-image folder before and after — expect 5-10x faster pre-flight on SSD.

---

### 2.3 Truncate captured stderr in `_run_subprocess`

**File:** `pixelpivot_batch/app/core/converters/base.py:119`
**Defect:** Full FFmpeg stderr (often >100 KB per encode) is stored verbatim in per-conversion results and surfaces in the GUI JSON payload.

**Change:** add a helper at the top of `base.py`:
```python
def _truncate(s: str | None, limit: int = 2048) -> str | None:
    if not s:
        return s
    return s if len(s) <= limit else s[:limit] + f"... ({len(s) - limit} bytes truncated)"
```
And:
```python
error = _truncate(stderr) if not success and not error else error
```

**Verify:** run a deliberately failing batch (e.g. `target_format="jxl"` on a tool that doesn't support it). The `errors` array in the batch summary should be capped to ~2 KB per entry.

---

### 2.4 Drop legacy constants from `config.py`

**File:** `pixelpivot_batch/app/core/config.py`
**Defect:** `TARGET_SSIM`, `MAX_CALIBRATION_ITERS`, `TEMP_SUBDIR`, `MAX_RETRIES_CONVERSION`, `MASSIVE_IMAGE_THRESHOLD`, `HUGE_IMAGE_THRESHOLD`, `VRAM_SAFE_THRESHOLD`, `CALIBRATION_SSIM_TOLERANCE`, `PLAYWRIGHT_NAV_TIMEOUT_MS`, `META_SCORE_WEIGHTS_*` are calibration-pipeline leftovers. The batch engine never imports them.

**Change:** `grep -R "TARGET_SSIM\|META_SCORE_WEIGHTS\|PLAYWRIGHT_NAV_TIMEOUT_MS\|MASSIVE_IMAGE_THRESHOLD\|HUGE_IMAGE_THRESHOLD\|VRAM_SAFE_THRESHOLD\|CALIBRATION_SSIM_TOLERANCE\|MAX_CALIBRATION_ITERS" pixelpivot_batch/app pixelpivot_batch/tests` — if no hits in the batch engine, delete those constants. Keep:
```python
FFMPEG_TIMEOUT = 120
TELEMETRY_INTERVAL = 0.25
MAX_LOG_BUFFER = 500
RESULT_LIMIT_DASHBOARD = 100
PERIODIC_EXPORT_BATCH_SIZE = 50
TELEMETRY_BATCH_SIZE = 20
TELEMETRY_QUEUE_TIMEOUT = 2.0
```

**Verify:** `pytest` still green.

---

### 2.5 Add hot-folder unregister + stable IDs

**File:** `pixelpivot_batch/app/batch_api/hot_folder.py` and `routes.py`
**Defect:** `add_hot_folder` returns the list index. There is no remove endpoint. Indexes invalidate as soon as one is removed.

**Change — `hot_folder.py`:**
```python
import uuid

class HotFolderManager:
    def __init__(self, orchestrator, loop):
        self.orchestrator = orchestrator
        self.loop = loop
        self.observer = Observer()
        self.watchers: dict[str, dict] = {}  # watcher_id -> {handler, watch, config}

    def add_hot_folder(self, config: Dict[str, Any]) -> str:
        handler = HotFolderHandler(self.orchestrator, self.loop, config)
        watch = self.observer.schedule(handler, config["source_dir"], recursive=False)
        watcher_id = uuid.uuid4().hex
        self.watchers[watcher_id] = {"handler": handler, "watch": watch, "config": config}
        return watcher_id

    def remove_hot_folder(self, watcher_id: str) -> bool:
        entry = self.watchers.pop(watcher_id, None)
        if not entry:
            return False
        self.observer.unschedule(entry["watch"])
        return True

    def list_hot_folders(self) -> List[Dict[str, Any]]:
        return [{"watcher_id": wid, **entry["config"]} for wid, entry in self.watchers.items()]
```

**Change — `routes.py`:**
```python
@router.delete("/hotfolder/{watcher_id}")
async def unregister_hot_folder(watcher_id: str):
    manager = get_hot_folder_manager()
    if not manager.remove_hot_folder(watcher_id):
        raise HTTPException(status_code=404, detail="Watcher not found")
    return {"status": "removed"}
```

**Change — `hot_folder_panel.py`:** show the UUID and add a "Stop" button per row that calls `client.unregister_hot_folder(wid)`.

**Verify:**
```bash
WID=$(curl -s -X POST http://localhost:8000/api/v1/hotfolder/register \
  -H 'Content-Type: application/json' \
  -d '{"source_dir":"/tmp","target_dir":"/tmp","target_format":"webp","tool":"magick"}' | jq -r .watcher_id)
curl -X DELETE http://localhost:8000/api/v1/hotfolder/$WID
```

---

## Phase 3 — Documentation reconciliation

### 3.1 Pick one version per dependency and align all three docs

**Files:** `pixelpivot_batch/CLAUDE.md`, `pixelpivot_batch/GEMINI.md`, root `README.md`

**Defects:**
- `GEMINI.md` says "Python 3.9+"; `pyproject.toml` says `>=3.12`.
- Root `README.md` says "PostgreSQL 16"; `docker-compose.yml` ships `postgres:15`.
- `GEMINI.md` claims fonts are baked in; the Dockerfile copies none.

**Change:**
- Decide once: **Python 3.12+, PostgreSQL 15**. Update `GEMINI.md` and root `README.md` to match.
- Remove the "fonts baked into the Docker images" sentence from `GEMINI.md` (or actually `COPY` a font into the image if it's needed by a tool that has not yet been added).

### 3.2 Scope the root README

**File:** root `README.md`
**Defect:** It describes the legacy monolith (`/app/`), but a reader landing on the repo will assume it documents the whole thing — including `pixelpivot_batch/`.

**Change:** Add a banner at the top of the root `README.md`:
```markdown
> ⚠️ **Two projects in this repo.** This README documents the legacy monolithic
> Streamlit calibration pipeline under `/app/`. For the new FastAPI batch
> microservice rewrite, see **[`pixelpivot_batch/`](./pixelpivot_batch/)** and
> its [`CLAUDE.md`](./pixelpivot_batch/CLAUDE.md).
```

### 3.3 Commit the rewrite

**Defect:** `pixelpivot_batch/` is entirely untracked. The audit you just received would be impossible from a fresh clone of `main`.

**Change:**
1. Add a `pixelpivot_batch/.gitignore` covering `.venv/`, `__pycache__/`, `node_modules/`, `out/`, `*.db`.
2. `git add pixelpivot_batch/` and commit on a feature branch.
3. Open a PR titled something like `feat: introduce batch microservice (initial drop)` and include this guide as the PR description.

---

## Phase 4 — Legacy disposition (defer until Phase 0-2 land)

Decide the fate of `/app/` (the calibration monolith):

**Option A — Freeze and isolate.** Move it to `legacy/app/`, drop its `docker-compose.yml`, mark it read-only. The batch engine becomes the only deployable surface.

**Option B — Coexist.** Keep both. Document the boundary in a top-level `ARCHITECTURE.md`. Each project gets its own `docker-compose.yml` (already true).

**Option C — Migrate features over.** Port calibration sweep and analytics dashboard into the batch project as additional phases/services. Retire `/app/` after migration. Highest engineering cost; cleanest long-term repo.

Pick before any further changes touch both projects — refactors that span the seam are wasted work otherwise.

---

## Checklist

Print, work top-down, tick as you go.

**Phase 0 — Unblock builds**
- [ ] 0.1 Populate `pyproject.toml.dependencies`; delete `app/requirements.txt`; update Dockerfile.
- [ ] 0.2 Add anonymous `node_modules` volume in `docker-compose.yml`.
- [ ] 0.3 Add API healthcheck; make GUI wait on `service_healthy`.

**Phase 1 — Sev-1 wiring**
- [ ] 1.1 Convert to `lifespan`; pass uvicorn loop into `HotFolderManager`.
- [ ] 1.2 Inject single `BatchOrchestrator` via `Depends`.
- [ ] 1.3 Register `sharp` and `ffmpeg_nvenc`; add `Tool` enum + `Literal` format; extend `heuristic_table.json`.
- [ ] 1.4 Move Sharp default port to 8765.
- [ ] 1.5 Compute real `yield_mb_sec` and `savings_pct`.

**Phase 2 — Hardening**
- [ ] 2.1 Unify GUI imports; launch with `streamlit run -m`.
- [ ] 2.2 Parallel image-header probing.
- [ ] 2.3 Truncate stderr in subprocess wrapper.
- [ ] 2.4 Remove dead calibration constants from `config.py`.
- [ ] 2.5 UUID hot-folder IDs + DELETE endpoint + GUI Stop button.

**Phase 3 — Docs**
- [ ] 3.1 Align Python/Postgres versions across all three `*.md` files.
- [ ] 3.2 Add "two projects" banner to root `README.md`.
- [ ] 3.3 Commit `pixelpivot_batch/` on a feature branch.

**Phase 4 — Legacy**
- [ ] 4.0 Decide A / B / C for `/app/` future before further cross-project refactors.

---

## Risk notes

- **Don't reorder phases.** Phase 1 fixes assume Phase 0's dependency install works.
- **Phase 1.3 is the riskiest.** It touches the request schema (breaking change for any existing API client) and the heuristic table (data migration). Stage it behind a feature flag if you have downstream callers; otherwise ship while the API has no production consumers.
- **Phase 2.2 may surface latent bugs** in `_probe_quality` when called on torn/corrupt files concurrently. Watch the log for `Failed to read metadata`. Cap the worker count; don't let it scale past 32.
- **Phase 4 is a strategy decision, not a code change.** Make it deliberately with the team.

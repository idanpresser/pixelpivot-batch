# Architectural Blueprint: PixelPivot High-Throughput Batch Engine

## 1. System Topology Overview

We are shifting from a monolithic synchronous pipeline to a **Decoupled Microservice Architecture** for batch processing:

* **FastAPI Backend (`app/batch_api`)**: A headless REST API responsible for orchestration, hot-folder watching, and database interaction.
* **Streamlit Frontend (`app/web/batch_gui`)**: A lightweight, standalone GUI that communicates exclusively via REST.
* **Batch Converter Layer (`app/core/converters`)**: Extended adapters utilizing native batching (e.g., `mogrify`), in-process loops (PyAV, PyVips), or persistent sockets (Sharp) to eliminate per-image boot overhead.

---

## 2. File & Directory Structure Additions

```text
app/
├── batch_api/                        # NEW: Headless FastAPI Backend
│   ├── __init__.py
│   ├── main.py                       # FastAPI application & lifecycle
│   ├── routes.py                     # REST endpoints
│   ├── models.py                     # Pydantic schemas
│   ├── orchestrator.py               # Batch job runner & telemetry aggregator
│   └── hot_folder.py                 # Watchdog observer for auto-processing
├── core/
│   ├── db/
│   │   ├── repositories/
│   │   │   └── batch.py              # NEW: DB operations for batch tables
│   ├── heuristic_interpolator.py     # NEW: 2D interpolation logic for qualities
├── web/
│   ├── batch_gui/                    # NEW: Standalone Streamlit UI
│   │   ├── main.py                   # UI Entry point
│   │   ├── api_client.py             # REST client wrapper
│   │   └── panels/
│   │       ├── run_panel.py          # Manual path selection & trigger
│   │       ├── monitor_panel.py      # API polling for active batches
│   │       └── hot_folder_panel.py   # Configure watchdog directories
```

---

## 3. Database Schema Updates (`app/core/db/schema.py`)

We will add new tables optimized for high-throughput logging, preserving the existing analytics tables.

```sql
-- Represents a single batch job (manual or hot-folder triggered)
CREATE TABLE IF NOT EXISTS batch_runs (
    id              SERIAL PRIMARY KEY,
    source_dir      TEXT NOT NULL,
    target_dir      TEXT NOT NULL,
    target_format   TEXT NOT NULL,
    tool            TEXT NOT NULL,
    trigger_type    TEXT NOT NULL, -- 'manual' or 'hot_folder'
    status          TEXT NOT NULL, -- 'running', 'completed', 'failed'
    total_images    INTEGER DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP WITH TIME ZONE
);

-- Single aggregated row per batch run
CREATE TABLE IF NOT EXISTS batch_summary (
    batch_id        INTEGER PRIMARY KEY REFERENCES batch_runs(id) ON DELETE CASCADE,
    duration_ms     DOUBLE PRECISION,
    cpu_avg_pct     DOUBLE PRECISION,
    cpu_peak_pct    DOUBLE PRECISION,
    ram_peak_mb     DOUBLE PRECISION,
    yield_mb_sec    DOUBLE PRECISION, -- (Input Size - Output Size) / Duration
    savings_pct     DOUBLE PRECISION,
    success_count   INTEGER,
    failure_count   INTEGER
);
```

---

## 4. Core Implementation Headers

### A. The Heuristic Interpolator (`app/core/heuristic_interpolator.py`)

Implements Choice #5. Smooths the quality curve between standard resolution buckets using linear interpolation based on total Megapixels.

```python
class HeuristicInterpolator:
    """
    Interpolates optimal image quality based on exact pixel counts, 
    averaging between the two nearest pre-calculated resolution buckets.
    """
    def __init__(self, heuristic_table_path: Path):
        self.table = self._load_table(heuristic_table_path)
        # Map buckets to representative Megapixel centers for mathematical interpolation
        self.bucket_centers = {
            "small": 0.25,   # < 0.5MP
            "medium": 1.25,  # 0.5 - 2MP
            "large": 5.0,    # 2 - 8MP
            "xlarge": 12.0   # > 8MP
        }

    def get_interpolated_quality(
        self, category: str, format: str, tool: str, width: int, height: int
    ) -> float:
        """
        Calculates exact MP, finds bounding buckets (e.g., Medium and Large), 
        and computes a weighted average of their quality scores.
        """
        pass
```

### B. Converter Batch Interfaces (`app/core/converters/base.py`)

Applying the Open/Closed principle: we extend the base class without breaking the existing `.convert()` method.

```python
class BaseConverter(ABC):
    # ... existing methods ...

    @abstractmethod
    def convert_batch(
        self, 
        input_paths: List[str], 
        output_dir: str, 
        target_format: str, 
        qualities: List[float]
    ) -> Dict[str, Any]:
        """
        Process multiple images efficiently.
        Returns: {
            "success_count": int, 
            "failure_count": int, 
            "duration_ms": float, 
            "errors": List[str]
        }
        """
        pass
```

### C. Specific Converter Implementations

* **`MagickConverter`**: Groups images by exact quality float, then executes native `mogrify -path <outdir> -format <fmt> -quality <q> <files>`.
* **`FFmpegConverter`**: Uses an in-process thread pool executing `_convert_via_pyav` to bypass subprocess spawning completely.
* **`SharpConverter`**: Opens a *single* persistent socket connection to `sharp_daemon.js` and streams a batch payload (JSON array) or rapid-fires JSON lines, waiting for aggregate fulfillment.

### D. Headless Batch Orchestrator (`app/batch_api/orchestrator.py`)

Manages the batch execution, interacts with `TelemetryMonitor`, and aggregates the final data.

```python
class BatchOrchestrator:
    def __init__(self, runner: Runner, interpolator: HeuristicInterpolator):
        self.runner = runner
        self.interpolator = interpolator

    async def execute_batch(self, run_id: int, request: BatchRequest) -> None:
        """
        1. Scans source_dir for images.
        2. Calculates target qualities using HeuristicInterpolator.
        3. Starts TelemetryMonitor (in-memory only).
        4. Calls converter.convert_batch().
        5. Stops TelemetryMonitor.
        6. Calculates Yield (MB/s) and overall Savings %.
        7. Writes aggregate row to `batch_summary` table.
        8. Moves files to target_dir (if hot_folder mode) or handles lifecycle.
        """
        pass
```

### E. FastAPI Application (`app/batch_api/main.py` & `routes.py`)

A high-performance async REST interface.

```python
from fastapi import FastAPI, BackgroundTasks
from .models import BatchRequest, BatchStatusResponse
from .hot_folder import HotFolderManager

app = FastAPI(title="PixelPivot Batch Engine")
hot_folder_manager = HotFolderManager()

@app.on_event("startup")
async def startup_event():
    # Start the watchdog threads for configured hot folders
    hot_folder_manager.start_watchers()

@app.post("/api/v1/batch/start")
async def start_batch(req: BatchRequest, bg_tasks: BackgroundTasks):
    """Triggers an arbitrary path batch job."""
    # 1. Create batch_runs row -> get run_id
    # 2. bg_tasks.add_task(orchestrator.execute_batch, run_id, req)
    # 3. Return {"run_id": run_id, "status": "queued"}
    pass

@app.get("/api/v1/batch/status/{run_id}")
async def get_batch_status(run_id: int) -> BatchStatusResponse:
    """Returns DB state and summary if completed."""
    pass
```

### F. Hot Folder Watcher (`app/batch_api/hot_folder.py`)

Uses `watchdog` to monitor specific directories without locking the API.

```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class HotFolderHandler(FileSystemEventHandler):
    """
    Listens for new images. Implements a debouncer (e.g., waits 5 seconds 
    after the last file write) to group files into a single batch, then 
    fires a request to the BatchOrchestrator.
    """
    def on_created(self, event):
        pass
```

### G. Standalone Streamlit GUI (`app/web/batch_gui/main.py`)

A decoupled frontend that never runs heavy computations locally.

```python
import streamlit as st
import requests

def render_batch_gui():
    st.title("PixelPivot // Production Batch Engine")
    
    tabs = st.tabs(["MANUAL RUN", "HOT FOLDERS", "HISTORY & YIELD"])
    
    with tabs[0]:
        # Form: Source Dir, Target Dir, Format, Tool
        # Action: POST to FastAPI -> switch to Polling state
        pass
        
    with tabs[1]:
        # Form: Register a new input/output pair for the Watchdog observer
        # Action: POST to FastAPI /api/v1/hotfolder/register
        pass
        
    with tabs[2]:
        # Queries batch_summary table.
        # Displays aggregated charts: Yield MB/s over time, Peak RAM usage per tool.
        pass
```

---

## 5. Docker Infrastructure Modifications (`docker-compose.yml`)

We will add two new services to the Docker Compose stack to support the decoupled architecture.

```yaml
  # NEW: FastAPI Headless Engine
  pixelpivot-batch-api:
    image: pixelpivot:latest
    container_name: pixelpivot_batch_api
    command: ["uvicorn", "app.batch_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
    ports:
      - "8000:8000"
    volumes:
      - ./dataset:/workspace/dataset
      - ./data:/workspace/data
    depends_on:
      db:
        condition: service_healthy

  # NEW: Standalone Batch GUI
  pixelpivot-batch-gui:
    image: pixelpivot:latest
    container_name: pixelpivot_batch_gui
    command: ["streamlit", "run", "app/web/batch_gui/main.py", "--server.port=8503"]
    ports:
      - "8503:8503"
    environment:
      - BATCH_API_URL=http://pixelpivot-batch-api:8000
    depends_on:
      - pixelpivot-batch-api
```

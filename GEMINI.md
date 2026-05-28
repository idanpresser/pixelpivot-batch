# PixelPivot Batch Engine

High-throughput, decoupled microservice system for image conversion and telemetry collection.

## Project Overview

PixelPivot Batch Engine is designed for processing large volumes of images efficiently. It utilizes a **FastAPI backend** for orchestration and a **Streamlit frontend** for monitoring and manual control. The system is built around a pluggable converter architecture that supports native batching (e.g., ImageMagick's `mogrify`) and in-process processing (e.g., PyAV, PyVips).

### Key Technologies
- **Backend:** FastAPI (Python 3.12+)
- **Frontend:** Streamlit
- **Database:** SQLite 3 (WAL mode enabled)
- **Image Tools:** ImageMagick, FFmpeg (PyAV), libvips (pyvips), Sharp (Node.js daemon)
- **Deployment:** Docker & Docker Compose

## Architecture

```text
Streamlit GUI (Port 8503)
    └── REST (HTTP/JSON) → FastAPI Backend (Port 8000)
                               ├── BatchOrchestrator
                               ├── HotFolderManager (Watchdog)
                               └── Converters Layer (BaseConverter ABC)
                                             └── SQLite DB (WAL Mode)
```

- **`app/batch_api`**: Headless REST API and orchestration logic.
- **`app/web/batch_gui`**: Standalone Streamlit interface.
- **`app/core/converters`**: Individual tool adapters implementing the `BaseConverter` interface.
- **`app/core/heuristic_interpolator.py`**: Linear interpolation of image quality based on Megapixel resolution buckets.
- **`app/core/db`**: SQLite schema and repository layer.

## Building and Running

### Using WSL & Docker (Recommended for Windows)
1. **Launch the Stack:**
   ```bash
   bash scripts/wsl_start.sh
   ```
   This script fixes line endings, builds the images within WSL, and starts all services.

2. **Access Interfaces:**
   - **GUI:** `http://localhost:8503`
   - **API:** `http://localhost:8000`
   - **CLI:** `docker exec -it pixelpivot_cli bash`

### Air-Gapped Deployment
The PixelPivot Batch Engine is fully self-contained for offline use.

1. **Export for Air-Gap:**
   ```bash
   bash scripts/export_airgap.sh
   ```
   This bundles the API, GUI, and CLI into `.tar.gz` files in `out/airgap_bundle/`.

2. **Deploy Offline:**
   Transfer the bundle to the secure machine and run:
   ```bash
   docker load < pixelpivot-api.tar.gz
   docker load < pixelpivot-gui.tar.gz
   docker load < pixelpivot-cli.tar.gz
   docker compose up -d
   ```


### Key Environment Variables
| Variable | Description | Default |
|---|---|---|
| `PIXELPIVOT_DB_PATH` | Path to SQLite database file | `./data/pixelpivot.db` |
| `BATCH_API_URL` | API endpoint for the GUI | `http://localhost:8000/api/v1` |
| `PIXELPIVOT_DATASET_DIR` | Root directory for source images | `./dataset` |

## Development Conventions

### Coding Style
- Follow PEP 8.
- Use `app/core/logger.py` for all logging.
- Global constants (timeouts, weights) must reside in `app/core/config.py`.

### Converters
- All converters must inherit from `BaseConverter`.
- `convert_batch()` should be implemented for efficiency; otherwise, it falls back to a thread pool of `convert()` calls.
- `quality` is a `Union[int, float]`. Never cast to `int` globally, as JXL uses float distance.

### Database
- Use `app/core/db/connection.py` to get connections.
- Schema is managed in `app/core/db/schema.py`. Call `init_db()` to apply migrations/init.

### Testing
- Run all tests with `pytest`.
- Integration tests involving real assets are located in `tests/test_integration_real_assets.py`.
- Mock database connections for unit tests.

## Key Files
- `app/core/config.py`: Centralized engineering constants.
- `app/core/converters/base.py`: Abstract base class for all conversion logic.
- `app/batch_api/orchestrator.py`: Logic for executing and logging batch jobs.
- `app/core/heuristic_table.json`: Quality lookup table for interpolation.
- `docs/blueprint.md`: Detailed architectural design.

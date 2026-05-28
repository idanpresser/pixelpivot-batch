# PixelPivot Batch Engine — Operations Guide

This guide is the **operator-facing** manual for deploying and running the engine. The system is designed to run in **two parity modes**:

1. **Docker container/image** — production deployment, air-gap bundling, WSL2/Linux hosts.
2. **Windows-native app** — direct execution on a Windows workstation. No Docker required.

Both modes share the same Python codebase, REST API surface, database schema, and converter contracts. The system uses SQLite 3 for all persistence.

> Reference doctrine: `CLAUDE.md`, `GEMINI.md`. Architectural detail in `docs/blueprint.md`.

---

## 1. System Overview

| Component | Role | Default Port / Location |
|---|---|---|
| FastAPI Backend (`app/batch_api`) | Orchestrates batches, manages hot folders, persists telemetry | `8000` |
| Streamlit GUI (`app/web/batch_gui`) | Manual job entry, hot-folder management, history review | `8503` |
| **SQLite Database** | Batch runs, summaries, image tracking, analytics | **`/data/pixelpivot.db`** (Docker) · **`./data/pixelpivot.db`** (native) |
| Sharp Daemon (`app/scripts/sharp_daemon.js`) | Persistent Node.js worker for pipelined Sharp conversions | `8765` (dynamic) |

There is no separate database service. The engine writes to a single SQLite file with WAL journaling, 5-second busy-timeout, and foreign keys enforced.

External binaries each converter depends on:

| Tool | Binary | Required for |
|---|---|---|
| `magick` | ImageMagick 7 (`magick.exe` on Windows) | `MagickConverter` (subprocess + native `mogrify` batching) |
| `ffmpeg` | FFmpeg | `FFmpegConverter` (subprocess, supervised by `app/core/ffmpeg/process.py`) and `FFmpegNvencConverter` |
| `pyvips` | libvips (`libvips-42.dll` on Windows) | `VipsConverter` (in-process) |
| `sharp` | Node.js 20+ runtime | `SharpConverter` (TCP daemon) |

---

## 2. Deployment Mode A — Docker (recommended for production)

The Docker image bundles Python, Node, ImageMagick, FFmpeg, and libvips. Compose wires API + GUI + CLI together — no separate DB container.

### 2.1 First start (WSL2 / Linux host)

```bash
bash scripts/wsl_start.sh
```

### 2.2 Verify

```bash
docker ps
# Expect: pixelpivot_batch_api, pixelpivot_batch_gui, pixelpivot_cli
curl -fsS http://localhost:8000/        # → {"message":"PixelPivot Batch Engine API is running"}
```

GUI: `http://localhost:8503` · API docs: `http://localhost:8000/docs`.

---

## 3. Deployment Mode B — Windows-native (no Docker)

### 3.1 Prerequisites

- Python 3.12+
- Node.js 20+
- ImageMagick 7
- FFmpeg
- libvips DLLs in `tools/vips/`

### 3.2 Install dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
npm install
```

### 3.3 Run the API

```powershell
uvicorn app.batch_api.main:app --host 0.0.0.0 --port 8000
```

---

## 4. Operation Modes

### 4.1 Hot Folder Monitoring
GUI → **HOT FOLDERS** tab. Watches a directory and fires batches after a 5s debounce.

### 4.2 Manual Batch
GUI → **EXECUTE** tab. Bulk conversion with telemetry capture.

---

## 5. Path Conventions
The API accepts absolute paths. Ensure Docker volumes are mapped correctly.

---

## 6. Maintenance & Troubleshooting

### 6.1 Telemetry inspection
All results land in `batch_summary` and `conversions` tables in the SQLite database.

### 6.2 SQLite housekeeping
WAL checkpointing is automatic, but can be forced via:
`PRAGMA wal_checkpoint(TRUNCATE);`

---

## 7. Air-Gap Bundling

Bundle the Docker stack:
```bash
bash scripts/export_airgap.sh
```
Deploy on the offline host via `docker load`.

---

## 8. Migration Notes
The system has been fully migrated from PostgreSQL to SQLite. All legacy modules (analytics, heuristics, repositories) now target the SQLite backend. The `psycopg` dependency and the optional `[legacy]` extra have been removed.

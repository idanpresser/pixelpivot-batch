# PixelPivot Batch Engine

A decoupled microservice for high-throughput image conversion.
A headless **FastAPI** backend orchestrates batch jobs against four
pluggable converter backends; a standalone **Streamlit** GUI communicates
with it exclusively via REST.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.14%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-pytest-green.svg)](tests/)

---

## Why

Most image-conversion pipelines pay a heavy per-file startup cost (process
spawn, codec init, DLL load). PixelPivot's converters override
`convert_batch()` to amortize that cost across many files using
tool-native batching paths:

| Backend | Strategy | Measured win |
|---|---|---|
| `MagickConverter` | quality-grouped `mogrify` | ~3× over per-file `magick` |
| `FFmpegConverter` | `image2` demuxer (hardlinked staging) for uniform-size sub-groups, multi-input/multi-output chunks otherwise | **~4.9× uniform, ~1.9× mixed** |
| `VipsConverter` | in-process `pyvips`, no subprocess overhead | linear-in-cores |
| `SharpConverter` | persistent socket connection to `sharp_daemon.js` | reuses Node + libvips heap across jobs |

A heuristic quality system picks the right encoder quality per image based
on a fitted log-linear curve `quality = a + b·log10(megapixels)`, so you
don't ship blurry thumbnails or oversized hero images.

---

## Architecture

```
┌─────────────────────────┐
│ Streamlit GUI :8503     │
│ (app/web/batch_gui)     │
└──────────┬──────────────┘
           │ HTTP (BATCH_API_URL)
           ▼
┌─────────────────────────┐
│ FastAPI Backend :8000   │
│ (app/batch_api)         │
│  ├─ BatchOrchestrator   │
│  └─ HotFolderManager    │
└──────────┬──────────────┘
           │ in-process
           ▼
┌─────────────────────────┐    ┌─────────────────────────┐
│ Converter Layer         │    │ SQLite (WAL mode)       │
│ (app/core/converters)   │◄──►│  - batch_runs           │
│  ├─ MagickConverter     │    │  - batch_summary        │
│  ├─ FFmpegConverter     │    │  - images / conversions │
│  ├─ VipsConverter       │    │  - metrics / priors     │
│  └─ SharpConverter      │    └─────────────────────────┘
└─────────────────────────┘
```

Full design notes live in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
Every constraint there is load-bearing — read it before refactoring.

---

## Installation

### Prerequisites

- **Python 3.14+**
- One or more of:
  - **FFmpeg** (with libaom-av1 for AVIF, libjxl for JXL)
  - **ImageMagick 7** (`magick` / `mogrify` on PATH)
  - **libvips** (Windows: DLL discoverable via `os.add_dll_directory`)
  - **Node.js 18+** for the Sharp daemon

The project is CPU-only; the NVENC backend was removed in v0.2.0 because
the target deployment server has no GPU. See `CHANGELOG.md` for context.

### From source

```bash
git clone https://github.com/idanpresser/pixelpivot-batch.git
cd pixelpivot-batch
python -m venv .venv
.venv\Scripts\Activate.ps1     # PowerShell
# source .venv/bin/activate    # bash / zsh
pip install -e ".[dev]"
```

### Air-gap Windows deployment

The project ships a Windows Sandbox configuration ([`PixelPivot.wsb`](PixelPivot.wsb))
and a layout that prefers vendored binaries under `bin/` and `vendor/`.
Those directories are not committed to git — they ship as a separate
bundle (USB / release artifact / internal mirror). The `BatchOrchestrator`
resolves to them automatically when present.

### Docker

```bash
docker-compose up --build
```

---

## Usage

> **New here?** The [User Guide](docs/USER_GUIDE.md) is a verified, step-by-step
> walkthrough: validate with the CLI, start the Sharp daemon and API server, run
> a batch, and read the results.

### Start the services locally

```powershell
# Optional: pick a database location (default: ./data/pixelpivot.db)
$env:PIXELPIVOT_DB_PATH = "./data/pixelpivot.db"

# API
uvicorn app.batch_api.main:app --host 0.0.0.0 --port 8000

# GUI (separate terminal)
$env:BATCH_API_URL = "http://localhost:8000/api/v1"
streamlit run -m app.web.batch_gui.main --server.port 8503
```

Open http://localhost:8503 for the GUI, or hit the API directly:

```bash
curl -X POST http://localhost:8000/api/v1/batch/start \
  -H "Content-Type: application/json" \
  -d '{
        "source_dir": "C:/images/in",
        "target_dir": "C:/images/out",
        "target_format": ["avif"],
        "tool": ["ffmpeg"],
        "category": ["general"]
      }'
# {"run_id": 1, "status": "queued"}
```

`target_format` and `tool` are **lists** — pass several to run the full matrix
(every image converted by every tool into every format). The call returns a
`run_id`; poll it for progress:

```bash
curl http://localhost:8000/api/v1/batch/status/1
```

### Hot-folder mode

Drop files into a watched directory; a batch fires automatically 5 seconds
after the last write:

```bash
curl -X POST http://localhost:8000/api/v1/hotfolder/register \
  -H "Content-Type: application/json" \
  -d '{
        "source_dir": "C:/inbox",
        "target_dir": "C:/out",
        "target_format": ["webp"],
        "tool": ["vips"],
        "category": ["general"]
      }'
# {"watcher_id": "...", "status": "active"}
```

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PIXELPIVOT_DB_PATH` | `./data/pixelpivot.db` | SQLite analytics DB |
| `BATCH_API_URL` | `http://localhost:8000/api/v1` | GUI → API address |
| `PIXELPIVOT_IMAGE2_ALLOW_LOSSY` | `0` | Opt in to `image2` fast path for AVIF/JXL |

Global timeouts, batch limits, fatal-error markers, and the
default-quality table live in [`app/core/config.py`](app/core/config.py).

---

## Quality scale

`quality` is **tool-and-format-native** — there is no single normalized
scale. Most paths use a 0–100 "higher is better" quality. Exceptions:

- `ffmpeg` AVIF: libaom-av1 **CRF** (0–63, *lower is better*, default ~28)
- JXL across all backends: 0–100 quality input mapped internally to a
  Butteraugli **distance** (0.0–15.0, lower is better) via
  `utils.quality_to_jxl_distance`

Use `config.default_quality_for(tool, format)` to look up the correct
fallback — never hard-code a number. Never cast interpolated quality to
`int` (the JXL mapping needs the float, and curve-fit quality is
fractional).

---

## Development

```bash
pytest                              # full suite
pytest tests/test_base_converter.py # focused
pytest -k "test_interpolator"      # by keyword
```

Issue tracking uses [beads](https://github.com/gastownhall/beads) (`bd`):

```bash
bd ready              # find available work
bd show <id>          # details
bd update <id> --claim
bd close <id>
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full development workflow.

---

## Project status

This is an early open-source release (v0.1.0). The internal API is
stable enough for the documented use cases but should be considered
pre-1.0 — minor breaking changes may land before v1.0 while we settle
the public surface.

## License

Apache License 2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).

## Code of Conduct

Participation in this project is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1).

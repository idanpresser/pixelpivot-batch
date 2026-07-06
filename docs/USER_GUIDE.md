# PixelPivot User Guide

A practical, end-to-end walkthrough of converting images with PixelPivot: the
conversion tools, the validation CLI, the API server, and the Sharp daemon.

Every command and payload below was verified against a live run on Windows.

---

## The four conversion tools

PixelPivot converts each image with whichever tool(s) you select. All four are
independent backends and can target the same formats (`webp`, `avif`, `jxl`).

| Tool | Engine | Process model | Needs |
|---|---|---|---|
| `magick` | ImageMagick | native `mogrify` batch, per-file `magick` fallback | `bin/magick/magick.exe` (or on `PATH`) |
| `ffmpeg` | FFmpeg / libaom-av1 | subprocess per file, native batch fast paths | `bin/ffmpeg/ffmpeg.exe` (or on `PATH`) |
| `vips` | libvips (pyvips) | in-process | `pyvips` + libvips DLLs |
| `sharp` | Node.js `sharp` | persistent socket daemon | Node + a **running Sharp daemon** (see below) |

A single batch can use any subset. When you pass several tools, PixelPivot runs
the full **matrix**: every image is converted by every tool into every format.
For example, 10 images × 4 tools × 1 format = 40 output files.

---

## Prerequisites

- **Python 3.14+.** The backend hard-fails on startup below this floor because
  the vendored native wheels are ABI-pinned. The air-gap bundle ships an
  embedded interpreter at `python-3.14.5-embed-amd64/`; use it if your system
  Python is older.
- Native binaries under `bin/` (`ffmpeg`, `magick`, `vips`) or on `PATH`.
- For the `sharp` tool: Node.js and the `sharp` npm module (`node_modules/`).

---

## Step 1 — Validate the environment (CLI)

The CLI (`app.cli`) is a **preflight validator**, not a converter. It checks the
source/target directories, the native binaries, libvips, and whether the Sharp
daemon is reachable. Run it before a batch to catch missing pieces early.

```powershell
$env:PYTHONPATH = "."
python -m app.cli --source ./e2e_src --target ./e2e_out

# Air-gap / embedded interpreter:
.\python-3.14.5-embed-amd64\python.exe -m app.cli --source ./e2e_src --target ./e2e_out
```

Expected output on a healthy install:

```
Checking source directory './e2e_src'... OK (readable)
Checking target directory './e2e_out'... OK (creatable/writable)
Checking FFmpeg... OK (found at ...\bin\ffmpeg\ffmpeg.exe)
Checking ImageMagick... OK (found at ...\bin\magick\magick.exe)
Checking pyvips/libvips... OK (libvips version 8.18.2)
Checking Sharp daemon (port 8765)... OK (connected)
 Validation Result: PASSED
```

`--dry-run` runs the same checks. A missing Sharp daemon is a **warning**, not a
failure — the other three tools still work without it.

---

## Step 2 — Start the Sharp daemon (only if using `sharp`)

The `sharp` tool talks to a long-lived Node process over a TCP socket on port
`8765`. Start it detached so it survives your shell:

```powershell
Start-Process node -ArgumentList "app/scripts/sharp_daemon.js","8765" -WindowStyle Hidden
```

Confirm it answers (raw JSON line protocol, not HTTP):

```powershell
$c = New-Object Net.Sockets.TcpClient("127.0.0.1", 8765)
$s = $c.GetStream(); $w = New-Object IO.StreamWriter($s); $r = New-Object IO.StreamReader($s)
$w.WriteLine('{"ping":true}'); $w.Flush()
$r.ReadLine()   # -> {"success":true,"pong":true}
$c.Close()
```

Skip this step entirely if you are not using the `sharp` tool.

---

## Step 3 — Start the API server

The API orchestrates all conversion. Point `PIXELPIVOT_DB_PATH` at your SQLite
analytics DB (optional; defaults to `./data/pixelpivot.db`).

### Configuring Security (Optional / Public Binds)
If you bind the server publicly (`--host 0.0.0.0`), you must configure shared secret environment variables to avoid startup safety aborts:
1. **Generate a secure token**:
   - **Python**: `python -c "import secrets; print(secrets.token_hex(32))"`
   - **OpenSSL**: `openssl rand -hex 32`
2. **Export the token before starting the server and any client**:
   - *Linux/macOS*: `export PIXELPIVOT_API_TOKEN="your_token"`
   - *Windows PowerShell*: `$env:PIXELPIVOT_API_TOKEN="your_token"`

```powershell
$env:PIXELPIVOT_DB_PATH = "./data/pixelpivot.db"
# If exposing publicly:
# $env:PIXELPIVOT_ALLOW_PUBLIC = "1"
# $env:PIXELPIVOT_API_TOKEN = "your_secure_token_here"
uvicorn app.batch_api.main:app --host 127.0.0.1 --port 8000

# Air-gap / embedded interpreter:
$env:PYTHONPATH = "."
# If exposing publicly:
# $env:PIXELPIVOT_ALLOW_PUBLIC = "1"
# $env:PIXELPIVOT_API_TOKEN = "your_secure_token_here"
.\python-3.14.5-embed-amd64\python.exe -m uvicorn app.batch_api.main:app --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/
# {"message":"PixelPivot Batch Engine API is running"}
```

---

## Step 4 — Run a batch conversion

Submit a batch with `POST /api/v1/batch/start`. The body lists **directories**,
one or more **formats**, and one or more **tools**:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/batch/start \
  -H "Content-Type: application/json" \
  -d '{
        "source_dir": "F:/DEV/pixelpivot_batch/e2e_src",
        "target_dir": "F:/DEV/pixelpivot_batch/e2e_out",
        "target_format": ["avif"],
        "tool": ["magick", "ffmpeg", "vips", "sharp"],
        "category": ["general"],
        "trigger_type": "manual"
      }'
# {"run_id":687,"status":"queued"}
```

| Field | Type | Notes |
|---|---|---|
| `source_dir` | string | Absolute path. Scanned for `.jpg .jpeg .png .webp .tiff .heic .heif .avif`. |
| `target_dir` | string | Absolute path. Created if missing. |
| `target_format` | string[] | One or more of `webp`, `avif`, `jxl`. |
| `tool` | string[] | One or more of `magick`, `ffmpeg`, `vips`, `sharp`. |
| `category` | string[] | Heuristic category, default `["general"]`. |
| `trigger_type` | string | Free-form label, default `"manual"`. |

> **Use absolute Windows paths** (`F:/DEV/...` or `F:\\DEV\\...`). A POSIX-style
> path such as `/f/DEV/...` from Git Bash resolves to a bogus `F:\f\DEV\...` on
> the server and the batch fails with "Source directory does not exist".

The work runs in the background; the call returns immediately with a `run_id`.

---

## Step 5 — Poll status

```bash
curl http://127.0.0.1:8000/api/v1/batch/status/687
```

```json
{
  "run_id": 687,
  "status": "completed",
  "total_images": 40,
  "summary": {
    "success_count": 40,
    "failure_count": 0,
    "duration_ms": 50847.9,
    "savings_pct": 58.48
  }
}
```

`status` moves `running` → `completed` (or `failed`). `total_images` is the
total **conversions** (images × tools × formats), not the input file count.
`summary` is populated only once the batch completes.

Errors for a run:

```bash
curl http://127.0.0.1:8000/api/v1/batch/687/errors
```

---

## Output naming

Each output file is suffixed with the tool that produced it, so a multi-tool
batch never collides:

```
highRes_0042_..._magick.avif
highRes_0042_..._ffmpeg.avif
highRes_0042_..._vips.avif
highRes_0042_..._sharp.avif
```

(With more than one `category`, the suffix becomes `_<category>_<tool>`.)

Confirm an output is real AVIF:

```bash
bin/ffmpeg/ffprobe.exe -v error -show_entries stream=codec_name -of csv=p=0 file_magick.avif
# av1
```

---

## Hot-folder mode (automatic batches)

Instead of submitting batches by hand, register a watched directory. A batch
fires automatically 5 seconds after the last file is written into it:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/hotfolder/register \
  -H "Content-Type: application/json" \
  -d '{
        "source_dir": "F:/inbox",
        "target_dir": "F:/out",
        "target_format": ["webp"],
        "tool": ["vips"],
        "category": ["general"]
      }'
# {"watcher_id":"...","status":"active"}

curl http://127.0.0.1:8000/api/v1/hotfolder/list
curl -X DELETE http://127.0.0.1:8000/api/v1/hotfolder/<watcher_id>
```

---

## Quality

You do not pass a quality number per batch — PixelPivot interpolates one per
image from a fitted heuristic curve, then clamps it to each encoder's native
range. The underlying scale is **tool-and-format-native** (e.g. ffmpeg AVIF uses
a libaom CRF where *lower is better*). See the
[Quality scale](../README.md#quality-scale) section of the README for details.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Python 3.14+ required; running 3.x` at startup | Interpreter below the floor | Use Python 3.14+ or the embedded `python-3.14.5-embed-amd64`. |
| `Source directory ...\f\... does not exist` | POSIX path from Git Bash | Pass an absolute Windows path. |
| Sharp cell fails / daemon "WARNING (could not connect)" | Daemon not running | Start `app/scripts/sharp_daemon.js` on port 8765 (Step 2). |
| Batch `failed`, `total_images: 0` | Source unreadable or empty | Check the path and that it contains supported image types. |
| `ModuleNotFoundError: app` (embedded interpreter) | `app` not on path | Set `PYTHONPATH=.` before launching. |

---

## See also

- [API_REFERENCE.md](API_REFERENCE.md) — complete endpoint-by-endpoint REST reference (all routes, schemas, status codes, out-of-scope)
- [README](../README.md) — install, configuration, quality scale
- [ARCHITECTURE.md](ARCHITECTURE.md) — service topology and converter internals
- [WINDOWS_SERVICE.md](WINDOWS_SERVICE.md) — run the API as a Windows service

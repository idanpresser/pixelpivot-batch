# PixelPivot TUI Guide

A terminal UI that supervises the PixelPivot backend: it launches the FastAPI
API as a child process, shows live batch telemetry, a tool-health board, batch
history, settings, and a merged log panel. It replaces the removed Streamlit GUI.

> **Maturity note.** The TUI shell, process supervision, and all backend
> control endpoints are implemented and tested. In-screen **interactive
> actions** (submit a batch, pause/stop, start the sharp daemon) are not yet
> bound to keys — the screens currently *display* state and show the intended
> key hints. Until those bindings land, drive batches via the REST API or the
> CLI (both fully working). See [Status](#status--whats-wired) below.

## Launch

```powershell
python -m app.cli tui
```

What happens:
1. Loads settings from `PROJ_ROOT/data/settings.toml` (defaults if absent).
2. Spawns the API child: `python -m app.cli serve --host <h> --port <p>`.
3. Polls `http://<h>:<p>/` until ready (15 s timeout; recent child logs printed if it never comes up).
4. Opens the full-screen UI. On exit the API child is always terminated.

The **sharp** node daemon is **not** auto-started (on-demand policy).

## Layout

```
+----------------------------------------------------------+
| submit | telemetry | history | tools | settings          |  tab bar
+----------------------------------------------------------+
|                                                          |
|   active screen body (Rich tables rendered to text)      |
|                                                          |
+----------------------------------------------------------+
| [API]  INFO  Starting Matrix Batch...                    |  log panel
| [API]  INFO  Processing Matrix Cell [ffmpeg]             |  (last 12 lines,
| [SHARP] listening :8765                                  |   merged child output)
+----------------------------------------------------------+
| API:running SHARP:? run:42  [Tab] switch  [q] quit       |  status bar
+----------------------------------------------------------+
```

## Keys

| Key | Action |
|-----|--------|
| `Tab` | Cycle to next tab (submit → telemetry → history → tools → settings → submit) |
| `q` | Quit (terminates the API child) |

## Screens

- **submit** — shows source/target paths, tool checkboxes (gated by enabled
  tools), format checkboxes (webp/avif/jxl), category. Display-only for now.
- **telemetry** — when a run is active, polls `GET /batch/{id}/progress` and
  renders a live table: cells done/total, current cell, ok/fail, cpu %, ram MB.
  Falls back to `/batch/status` once the run finalizes.
- **history** — table from `GET /batch/history` (run id, status, tool, format,
  savings %).
- **tools** — health/version board for magick, ffmpeg, vips, sharp (via
  `app.core.toolcheck`). Shows sharp daemon up/down.
- **settings** — points at `settings.toml`; edit the file directly for now.

## Settings (`PROJ_ROOT/data/settings.toml`)

Precedence (highest first): **file → environment → built-in defaults**. Edit the
file and restart the TUI to apply restart-scoped keys.

```toml
[api]
host = "127.0.0.1"
port = 8000
[paths]
db = "./data/pixelpivot.db"        # env: PIXELPIVOT_DB_PATH
sharp_port = 8765
[tools]
ffmpeg = ""                        # blank = use bundled binary or PATH
magick = ""
sharp_script = "app/scripts/sharp_daemon.js"
enabled = ["magick", "ffmpeg", "vips", "sharp"]
[security]
allowed_root = ""                  # env: PIXELPIVOT_ALLOWED_ROOT
[limits]
max_workers = 0                    # 0 = auto; env: PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS
[batch]
default_tool = "ffmpeg"
default_format = "avif"
default_quality = 90
```

**Live-apply** (no restart): `batch.default_tool/format/quality`,
`security.allowed_root`, `tools.enabled`.
**Restart-required**: `api.host/port`, `paths.db`, `paths.sharp_port`,
`tools.ffmpeg/magick`, `limits.max_workers`.

## CLI subcommands

```powershell
python -m app.cli serve   --host 0.0.0.0 --port 8000      # run API only
python -m app.cli convert --source <dir> --target <dir> --dry-run  # validate env/paths/binaries
python -m app.cli tui                                      # launch the TUI (supervises API)
```

## Batch control via REST (works today)

The control plane the TUI sits on is fully functional over REST. Base:
`http://<host>:<port>/api/v1`.

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/batch/start` | Start a batch (`source_dir`, `target_dir`, `target_format[]`, `tool[]`, `category[]`) → `{run_id}` |
| GET  | `/batch/status/{id}` | Status + summary (after completion) |
| GET  | `/batch/{id}/progress` | Live in-flight progress + cpu/ram sample (404 once finalized) |
| POST | `/batch/{id}/control` | `{"action":"pause"\|"resume"\|"stop"}` |
| POST | `/batch/{id}/restart` | Re-run with the stored config (category resets to `general`) |
| GET  | `/batch/{id}/errors` | Per-file errors |
| GET  | `/batch/history` | Recent runs |

**Control semantics:** pause/resume/stop act at **matrix-cell boundaries** — a
cell already inside `convert_batch` finishes before the signal takes effect.
Only one active batch is controlled at a time.

Example — start, watch, pause, stop:
```powershell
$body = @{ source_dir="C:\in"; target_dir="C:\out"; target_format=@("avif"); tool=@("ffmpeg"); category=@("general") } | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/batch/start -Body $body -ContentType application/json
Invoke-RestMethod http://127.0.0.1:8000/api/v1/batch/$($run.run_id)/progress
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/batch/$($run.run_id)/control -Body (@{action="pause"} | ConvertTo-Json) -ContentType application/json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/batch/$($run.run_id)/control -Body (@{action="stop"}  | ConvertTo-Json) -ContentType application/json
```

## Troubleshooting

- **"API did not become ready"** — the child failed to start; the last 5 child
  log lines print to stderr. Common causes: port already in use (change
  `[api] port`), Python below the required floor, missing native wheels.
- **Tools board shows sharp DOWN** — expected unless the sharp daemon is
  running; batches not using sharp are unaffected.
- **Nothing on telemetry** — no active run, or the run already finalized (check
  history). Progress is in-memory only and disappears at finalize.
- **Garbled box drawing** — use a UTF-8 / truecolor terminal (Windows Terminal).

## Status — what's wired

**Working now:** `cli tui` launch + API supervision, child-log capture into the
log panel, tab navigation, quit-with-cleanup, live telemetry display (polling),
tool-health board, history view, settings file load with precedence. All backend
endpoints (control/restart/progress) and CLI subcommands, fully tested.

**Not yet bound (display-only hints in screens):** in-TUI batch submit, in-TUI
pause/resume/stop/restart keys, in-TUI sharp daemon start/stop/restart, in-TUI
settings editing. Use the REST API / CLI meanwhile.

## Architecture

See `docs/superpowers/specs/2026-06-20-tui-control-plane-design.md` (design) and
`docs/superpowers/plans/2026-06-20-tui-control-plane.md` (implementation plan).
Key modules: `app/tui/app.py` (shell), `app/tui/launcher.py` (wiring),
`app/tui/supervisor.py` (child processes + log buffer),
`app/tui/api_client.py` (REST), `app/tui/settings.py`, `app/tui/state.py`,
`app/tui/render.py`, `app/tui/screens/`, `app/core/toolcheck.py` (probes),
`app/batch_api/run_control.py` (pause/cancel).

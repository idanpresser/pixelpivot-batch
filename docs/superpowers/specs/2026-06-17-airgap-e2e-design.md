# Air-Gap E2E Test & Deploy Bundle — Design Spec

**Date:** 2026-06-17  
**Branch:** fix/circuit-breaker-file-isolation → merge to main  
**Status:** Approved, pending implementation

---

## 1. Goal

Verify PixelPivot Batch Engine works correctly in a fully air-gapped Windows environment, including UNC network paths, all 4 converters producing AVIF output, CPU-only telemetry, and edge-case resilience. Deliver a self-contained deploy folder that runs from any path on Windows 10+.

---

## 2. Scope

| In scope | Out of scope |
|----------|-------------|
| magick, vips, sharp, ffmpeg converters | ffmpeg_nvenc (removed) |
| AVIF output format | webp, jxl, jpg, png output |
| UNC mapped (Z:\) + raw (\\server\share\) paths | Linux/macOS |
| CPU telemetry assertions | GPU telemetry |
| Edge case resilience (corrupt, huge, long paths) | Performance benchmarks |
| Windows 10 / 11 (any build) | Docker / WSL |

---

## 3. Prerequisites (Blockers to Fix First)

These open beads must be resolved before E2E tests can pass:

| Bead | Priority | Issue | Impact on tests |
|------|----------|-------|----------------|
| `pixelpivot_batch-rts` | P1 | Native binaries fail on paths >260 chars (no `\\?\` prefix) | Long-path edge case |
| `pixelpivot_batch-qim` | P2 | MASSIVE_IMAGE_THRESHOLD never rejects | Huge-image edge case |
| `pixelpivot_batch-9pz` | P2 | Corrupt detection only checks PIL header-open | Truncated-file edge case |

---

## 4. Artifacts

Three new scripts + one new deploy folder:

```
scripts/
  gen_e2e_dataset.ps1     ← populate Z:\pics (idempotent, run once)
  test_e2e_airgap.ps1     ← standalone E2E harness
  build_deploy.ps1        ← assemble deploy\ folder

deploy\                   ← deliverable for air-gapped machine
```

---

## 5. Dataset (`Z:\pics`)

**Source:** `Z:` = `\\ipsds5\Share` (1.5 TB network drive, already mapped).

`gen_e2e_dataset.ps1` is idempotent — skips files already present.

### 5.1 Real images (`Z:\pics\real\`)

Copy all 500 files from `image_samples\` (290 `.jpg` + 210 `.png`). No rename. Preserves original filenames including any with spaces or mixed case.

### 5.2 Edge cases (`Z:\pics\edge_cases\`)

| Subfolder | Contents | Expected behavior |
|-----------|----------|------------------|
| `truncated\` | 5 JPEGs — valid JFIF magic, content cut at 100 bytes | `failure_count > 0`, API alive |
| `empty\` | 1 zero-byte file with `.jpg` extension | `failure_count > 0` |
| `bad_header\` | 1 file with random bytes, `.jpg` extension | `failure_count > 0` |
| `huge\` | 1 PIL-generated 1×30000 PNG (extreme aspect ratio) | Rejected before conversion (MASSIVE_IMAGE_THRESHOLD) |
| `tiny\` | 1 PIL-generated 1×1 PNG | Success (no crash) |
| `paths\unicode\` | 1 JPEG with non-ASCII chars in filename | Success or graceful failure |
| `paths\spaces\` | 1 JPEG with spaces in filename | Success |
| `paths\deep\a\b\c\d\` | 1 JPEG, deeply nested | Success |
| `paths\longname\` | 1 JPEG with 252-char filename | Tests `\\?\` long-path fix |

### 5.3 UNC raw path

No extra files. Harness submits one job with `source_dir=\\ipsds5\Share\pics\real\` (raw UNC) to verify the API accepts it without the mapped drive letter.

---

## 6. E2E Harness (`scripts/test_e2e_airgap.ps1`)

### 6.1 Parameters

```powershell
param(
    [string]$DeployDir   = (Join-Path $PSScriptRoot '..\deploy'),
    [string]$PicsRoot    = 'Z:\pics',
    [string]$UncRoot     = '\\ipsds5\Share\pics',
    [string]$ApiUrl      = 'http://localhost:8000',
    [int]   $StartupSec  = 30,
    [int]   $BatchTimeout = 600
)
```

### 6.2 Phases

**Phase 1 — Preflight**
- `deploy\` exists and contains `Run-PixelPivot.ps1`
- `Z:\pics\real\` has ≥500 files
- Port 8000 is free (fail fast, don't stomp a running instance)

**Phase 2 — Launch API**
- Start `python.exe -m uvicorn app.batch_api.main:app --host 127.0.0.1 --port 8000` via embedded Python in `deploy\`
- Set `PATH` to `deploy\bin\ffmpeg`, `deploy\bin\magick`, `deploy\bin\vips`, `deploy\vendor\node`
- Poll `GET /` every 2s, max `$StartupSec`. Exit code 2 if timeout.

**Phase 3 — Launch Sharp Daemon**
- Start `node app\scripts\sharp_daemon.js` via `deploy\vendor\node\node.exe`
- Wait 3s (daemon logs "ready" to stdout; capture and verify)

**Phase 4 — Main Matrix**

For each tool in `magick`, `vips`, `sharp`, `ffmpeg`:
```
POST /api/v1/batches {
  source_dir: Z:\pics\real\
  target_dir: Z:\pics\out\{tool}\
  target_format: avif
  tool: {tool}
  category: general
  trigger_type: e2e
}
```
Poll `GET /api/v1/batches/{id}/status` every 5s until `completed` or `failed`, max `$BatchTimeout`.

Assert:
- `success_count == 500`
- `failure_count == 0`
- Response body contains telemetry: `cpu_percent` is numeric, `gpu_*` fields absent

**Phase 5 — UNC Raw Path**

Repeat Phase 4 for `magick` only, with `source_dir = \\ipsds5\Share\pics\real\`. Same assertions.

**Phase 6 — Edge Cases**

| Job | source_dir | Assertion |
|-----|-----------|-----------|
| Truncated | `Z:\pics\edge_cases\truncated\` | `failure_count > 0`, API still responding after |
| Empty file | `Z:\pics\edge_cases\empty\` | `failure_count > 0` |
| Bad header | `Z:\pics\edge_cases\bad_header\` | `failure_count > 0` |
| Huge image | `Z:\pics\edge_cases\huge\` | `success_count == 0`, error message contains "MASSIVE" or "rejected" |
| Tiny | `Z:\pics\edge_cases\tiny\` | `success_count == 1` |
| Path cases | `Z:\pics\edge_cases\paths\` | `failure_count == 0` (all path formats handled) |

All edge case jobs use `magick` + `avif`.

**Phase 7 — Teardown**
- Kill uvicorn PID and sharp daemon PID (tracked from Phase 2/3)
- Remove `Z:\pics\out\` recursively
- Write `deploy\last_run.txt` with full report

### 6.3 Report Format

```
PixelPivot E2E Report — 2026-06-17 21:34:01
=============================================
PHASE     TOOL    PATH_TYPE  SUCCESS  FAIL  TIME(s)  TELEMETRY
Matrix    magick  mapped     500/500  0     42       CPU:OK GPU:absent
Matrix    vips    mapped     500/500  0     18       CPU:OK GPU:absent
Matrix    sharp   mapped     500/500  0     61       CPU:OK GPU:absent
Matrix    ffmpeg  mapped     500/500  0     55       CPU:OK GPU:absent
UNC raw   magick  \\server   500/500  0     44       CPU:OK GPU:absent

EDGE CASES
  truncated   : PASS (failures detected, API alive)
  empty       : PASS
  bad_header  : PASS
  huge        : PASS (rejected before conversion)
  tiny        : PASS
  paths       : PASS

RESULT: PASS  (exit 0)
```

### 6.4 Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All assertions pass |
| 2 | API failed to start within timeout |
| 3 | Matrix conversion failures (success_count < 500 or failure_count > 0) |
| 4 | Batch did not complete within `$BatchTimeout` |
| 5 | Dataset missing or incomplete |

---

## 7. Deploy Folder

### 7.1 Structure

```
deploy\
  start.bat                        ← double-click entry (Win10+, any cwd)
  Run-PixelPivot.ps1               ← starts API + sharp, traps Ctrl+C
  app\                             ← Python source (no tests/, docs/, scripts/)
  python-3.14.5-embed-amd64\      ← embedded CPython
  vendor\
    wheels\                        ← .whl files (offline install source)
    site-packages\                 ← pre-installed packages (pip install --target)
    node\node.exe                  ← Node.js runtime
  bin\
    ffmpeg\ffmpeg.exe
    ffmpeg\ffprobe.exe
    magick\magick.exe
    vips\bin\vips.exe  (+ DLLs)
  data\                            ← SQLite DB created on first run
  MANIFEST.sha256                  ← integrity manifest
```

### 7.2 `start.bat`

```bat
@echo off
powershell -ExecutionPolicy Bypass -File "%~dp0Run-PixelPivot.ps1" %*
pause
```

`%~dp0` resolves to the `.bat`'s own directory regardless of cwd. `-ExecutionPolicy Bypass` avoids policy prompt. `pause` keeps the window open on error so operators can read the output.

### 7.3 `Run-PixelPivot.ps1`

Responsibilities:
- Discover own root via `$PSScriptRoot`
- Set `$env:PATH` to bundled binaries only (no host PATH leakage)
- Set `PIXELPIVOT_DB_PATH` to `$root\data\pixelpivot.db`
- Set `PYTHONPATH` to `$root\vendor\site-packages`
- Start uvicorn subprocess, capture PID
- Start sharp daemon subprocess, capture PID
- Print `API ready at http://localhost:8000` when health check passes
- `try/finally` block: kill both PIDs on `Ctrl+C` or script exit

### 7.4 `build_deploy.ps1`

Steps:
1. **Preflight** — refuse if any binary missing (ffmpeg, magick, vips, node, embedded Python)
2. **Clean** — wipe `deploy\` except `data\`
3. **Stage app** — `robocopy app\ deploy\app\ /E /XD tests __pycache__`
4. **Stage runtime** — copy `python-3.14.5-embed-amd64\`, `vendor\node\`, `bin\`
5. **Install wheels** — `python.exe -m pip install --no-index --find-links vendor\wheels --target deploy\vendor\site-packages .[all]` (reads deps from `pyproject.toml`; excludes `[dev]` extras)
6. **Write `.pth`** — `echo ..\vendor\site-packages > deploy\python-3.14.5-embed-amd64\pixelpivot.pth` (path relative to embedded Python root so it resolves correctly from any cwd)
7. **Copy launchers** — `start.bat`, `Run-PixelPivot.ps1`
8. **Manifest** — `scripts\manifest.ps1 -Mode create -Root deploy\`

---

## 8. Implementation Order

1. Fix `pixelpivot_batch-rts` (long-path `\\?\`)
2. Fix `pixelpivot_batch-qim` (MASSIVE_IMAGE rejection)
3. Fix `pixelpivot_batch-9pz` (corrupt detection)
4. Write `scripts/gen_e2e_dataset.ps1`
5. Write `scripts/test_e2e_airgap.ps1`
6. Write `Run-PixelPivot.ps1` + `start.bat`
7. Write `scripts/build_deploy.ps1`
8. Run full E2E harness, fix failures
9. Commit all, propose merge to main

---

## 9. Open Questions (Resolved)

| Question | Decision |
|----------|----------|
| Dataset source | Hybrid: 500 real from image_samples/ + synthetic edge cases |
| Output format | AVIF for all tools |
| E2E approach | Standalone PowerShell harness (no pytest) |
| Entry point | PS1 launcher + .bat shim |
| GPU telemetry | Assert absent (CPU-only path) |
| UNC testing | Mapped drive (Z:\) + raw UNC (\\ipsds5\Share\) |
